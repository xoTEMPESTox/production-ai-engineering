"""
Lesson 13: State Machines.

An agent workflow as explicit states and guarded transitions: moving
between phases is allowed only along declared edges, and only when the
edge's guard holds, otherwise a typed error says why. Parallel branches
return partial updates that are merged per key: keys with a reducer
(append, union, dict-merge) combine, plain keys may be written by only one
branch per step, so a branch can no longer silently overwrite data another
branch produced. Every merge records which branch changed which keys.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Optional, Protocol


class Phase(str, Enum):
    RECEIVED = "received"
    GATHERING = "gathering"
    ASSESSING = "assessing"
    DRAFTING = "drafting"
    AWAITING_REVIEW = "awaiting_review"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


@dataclass(frozen=True)
class TicketState:
    ticket: str
    phase: Phase = Phase.RECEIVED
    flags: tuple[str, ...] = ()            # reducer: union, order kept
    context: dict[str, Any] = field(default_factory=dict)  # reducer: merge keys, conflicting values refused
    risk: Optional[str] = None             # plain key: one writer per step
    draft: Optional[str] = None
    approved_by: Optional[str] = None
    log: tuple[str, ...] = ()              # reducer: append


class InvalidTransition(ValueError):
    pass


class GuardFailed(ValueError):
    pass


class MergeConflict(ValueError):
    pass


Guard = Callable[[TicketState], Optional[str]]  # None = allowed, else the reason it isn't

TRANSITIONS: dict[Phase, dict[Phase, Guard]] = {
    Phase.RECEIVED: {Phase.GATHERING: lambda s: None},
    Phase.GATHERING: {Phase.ASSESSING: lambda s: None if s.flags else "no findings gathered yet"},
    Phase.ASSESSING: {
        Phase.DRAFTING: lambda s: "risk not assessed" if s.risk is None
        else ("high risk must be escalated" if s.risk == "high" else None),
        Phase.ESCALATED: lambda s: None if s.risk == "high" else "only high-risk tickets escalate",
    },
    Phase.DRAFTING: {Phase.AWAITING_REVIEW: lambda s: None if s.draft else "no draft written"},
    Phase.AWAITING_REVIEW: {
        Phase.RESOLVED: lambda s: None if s.approved_by else "a person must approve the draft",
        Phase.DRAFTING: lambda s: None,  # reviewer sent it back
    },
}


def transition(state: TicketState, to: Phase) -> TicketState:
    allowed = TRANSITIONS.get(state.phase, {})
    if to not in allowed:
        names = ", ".join(p.value for p in allowed) or "none (terminal state)"
        raise InvalidTransition(f"{state.phase.value} -> {to.value} is not allowed; allowed: {names}")
    reason = allowed[to](state)
    if reason is not None:
        raise GuardFailed(f"{state.phase.value} -> {to.value} refused: {reason}")
    return replace(state, phase=to, log=state.log + (f"{state.phase.value} -> {to.value}",))


# --- Merging parallel branch updates ---

def _union(old: tuple, new: tuple) -> tuple:
    return old + tuple(x for x in new if x not in old)


def _merge_dict(old: dict, new: dict) -> dict:
    clashes = [k for k in new if k in old and old[k] != new[k]]
    if clashes:
        raise MergeConflict(f"context keys written with different values: {clashes}")
    return {**old, **new}


REDUCERS: dict[str, Callable[[Any, Any], Any]] = {"flags": _union, "context": _merge_dict,
                                                  "log": lambda old, new: old + tuple(new)}


def merge(state: TicketState, updates: dict[str, dict[str, Any]]) -> TicketState:
    """Apply every branch's partial update. Reducer keys combine; a plain key
    written by two branches in the same step is a conflict, never last-writer-wins."""
    writers: dict[str, str] = {}
    values = {name: getattr(state, name) for name in state.__dataclass_fields__}
    trace = []
    for branch, update in updates.items():
        for key, value in update.items():
            if key == "phase":
                raise MergeConflict(f"branch {branch!r} tried to set the phase; use transition()")
            if key in REDUCERS:
                values[key] = REDUCERS[key](values[key], tuple(value) if isinstance(value, list) else value)
            elif key in writers:
                raise MergeConflict(f"{key!r} written by both {writers[key]!r} and {branch!r}")
            else:
                writers[key] = branch
                values[key] = value
        trace.append(f"{branch} changed {sorted(update)}")
    values["log"] = values["log"] + tuple(trace)
    return TicketState(**values)


def naive_merge(state: TicketState, updates: dict[str, dict[str, Any]]) -> TicketState:
    """The bug this lesson exists for: each update replaces the key wholesale,
    so the last branch to finish silently erases what the others found."""
    values = {name: getattr(state, name) for name in state.__dataclass_fields__}
    for update in updates.values():
        values.update({k: tuple(v) if isinstance(v, list) else v for k, v in update.items()})
    return TicketState(**values)


# --- Branches and the model step ---

class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class ScriptedLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies, self.prompts = list(replies), []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._replies[len(self.prompts) - 1]


def billing_check(state: TicketState) -> dict[str, Any]:
    if "charged twice" in state.ticket:
        return {"flags": ["duplicate_charge"], "context": {"charges_this_month": 2}}
    return {}


def account_check(state: TicketState) -> dict[str, Any]:
    if "log in" in state.ticket:
        return {"flags": ["locked_account"], "context": {"failed_logins": 5}}
    return {}


def assess(llm: LLMClient, state: TicketState) -> dict[str, Any]:
    """The model sees exactly the flags the merge kept, so a dropped flag is a wrong answer."""
    reply = json.loads(llm.complete(f"Ticket: {state.ticket}\nFindings: {list(state.flags)}\nContext: {state.context}"))
    return {"risk": reply["risk"]}


if __name__ == "__main__":
    ticket = "I was charged twice this month and now I can't log in"
    start = transition(TicketState(ticket), Phase.GATHERING)
    branches = {"billing_check": billing_check(start), "account_check": account_check(start)}

    print("naive merge keeps:  ", naive_merge(start, branches).flags, " <- the billing finding is gone")
    state = merge(start, branches)
    print("reducer merge keeps:", state.flags, state.context)

    state = transition(state, Phase.ASSESSING)
    llm = ScriptedLLM(['{"risk": "medium"}'])
    state = merge(state, {"assess": assess(llm, state)})
    print("model saw:", llm.prompts[0].splitlines()[1])

    for bad in (Phase.RESOLVED, Phase.ESCALATED):
        try:
            transition(state, bad)
        except (InvalidTransition, GuardFailed) as exc:
            print(f"{type(exc).__name__}: {exc}")

    state = transition(state, Phase.DRAFTING)
    state = merge(state, {"writer": {"draft": "We've refunded the duplicate charge and unlocked your account."}})
    state = transition(state, Phase.AWAITING_REVIEW)
    state = transition(merge(state, {"reviewer": {"approved_by": "lead-sam"}}), Phase.RESOLVED)
    print("\nlog:")
    for line in state.log:
        print("  ", line)
