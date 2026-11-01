"""
Lesson 09: Evaluation & Self-Reflection.

Measure quality with a golden set and deterministic scorers first: Recall@k
and reciprocal rank for retrieval, required facts and groundedness for the
answer, completeness computed outside the model. Each failing case is
diagnosed as a retrieval miss or a generation miss. An LLM judge is used
only where no exact check exists: with an anchored rubric, pairwise
comparisons run in both orders to expose position bias, and agreement with
human labels (Cohen's kappa) measured before its scores are trusted. A
model's review of its own output is reported, but never decides the result.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class ScriptedLLM:
    """Deterministic stand-in: replies in order (cycling), records every prompt."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._replies[(len(self.prompts) - 1) % len(self._replies)]


# --- Golden set and system outputs ---

@dataclass(frozen=True)
class GoldenCase:
    id: str
    question: str
    relevant_ids: frozenset[str]             # chunks that answer it (retrieval labels)
    required_facts: tuple[str, ...] = ()     # phrases a correct answer must contain
    expected_items: frozenset[str] = frozenset()  # for "list every ..." questions


@dataclass(frozen=True)
class SystemOutput:
    retrieved: tuple[str, ...]  # ranked chunk ids
    answer: str
    citations: tuple[str, ...]
    items: tuple[str, ...] = ()


# --- Deterministic scorers ---

def recall_at_k(retrieved: tuple[str, ...], relevant: frozenset[str], k: int) -> float:
    return len(set(retrieved[:k]) & relevant) / len(relevant) if relevant else 1.0


def precision_at_k(retrieved: tuple[str, ...], relevant: frozenset[str], k: int) -> float:
    top = retrieved[:k]
    return len(set(top) & relevant) / len(top) if top else 0.0


def reciprocal_rank(retrieved: tuple[str, ...], relevant: frozenset[str]) -> float:
    return next((1 / rank for rank, doc in enumerate(retrieved, 1) if doc in relevant), 0.0)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def facts_found(answer: str, facts: tuple[str, ...]) -> float:
    return sum(_norm(f) in _norm(answer) for f in facts) / len(facts) if facts else 1.0


@dataclass(frozen=True)
class ScoreCard:
    case_id: str
    recall: float
    reciprocal_rank: float
    facts: float
    grounded: bool                 # cites something, and only what was retrieved
    missing_items: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.facts == 1.0 and self.grounded and not self.missing_items

    @property
    def diagnosis(self) -> str:
        """Which component to fix. Retrieval comes first: the model can't use what it never saw."""
        if self.passed:
            return "pass"
        if self.recall == 0.0:
            return "retrieval_miss"
        if not self.grounded:
            return "ungrounded"
        if self.missing_items:
            return "incomplete"
        return "generation_miss"


def score_case(case: GoldenCase, out: SystemOutput, k: int = 3) -> ScoreCard:
    grounded = bool(out.citations) and set(out.citations) <= set(out.retrieved)
    missing = tuple(sorted(case.expected_items - set(out.items)))  # computed outside the model
    return ScoreCard(case.id, recall_at_k(out.retrieved, case.relevant_ids, k),
                     reciprocal_rank(out.retrieved, case.relevant_ids),
                     facts_found(out.answer, case.required_facts), grounded, missing)


# --- Self-review: reported, never decisive ---

def self_review(llm: LLMClient, case: GoldenCase, out: SystemOutput) -> dict:
    """Ask the same model whether its answer is complete. Useful as a signal,
    unreliable as a verdict: a model that skipped an item usually can't see the gap."""
    reply = llm.complete(f"Question: {case.question}\nYour answer: {out.answer}\nItems: {list(out.items)}\n"
                         'Is the answer complete? Reply as JSON: {"complete": bool, "count": int}')
    return json.loads(reply)


# --- LLM-as-judge, used carefully ---

RUBRIC = """Score how well the answer serves a customer, from 1 to 5.
5: correct, complete, and states the conditions that apply (deadlines, eligibility).
4: correct, but misses a condition the customer needs.
3: partly correct, or correct but unclear.
2: mostly wrong, or answers a different question.
1: wrong in a way that could cost the customer money or time.
Reply as JSON: {"score": 1-5, "reason": "one sentence"}"""


class JudgeVerdict(BaseModel):
    score: int = Field(ge=1, le=5)
    reason: str


def judge_pointwise(llm: LLMClient, question: str, answer: str) -> JudgeVerdict:
    return JudgeVerdict.model_validate_json(llm.complete(f"{RUBRIC}\n\nQuestion: {question}\nAnswer: {answer}"))


def judge_pairwise(llm: LLMClient, question: str, a: str, b: str) -> Literal["a", "b", "inconsistent"]:
    """Ask twice with the order swapped. A judge that prefers whichever answer
    comes first gives opposite verdicts, and the comparison is discarded."""
    def ask(first: str, second: str) -> str:
        reply = llm.complete(f"Question: {question}\nAnswer 1: {first}\nAnswer 2: {second}\n"
                             'Which answer is better? Reply as JSON: {"better": 1 or 2}')
        return {1: first, 2: second}[json.loads(reply)["better"]]

    winners = {ask(a, b), ask(b, a)}
    if winners == {a}:
        return "a"
    if winners == {b}:
        return "b"
    return "inconsistent"


def cohens_kappa(judge: list[bool], human: list[bool]) -> float:
    """Agreement beyond chance. Raw agreement flatters a judge that always says 'pass'."""
    n = len(judge)
    observed = sum(j == h for j, h in zip(judge, human)) / n
    p_judge, p_human = sum(judge) / n, sum(human) / n
    expected = p_judge * p_human + (1 - p_judge) * (1 - p_human)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


@dataclass(frozen=True)
class Calibration:
    agreement: float
    kappa: float
    trusted: bool


def calibrate(judge: list[bool], human: list[bool], min_kappa: float = 0.6) -> Calibration:
    kappa = cohens_kappa(judge, human)
    agreement = sum(j == h for j, h in zip(judge, human)) / len(judge)
    return Calibration(round(agreement, 3), round(kappa, 3), kappa >= min_kappa)


GOLDEN = [
    GoldenCase("g1", "How long do I have to get a refund on an annual plan?",
               frozenset({"billing-04"}), required_facts=("30 days",)),
    GoldenCase("g2", "What does error E4012 mean?", frozenset({"billing-11"}), required_facts=("declined",)),
    GoldenCase("g3", "List every overdue invoice for acct-17.", frozenset({"ledger-17"}),
               expected_items=frozenset({"inv-1041", "inv-1042", "inv-1044", "inv-1047", "inv-1050"})),
]
OUTPUTS = {
    "g1": SystemOutput(("billing-04", "billing-07"), "Within 30 days of purchase [billing-04].", ("billing-04",)),
    "g2": SystemOutput(("billing-07", "billing-02", "billing-09"), "It is a network error.", ("billing-02",)),
    "g3": SystemOutput(("ledger-17",), "Four invoices are overdue [ledger-17].", ("ledger-17",),
                       items=("inv-1041", "inv-1042", "inv-1047", "inv-1050")),
}

if __name__ == "__main__":
    print("case  R@3   RR    facts  grounded  diagnosis")
    for case in GOLDEN:
        card = score_case(case, OUTPUTS[case.id])
        print(f"{card.case_id:<5} {card.recall:<5} {card.reciprocal_rank:<5} {card.facts:<6} "
              f"{str(card.grounded):<9} {card.diagnosis} {list(card.missing_items) or ''}")

    review = self_review(ScriptedLLM(['{"complete": true, "count": 4}']), GOLDEN[2], OUTPUTS["g3"])
    print(f"\ng3 self-review: {review} -> its count matches its own partial list; "
          f"the external check still finds {list(score_case(GOLDEN[2], OUTPUTS['g3']).missing_items)}")

    biased = ScriptedLLM(['{"better": 1}'])  # always prefers whichever answer it reads first
    verdict = judge_pairwise(biased, GOLDEN[0].question, "Within 30 days.", "Within 30 days of purchase, in full.")
    print(f"pairwise with a position-biased judge: {verdict}")

    human = [True] * 9 + [False]
    print("judge that always says pass:", calibrate([True] * 10, human))
