"""
Lesson 04: Tool Calling.

Tools are contracts too. Each tool declares a Pydantic argument model (which
becomes the JSON Schema the model sees), a permission tier and a timeout.
One dispatcher runs every call through the same pipeline: validate the
arguments, check permission, deduplicate side effects with an idempotency
key, execute with a timeout, and truncate the result before the model sees it.
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as ToolTimeout
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Tier(str, Enum):
    READ = "read"                # no side effects: runs freely
    WRITE = "write"              # side effects: deduplicated by idempotency key
    DESTRUCTIVE = "destructive"  # irreversible: needs explicit approval first


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    idempotency_key: str  # empty for read tools; forward it to downstream APIs


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[Any, ToolContext], str]  # receives validated args
    tier: Tier = Tier.READ
    timeout_s: float = 5.0

    def json_schema(self) -> dict[str, Any]:
        """The definition the model sees. Providers wrap it in their own envelope."""
        return {"name": self.name, "description": self.description,
                "parameters": self.args.model_json_schema()}


class Outcome(str, Enum):
    OK = "ok"
    REPLAYED = "replayed"          # an earlier identical write; not executed again
    INVALID_ARGS = "invalid_args"
    UNKNOWN_TOOL = "unknown_tool"
    DENIED = "denied"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class ToolResult:
    tool: str
    outcome: Outcome
    content: str  # exactly what goes back to the model as the observation
    truncated: bool = False


Approver = Callable[[str, BaseModel], bool]


def deny_all(tool: str, args: BaseModel) -> bool:
    return False


def idempotency_key(run_id: str, tool: str, args: BaseModel) -> str:
    """Same run + same tool + same validated arguments -> same key."""
    canonical = json.dumps(args.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(f"{run_id}|{tool}|{canonical}".encode()).hexdigest()[:16]


def truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    note = f"\n[truncated: showing {limit} of {len(text)} chars. Narrow the query to see more.]"
    return text[:limit] + note, True


class ToolRegistry:
    def __init__(self, approver: Approver = deny_all, max_result_chars: int = 1_500) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._approver = approver
        self._max_chars = max_result_chars
        self._completed: dict[str, str] = {}  # idempotency key -> output of a finished write
        self._pool = ThreadPoolExecutor(max_workers=4)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool '{spec.name}' is already registered")
        self._tools[spec.name] = spec

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.json_schema() for spec in self._tools.values()]

    def call(self, name: str, raw_args: dict[str, Any], run_id: str) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(name, Outcome.UNKNOWN_TOOL,
                              f"ERROR: unknown tool '{name}'. Available: {', '.join(sorted(self._tools))}")

        # 1. Validate: the model's arguments are untrusted input.
        try:
            args = spec.args.model_validate(raw_args)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            return ToolResult(name, Outcome.INVALID_ARGS, f"ERROR: invalid arguments: {problems}")

        # 2. Permission: the approver sees the validated arguments that will actually run.
        if spec.tier is Tier.DESTRUCTIVE and not self._approver(name, args):
            return ToolResult(name, Outcome.DENIED,
                              f"DENIED: '{name}' needs human approval, which was not given. "
                              "Do not retry; tell the user what you wanted to do.")

        # 3. Idempotency: a repeated write returns the first result instead of running twice.
        key = idempotency_key(run_id, name, args) if spec.tier is not Tier.READ else ""
        if key in self._completed:
            content, cut = truncate(self._completed[key], self._max_chars)
            return ToolResult(name, Outcome.REPLAYED, content, cut)

        # 4. Execute with a timeout. Python can't kill the thread, so a timed-out
        #    write may still finish: its outcome is unknown, not failed.
        future = self._pool.submit(spec.handler, args, ToolContext(run_id, key))
        try:
            output = str(future.result(timeout=spec.timeout_s))
        except ToolTimeout:
            message = f"ERROR: '{name}' timed out after {spec.timeout_s}s."
            if spec.tier is not Tier.READ:
                message += " It may still complete; check its status before trying again."
            return ToolResult(name, Outcome.TIMEOUT, message)
        except Exception as exc:  # failures are observations, and are never cached
            return ToolResult(name, Outcome.ERROR, f"ERROR: {type(exc).__name__}: {exc}")

        if key:
            self._completed[key] = output
        # 5. Truncate: one huge result must not flood the context window.
        content, cut = truncate(output, self._max_chars)
        return ToolResult(name, Outcome.OK, content, cut)


# --- Demo tools: help-centre search, refunds, account closure ---

class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=200, description="What to look for")


class RefundArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(pattern=r"^acct-\d+$")
    amount_cents: int = Field(gt=0, le=50_000, description="At most 500.00")
    reason: str = Field(min_length=3, max_length=200)


class CloseArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(pattern=r"^acct-\d+$")


class FakeBilling:
    """Stands in for a payment API that, like real ones, dedups on an idempotency key."""

    def __init__(self) -> None:
        self.refunds: dict[str, RefundArgs] = {}
        self.closed: list[str] = []

    def refund(self, args: RefundArgs, ctx: ToolContext) -> str:
        self.refunds.setdefault(ctx.idempotency_key, args)
        return f"refund {ctx.idempotency_key[:8]} queued: {args.amount_cents / 100:.2f} to {args.account_id}"

    def close(self, args: CloseArgs, ctx: ToolContext) -> str:
        self.closed.append(args.account_id)
        return f"{args.account_id} closed"


def search_docs(args: SearchArgs, ctx: ToolContext) -> str:
    return "\n".join(f"[faq-{i:02d}] Answer {i} mentioning {args.query}." for i in range(1, 80))


def build_registry(billing: FakeBilling, approver: Approver = deny_all) -> ToolRegistry:
    registry = ToolRegistry(approver=approver, max_result_chars=300)
    registry.register(ToolSpec("search_docs", "Search the help centre.", SearchArgs, search_docs))
    registry.register(ToolSpec("issue_refund", "Refund part of a payment.", RefundArgs,
                               billing.refund, tier=Tier.WRITE))
    registry.register(ToolSpec("close_account", "Permanently close an account.", CloseArgs,
                               billing.close, tier=Tier.DESTRUCTIVE))
    return registry


if __name__ == "__main__":
    billing = FakeBilling()
    tools = build_registry(billing)
    print("What the model sees for issue_refund:")
    print(json.dumps(tools.schemas()[1], indent=2), "\n")

    refund = {"account_id": "acct-17", "amount_cents": 1999, "reason": "duplicate charge"}
    calls = [
        ("issue_refund", {"account_id": "17", "amount_cents": -5, "reason": "x"}),
        ("issue_refund", refund),
        ("issue_refund", refund),  # the model repeats itself after a slow response
        ("close_account", {"account_id": "acct-17"}),
        ("search_docs", {"query": "refund"}),
    ]
    for name, raw in calls:
        result = tools.call(name, raw, run_id="run-001")
        print(f"{name:<14} {result.outcome.value:<13} {result.content.splitlines()[-1][:110]}")
    print(f"\nrefunds actually issued: {len(billing.refunds)}; accounts closed: {billing.closed}")

    def approve_closures(tool: str, args: BaseModel) -> bool:
        print(f"  [approval requested] {tool} {args.model_dump()} -> approved")
        return True

    approved = build_registry(billing, approver=approve_closures)
    print(approved.call("close_account", {"account_id": "acct-17"}, run_id="run-002").content)
