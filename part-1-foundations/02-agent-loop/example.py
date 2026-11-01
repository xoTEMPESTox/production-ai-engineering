"""
Lesson 02: The Agent Loop.

A bounded think -> act -> observe loop. The model proposes the next action;
the harness decides whether the run continues. Every step is checked against
step, token and time budgets, identical repeated tool calls are detected,
tool errors go back to the model as observations, and every step is recorded
in a typed event log.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Literal, Optional, Protocol

Tool = Callable[..., str]


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Same tool + same arguments -> same fingerprint (argument order ignored)."""
        return f"{self.name}:{json.dumps(self.arguments, sort_keys=True)}"


@dataclass(frozen=True)
class ModelTurn:
    """One model response: a thought plus either a tool call or a final answer."""
    thought: str = ""
    tool_call: Optional[ToolCall] = None
    final_answer: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    def complete(self, messages: list[Message], tools: list[str]) -> ModelTurn: ...


class ScriptedLLM:
    """Deterministic stand-in for a real model: replays a fixed list of turns.

    It records every message list it was shown, so tests can check exactly
    what the model saw (for example, that a tool error reached it).
    """

    def __init__(self, turns: list[ModelTurn]) -> None:
        self._turns = list(turns)
        self.seen: list[list[Message]] = []

    def complete(self, messages: list[Message], tools: list[str]) -> ModelTurn:
        self.seen.append(list(messages))
        if len(self.seen) > len(self._turns):
            raise RuntimeError("ScriptedLLM ran out of turns")
        return self._turns[len(self.seen) - 1]


@dataclass(frozen=True)
class Budget:
    max_steps: int = 8            # model calls per run
    max_tokens: int = 20_000      # input + output tokens across the whole run
    max_seconds: float = 60.0     # wall-clock time for the whole run
    max_identical_calls: int = 2  # the same tool + arguments may run this many times


class StopReason(str, Enum):
    FINAL_ANSWER = "final_answer"
    MAX_STEPS = "max_steps"
    TOKEN_BUDGET = "token_budget"
    TIME_BUDGET = "time_budget"
    LOOP_DETECTED = "loop_detected"
    MODEL_ERROR = "model_error"


class EventKind(str, Enum):
    MODEL_TURN = "model_turn"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    CORRECTION = "correction"    # the turn had neither a tool call nor an answer
    MODEL_ERROR = "model_error"
    STOPPED = "stopped"


@dataclass(frozen=True)
class Event:
    step: int
    kind: EventKind
    detail: str
    tokens_used: int  # cumulative for the run
    elapsed_s: float

    def to_json(self) -> str:
        return json.dumps({**asdict(self), "kind": self.kind.value})


@dataclass(frozen=True)
class RunResult:
    answer: Optional[str]
    stop_reason: StopReason
    steps: int
    tokens_used: int
    events: tuple[Event, ...]


SYSTEM_PROMPT = "You are a support agent. Use tools to look things up. Cite document IDs."
CORRECTION = "Your last reply had no tool call and no final answer. Reply with exactly one of them."


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        tools: dict[str, Tool],
        budget: Budget = Budget(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._llm = llm
        self._tools = dict(tools)
        self._budget = budget
        self._clock = clock

    def run(self, task: str) -> RunResult:
        messages = [Message("system", SYSTEM_PROMPT), Message("user", task)]
        events: list[Event] = []
        call_counts: dict[str, int] = {}
        tokens = 0
        started = self._clock()

        def record(step: int, kind: EventKind, detail: str) -> None:
            elapsed = round(self._clock() - started, 3)
            events.append(Event(step, kind, detail, tokens, elapsed))

        def stop(step: int, reason: StopReason, answer: Optional[str] = None) -> RunResult:
            record(step, EventKind.STOPPED, reason.value)
            return RunResult(answer, reason, step, tokens, tuple(events))

        for step in range(1, self._budget.max_steps + 1):
            # Budgets are checked by the harness every step, never left to the model.
            if self._clock() - started > self._budget.max_seconds:
                return stop(step - 1, StopReason.TIME_BUDGET)

            try:
                turn = self._llm.complete(messages, sorted(self._tools))
            except Exception as exc:  # provider outage, malformed response, ...
                record(step, EventKind.MODEL_ERROR, f"{type(exc).__name__}: {exc}")
                return stop(step, StopReason.MODEL_ERROR)

            tokens += turn.input_tokens + turn.output_tokens
            record(step, EventKind.MODEL_TURN, turn.thought or "(no thought)")

            # An answer that arrives on the step that crosses the token budget is
            # still returned: those tokens are already spent.
            if turn.final_answer is not None:
                messages.append(Message("assistant", turn.final_answer))
                return stop(step, StopReason.FINAL_ANSWER, turn.final_answer)
            if tokens > self._budget.max_tokens:
                return stop(step, StopReason.TOKEN_BUDGET)

            if turn.tool_call is None:
                messages.append(Message("user", CORRECTION))
                record(step, EventKind.CORRECTION, CORRECTION)
                continue

            call = turn.tool_call
            key = call.fingerprint()
            if call_counts.get(key, 0) >= self._budget.max_identical_calls:
                return stop(step, StopReason.LOOP_DETECTED)
            call_counts[key] = call_counts.get(key, 0) + 1

            observation, ok = self._execute(call)
            messages.append(Message("assistant", f"call {key}"))
            messages.append(Message("tool", observation))
            kind = EventKind.TOOL_RESULT if ok else EventKind.TOOL_ERROR
            record(step, kind, f"{call.name} -> {' '.join(observation.split())[:100]}")

        return stop(self._budget.max_steps, StopReason.MAX_STEPS)

    def _execute(self, call: ToolCall) -> tuple[str, bool]:
        """Run a tool. Failures become observations the model can act on."""
        tool = self._tools.get(call.name)
        if tool is None:
            available = ", ".join(sorted(self._tools))
            return f"ERROR: unknown tool '{call.name}'. Available tools: {available}", False
        try:
            return str(tool(**call.arguments)), True
        except Exception as exc:  # includes TypeError for wrong arguments
            return f"ERROR: {type(exc).__name__}: {exc}", False


# --- Demo tools: a tiny help-centre search (agentic RAG) and an account lookup ---

DOCS = {
    "billing-04": "Annual plans can be refunded in full within 30 days of purchase.",
    "billing-07": "Monthly plans are not refundable; cancel any time before renewal.",
    "account-02": "Workspace owners can transfer ownership from Settings > Members.",
}
ACCOUNTS = {"acct-17": "annual plan, purchased 2026-10-20"}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def search_docs(query: str) -> str:
    """Keyword match over the help centre (lesson 05 replaces this with real retrieval)."""
    hits = [f"[{doc_id}] {text}" for doc_id, text in DOCS.items() if _words(query) & _words(text)]
    return "\n".join(hits) or "no matching documents"


def get_plan(account_id: str) -> str:
    if account_id not in ACCOUNTS:
        raise KeyError(f"unknown account {account_id}")
    return ACCOUNTS[account_id]


def _print_run(title: str, result: RunResult) -> None:
    print(f"\n== {title}: stopped by {result.stop_reason.value} after {result.steps} steps")
    for event in result.events:
        print(f"  step {event.step} {event.kind.value:<12} {event.detail}")
    print(f"  answer: {result.answer}")


if __name__ == "__main__":
    tools: dict[str, Tool] = {"search_docs": search_docs, "get_plan": get_plan}

    recovering = ScriptedLLM([
        ModelTurn("Look up the plan.", ToolCall("get_plan", {"account_id": "acct-71"}), input_tokens=120),
        ModelTurn("Typo in the ID; retry.", ToolCall("get_plan", {"account_id": "acct-17"}), input_tokens=160),
        ModelTurn("Find the refund policy.", ToolCall("search_docs", {"query": "refund annual plans"}), input_tokens=210),
        ModelTurn("Purchased 12 days ago; inside the window.",
                  final_answer="Yes. acct-17 is on an annual plan bought 2026-10-20, inside the "
                               "30-day full-refund window [billing-04].", input_tokens=300),
    ])
    _print_run("Tool error recovered", AgentLoop(recovering, tools).run("Can acct-17 get a refund?"))

    stuck = ScriptedLLM([
        ModelTurn("Search again.", ToolCall("search_docs", {"query": "enterprise discount"})) for _ in range(5)
    ])
    _print_run("Repeated call", AgentLoop(stuck, tools).run("Is there an enterprise discount?"))
