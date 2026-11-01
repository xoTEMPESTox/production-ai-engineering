"""
Lesson 03: Prompting & Structured Output.

Prompts are versioned artifacts rendered in a cache-friendly order, and the
model's output is a contract: parsed into a Pydantic schema, checked against
the sources it was given, repaired once with the exact errors, and failed
closed if it is still invalid.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


class LLMClient(Protocol):
    def complete(self, messages: list[Message]) -> str: ...


class ScriptedLLM:
    """Deterministic stand-in for a real model: returns fixed replies in order
    and records every message list it was shown."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.seen: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.seen.append(list(messages))
        return self._replies[len(self.seen) - 1]


# --- The output contract ---

class GroundedAnswer(BaseModel):
    """What the model must return. found=false is the honest way to say
    'the sources don't cover this'."""
    model_config = ConfigDict(extra="forbid")  # a renamed field fails loudly

    found: bool
    answer: str = Field(min_length=1, max_length=600)
    citations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _citations_match_found(self) -> "GroundedAnswer":
        if self.found and not self.citations:
            raise ValueError("found=true requires at least one citation")
        if not self.found and self.citations:
            raise ValueError("found=false must not cite sources")
        return self


# --- Versioned prompts ---

@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    system: str  # instructions + output schema: identical for every request

    @property
    def fingerprint(self) -> str:
        """Content hash, so an edit that forgot to bump the version is caught."""
        return hashlib.sha256(self.system.encode()).hexdigest()[:12]

    @property
    def prompt_id(self) -> str:
        return f"{self.name}@{self.version}#{self.fingerprint}"


class PromptRegistry:
    """A version, once registered, is immutable: new text needs a new version."""

    def __init__(self) -> None:
        self._templates: dict[tuple[str, str], PromptTemplate] = {}

    def register(self, template: PromptTemplate) -> None:
        existing = self._templates.get((template.name, template.version))
        if existing is not None and existing.fingerprint != template.fingerprint:
            raise ValueError(
                f"{template.name}@{template.version} is already registered with different "
                "text; bump the version instead of editing it in place"
            )
        self._templates[(template.name, template.version)] = template

    def get(self, name: str, version: str) -> PromptTemplate:
        return self._templates[(name, version)]


GROUNDED_ANSWER_V2 = PromptTemplate(
    name="grounded_answer",
    version="v2",
    system=(
        "You answer questions using only the sources the user provides.\n"
        "- Use only facts stated in the sources. If they do not answer the question, "
        'set "found" to false and say so in one sentence.\n'
        '- Put the id of every source you used in "citations". Never cite an id that '
        "was not provided.\n"
        "- Reply with one JSON object and nothing else, matching this JSON Schema:\n"
        + json.dumps(GroundedAnswer.model_json_schema(), sort_keys=True)
    ),
)

REPAIR = "Your reply was rejected:\n{errors}\nReply again with only the corrected JSON object."


@dataclass(frozen=True)
class Source:
    id: str
    text: str


def render(template: PromptTemplate, sources: list[Source], question: str) -> list[Message]:
    """Most stable content first, so a provider's prefix cache can reuse it:
    system prompt (same for every request) -> sources (same for follow-up
    questions on the same documents) -> the question (changes every time)."""
    source_block = "\n".join(f"[{s.id}] {s.text}" for s in sources)
    return [
        Message("system", template.system),
        Message("user", f"Sources:\n{source_block}\n\nQuestion: {question}"),
    ]


# --- Validate -> one repair -> fail closed ---

def parse_output(raw: str, allowed_ids: set[str]) -> tuple[Optional[GroundedAnswer], list[str]]:
    """Schema check first, then the check the schema can't express: every
    citation must be one of the sources this request actually provided."""
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        answer = GroundedAnswer.model_validate_json(text)
    except ValidationError as exc:
        return None, [f"{'.'.join(map(str, e['loc'])) or 'output'}: {e['msg']}" for e in exc.errors()]
    unknown = [c for c in answer.citations if c not in allowed_ids]
    if unknown:
        return None, [f"citations: unknown source id(s) {unknown}; cite only {sorted(allowed_ids)}"]
    return answer, []


@dataclass(frozen=True)
class AnswerResult:
    status: Literal["ok", "repaired", "failed"]
    answer: Optional[GroundedAnswer]
    errors: tuple[str, ...]  # every validation error seen, in order
    prompt_id: str           # ties the output to the exact prompt text
    model_calls: int


def answer_question(
    llm: LLMClient, template: PromptTemplate, sources: list[Source], question: str
) -> AnswerResult:
    messages = render(template, sources, question)
    allowed = {s.id for s in sources}

    first = llm.complete(messages)
    answer, errors = parse_output(first, allowed)
    if answer is not None:
        return AnswerResult("ok", answer, (), template.prompt_id, 1)

    # One repair attempt: show the model its own reply and the exact errors.
    repair = messages + [
        Message("assistant", first),
        Message("user", REPAIR.format(errors="\n".join(f"- {e}" for e in errors))),
    ]
    second = llm.complete(repair)
    answer, second_errors = parse_output(second, allowed)
    if answer is not None:
        return AnswerResult("repaired", answer, tuple(errors), template.prompt_id, 2)

    # Fail closed: no answer at all is better than an unverified one.
    return AnswerResult("failed", None, tuple(errors + second_errors), template.prompt_id, 2)


if __name__ == "__main__":
    handbook = [
        Source("pto-01", "Full-time employees accrue 1.5 days of paid leave per month."),
        Source("pto-03", "Up to 10 unused leave days carry over to the next calendar year."),
        Source("travel-02", "Economy class is required for flights shorter than 6 hours."),
    ]
    question = "How many unused leave days carry over?"
    cases = {
        "valid first time": [
            '{"found": true, "answer": "Up to 10 days carry over.", "citations": ["pto-03"]}',
        ],
        "invented citation, repaired": [
            '```json\n{"found": true, "answer": "Up to 10 days.", "citations": ["pto-09"]}\n```',
            '{"found": true, "answer": "Up to 10 days carry over.", "citations": ["pto-03"]}',
        ],
        "prose twice, fail closed": [
            "Sure! Up to 10 days carry over.",
            "Up to 10 days carry over (pto-03).",
        ],
    }
    for title, replies in cases.items():
        result = answer_question(ScriptedLLM(replies), GROUNDED_ANSWER_V2, handbook, question)
        shown = result.answer.answer if result.answer else "Couldn't verify an answer; routed to a person."
        print(f"\n== {title}: {result.status} after {result.model_calls} call(s) [{result.prompt_id}]")
        for error in result.errors:
            print(f"  rejected: {error}")
        print(f"  user sees: {shown}")
