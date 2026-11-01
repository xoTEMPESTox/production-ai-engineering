"""
Lesson 06: Planning & Task Decomposition.

Plan-and-execute with the plan as data. The planner model returns a todo
list as JSON; the harness validates it (known tools, dependencies that
exist and come first, a size limit), executes the steps in order, and keeps
progress in the plan itself. When a step fails, the planner sees the
current checklist and re-plans only the remaining work. Finished steps are
never redone, and the number of re-plans is capped.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Tool = Callable[..., str]
PLACEHOLDER = re.compile(r"\{(s\d+)\}")  # "{s1}" in an argument = the result of step s1


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class ScriptedLLM:
    """Deterministic stand-in for a real model: replies in order, records prompts."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        return self._replies[len(self.prompts) - 1]


# --- The plan the model writes (validated) ---

class StepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^s\d+$")
    description: str = Field(min_length=3)
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class PlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[StepSpec] = Field(min_length=1, max_length=8)


class PlanError(ValueError):
    pass


# --- The plan the harness tracks ---

class Status(str, Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SUPERSEDED = "superseded"  # dropped by a re-plan before it ran


MARKS = {Status.PENDING: "[ ]", Status.DONE: "[x]", Status.FAILED: "[!]", Status.SUPERSEDED: "[-]"}


@dataclass(frozen=True)
class Step:
    spec: StepSpec
    version: int  # which plan version added it
    status: Status = Status.PENDING
    result: str = ""


@dataclass
class Plan:
    goal: str
    steps: list[Step] = field(default_factory=list)
    version: int = 0

    def results(self) -> dict[str, str]:
        return {s.spec.id: s.result for s in self.steps if s.status is Status.DONE}

    def next_ready(self) -> Optional[Step]:
        done = self.results()
        for step in self.steps:
            if step.status is Status.PENDING and all(d in done for d in step.spec.depends_on):
                return step
        return None

    def mark(self, step_id: str, status: Status, result: str) -> None:
        self.steps = [replace(s, status=status, result=result) if s.spec.id == step_id else s
                      for s in self.steps]

    def progress(self) -> dict[str, int]:
        counts = {status.value: 0 for status in Status}
        for step in self.steps:
            counts[step.status.value] += 1
        return counts

    def render(self) -> str:
        """The checklist the planner sees when it re-plans (and a human sees in a UI)."""
        p = self.progress()
        summary = f"{p['done']} done, {p['failed']} failed, {p['pending']} pending"
        lines = [f"Goal: {self.goal}  (plan v{self.version}: {summary})"]
        for s in self.steps:
            outcome = f" -> {s.result}" if s.result else ""
            lines.append(f"{MARKS[s.status]} {s.spec.id} {s.spec.tool}: {s.spec.description}{outcome}")
        return "\n".join(lines)


def validate_plan(spec: PlanSpec, tools: dict[str, Tool], plan: Plan) -> None:
    """Checks a schema can't express. Raises PlanError listing every problem."""
    used = {s.spec.id for s in plan.steps}
    available = set(plan.results())  # earlier steps' results that new steps may depend on
    problems = []
    for step in spec.steps:
        if step.id in used:
            problems.append(f"{step.id}: id already used; new steps need new ids")
        if step.tool not in tools:
            problems.append(f"{step.id}: unknown tool '{step.tool}'")
        for dep in step.depends_on:
            if dep not in available:
                problems.append(f"{step.id}: depends on '{dep}', which is not a finished or earlier step")
        referenced = {m for v in step.args.values() if isinstance(v, str) for m in PLACEHOLDER.findall(v)}
        if not referenced <= set(step.depends_on):
            problems.append(f"{step.id}: uses {sorted(referenced - set(step.depends_on))} without depending on it")
        used.add(step.id)
        available.add(step.id)
    if problems:
        raise PlanError("; ".join(problems))


PLANNER_SYSTEM = (
    "You plan work for a support agent. Break the goal into at most 8 steps, each one call to a "
    "listed tool. Reply with JSON only: "
    '{"steps": [{"id": "s1", "description": "...", "tool": "...", "args": {...}, "depends_on": []}]}. '
    "To pass a step's result into a later step, write {s1} in the argument and list s1 in "
    "depends_on. When re-planning, finished steps stay done: plan only the remaining work, with new ids."
)


@dataclass(frozen=True)
class RunReport:
    status: Literal["done", "failed"]
    plan: Plan
    replans: int
    reason: str = ""


class PlanExecutor:
    def __init__(self, llm: LLMClient, tools: dict[str, Tool], max_replans: int = 2) -> None:
        self._llm = llm
        self._tools = dict(tools)
        self._max_replans = max_replans

    def _plan(self, plan: Plan, failure: str = "") -> None:
        user = f"Tools: {', '.join(sorted(self._tools))}\nGoal: {plan.goal}"
        if failure:
            user += f"\n\nCurrent plan:\n{plan.render()}\n\n{failure}\nPlan the remaining work."
        try:
            spec = PlanSpec.model_validate_json(self._llm.complete(PLANNER_SYSTEM, user))
        except ValidationError as exc:
            raise PlanError(f"plan is not valid JSON for the schema: {exc.error_count()} error(s)") from exc
        validate_plan(spec, self._tools, plan)
        plan.version += 1
        plan.steps += [Step(s, plan.version) for s in spec.steps]

    def run(self, goal: str) -> RunReport:
        plan, replans = Plan(goal), 0
        try:
            self._plan(plan)
            while (step := plan.next_ready()) is not None:
                args = {k: PLACEHOLDER.sub(lambda m: plan.results()[m.group(1)], v) if isinstance(v, str) else v
                        for k, v in step.spec.args.items()}
                try:
                    plan.mark(step.spec.id, Status.DONE, str(self._tools[step.spec.tool](**args)))
                except Exception as exc:  # a step failed: record it, then re-plan the rest
                    error = f"{type(exc).__name__}: {exc}"
                    plan.mark(step.spec.id, Status.FAILED, error)
                    if replans == self._max_replans:
                        return RunReport("failed", plan, replans, f"re-plan budget spent; last error {error}")
                    for pending in [s for s in plan.steps if s.status is Status.PENDING]:
                        plan.mark(pending.spec.id, Status.SUPERSEDED, "")
                    replans += 1
                    self._plan(plan, failure=f"Step {step.spec.id} failed: {error}")
        except PlanError as exc:
            return RunReport("failed", plan, replans, f"invalid plan: {exc}")
        return RunReport("done", plan, replans)


if __name__ == "__main__":
    refunds: list[str] = []

    def find_charges(account_id: str) -> str:
        return "ch_2"  # the duplicate charge

    def issue_refund(charge_id: str) -> str:
        refunds.append(charge_id)
        return f"refund queued for {charge_id}"

    def send_email(account_id: str, template: str) -> str:
        raise LookupError(f"{account_id} has no email address on file")

    def create_callback(account_id: str, note: str) -> str:
        return f"callback task created for {account_id}: {note}"

    tools: dict[str, Tool] = {"find_charges": find_charges, "issue_refund": issue_refund,
                              "send_email": send_email, "create_callback": create_callback}
    planner = ScriptedLLM([
        json.dumps({"steps": [
            {"id": "s1", "description": "Find the duplicate charge", "tool": "find_charges",
             "args": {"account_id": "acct-17"}},
            {"id": "s2", "description": "Refund it", "tool": "issue_refund",
             "args": {"charge_id": "{s1}"}, "depends_on": ["s1"]},
            {"id": "s3", "description": "Email the customer", "tool": "send_email",
             "args": {"account_id": "acct-17", "template": "refund_done"}, "depends_on": ["s2"]},
        ]}),
        json.dumps({"steps": [
            {"id": "s4", "description": "Ask a person to phone the customer instead", "tool": "create_callback",
             "args": {"account_id": "acct-17", "note": "Tell them: {s2}"}, "depends_on": ["s2"]},
        ]}),
    ])
    report = PlanExecutor(planner, tools).run("Refund the duplicate charge on acct-17 and tell the customer")
    print(f"status: {report.status} after {report.replans} re-plan(s)\n{report.plan.render()}")
    print(f"\nrefunds issued: {refunds}  (the refund was not repeated by the re-plan)")
