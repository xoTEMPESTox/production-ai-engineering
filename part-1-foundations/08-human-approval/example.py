"""
Lesson 08: Human Approval.

A risky action doesn't block a thread waiting for a person. The run pauses:
the proposed action and the AI-written draft are persisted as an approval
request, and the decision arrives later, possibly in another process.
A person approves, edits (re-validated) or rejects; each request is decided
at most once, by someone with the right role. Overdue requests escalate,
then expire (expiry rejects, never approves), and every step is written to
an append-only audit trail. The signed draft records how much the person
changed it.
"""
from __future__ import annotations

import difflib
import time
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

HOUR = 3600.0
ROLE_RANK = {"agent": 0, "support_lead": 1, "billing_manager": 2}


class RefundArgs(BaseModel):
    account_id: str = Field(pattern=r"^acct-\d+$")
    amount_cents: int = Field(gt=0, le=100_000)
    reason: str = Field(min_length=3)


class AuditEntry(BaseModel):
    at: float
    actor: str
    event: str
    detail: str = ""


class ApprovalRequest(BaseModel):
    id: str
    run_id: str
    args: RefundArgs
    draft: str                      # the customer message the agent wrote
    required_role: str = "support_lead"
    status: Literal["pending", "approved", "edited", "rejected", "expired"] = "pending"
    created_at: float
    escalate_at: float
    expires_at: float
    escalated: bool = False
    final_draft: Optional[str] = None
    signed_by: Optional[str] = None
    audit: list[AuditEntry] = Field(default_factory=list)


class Decision(BaseModel):
    actor: str
    role: str
    verdict: Literal["approve", "edit", "reject"]
    amount_cents: Optional[int] = None  # edits
    draft: Optional[str] = None         # edits
    reason: str = ""


@dataclass(frozen=True)
class Outcome:
    status: str                     # auto_approved | awaiting_approval | approved | edited | rejected | refused
    request_id: Optional[str] = None
    observation: str = ""           # what the paused agent is told when it resumes


class ApprovalGate:
    def __init__(self, store: dict[str, str], execute: Callable[[RefundArgs, str], str],
                 notify: Callable[[str, str], None] = lambda role, request_id: None,
                 clock: Callable[[], float] = time.time, auto_approve_cents: int = 10_000,
                 escalate_after_s: float = 4 * HOUR, expire_after_s: float = 24 * HOUR) -> None:
        self._store = store         # request id -> JSON; durable storage in production (Lesson 15)
        self._execute = execute     # receives the request id as an idempotency key
        self._notify = notify
        self._clock = clock
        self._auto_cents = auto_approve_cents
        self._escalate_after = escalate_after_s
        self._expire_after = expire_after_s

    def _load(self, request_id: str) -> ApprovalRequest:
        return ApprovalRequest.model_validate_json(self._store[request_id])

    def _save(self, request: ApprovalRequest, actor: str, event: str, detail: str = "") -> None:
        request.audit.append(AuditEntry(at=self._clock(), actor=actor, event=event, detail=detail))
        self._store[request.id] = request.model_dump_json()

    def propose(self, run_id: str, args: RefundArgs, draft: str) -> Outcome:
        now = self._clock()
        request = ApprovalRequest(id=f"apr-{run_id}", run_id=run_id, args=args, draft=draft, created_at=now,
                                  escalate_at=now + self._escalate_after, expires_at=now + self._expire_after)
        if args.amount_cents <= self._auto_cents:
            result = self._execute(args, request.id)
            request.status, request.final_draft, request.signed_by = "approved", draft, "policy"
            self._save(request, "policy", "auto_approved", f"{args.amount_cents} <= {self._auto_cents}; {result}")
            return Outcome("auto_approved", request.id, f"Refund done: {result}")
        self._save(request, "agent", "requested", f"refund {args.amount_cents} for {args.account_id}")
        self._notify(request.required_role, request.id)
        return Outcome("awaiting_approval", request.id, "Paused: waiting for a person to approve.")

    def decide(self, request_id: str, decision: Decision) -> Outcome:
        request = self._load(request_id)
        if request.status == "pending" and self._clock() >= request.expires_at:
            request.status = "expired"  # don't depend on the sweeper having run
            self._save(request, "system", "expired", "decision arrived after the deadline")
        if request.status != "pending":
            return Outcome("refused", request_id, f"request is already {request.status}")
        if ROLE_RANK.get(decision.role, 0) < ROLE_RANK[request.required_role]:
            return Outcome("refused", request_id, f"{decision.role} cannot approve; needs {request.required_role}")

        if decision.verdict == "reject":
            request.status = "rejected"
            self._save(request, decision.actor, "rejected", decision.reason)
            return Outcome("rejected", request_id, f"A person rejected the refund: {decision.reason}")

        args, draft, changes = request.args, request.draft, []
        if decision.verdict == "edit":
            try:  # a person's edit is input too: validate it like the model's
                args = RefundArgs.model_validate({**args.model_dump(), **(
                    {"amount_cents": decision.amount_cents} if decision.amount_cents is not None else {})})
            except ValidationError as exc:
                return Outcome("refused", request_id, f"invalid edit: {exc.errors()[0]['msg']}")
            if args.amount_cents != request.args.amount_cents:
                changes.append(f"amount {request.args.amount_cents} -> {args.amount_cents}")
            if decision.draft is not None and decision.draft != draft:
                draft = decision.draft
                changes.append(f"draft edited (similarity {draft_similarity(request.draft, draft):.2f})")

        result = self._execute(args, request.id)
        request.args, request.final_draft, request.signed_by = args, draft, decision.actor
        request.status = "edited" if changes else "approved"
        self._save(request, decision.actor, request.status, "; ".join(changes) or "as proposed")
        self._save(request, "system", "executed", result)
        return Outcome(request.status, request_id, f"Refund {request.status} by a person: {result}")

    def sweep(self) -> list[str]:
        """Run periodically: escalate overdue requests once, then expire them."""
        now, events = self._clock(), []
        for request_id in list(self._store):
            request = self._load(request_id)
            if request.status != "pending":
                continue
            if now >= request.expires_at:
                request.status = "expired"  # fail safe: nothing is executed
                self._save(request, "system", "expired", "no decision before the deadline")
                events.append(f"{request_id} expired")
            elif now >= request.escalate_at and not request.escalated:
                request.escalated = True
                self._save(request, "system", "escalated", "notified billing_manager")
                self._notify("billing_manager", request_id)
                events.append(f"{request_id} escalated")
        return events


def draft_similarity(draft: str, final: str) -> float:
    """1.0 = signed unchanged. Track 1 - similarity as the 'human edit rate' (Lesson 26)."""
    return difflib.SequenceMatcher(None, draft, final).ratio()


if __name__ == "__main__":
    now = [1_000_000.0]
    store: dict[str, str] = {}
    issued: list[tuple[str, int]] = []

    def execute(args: RefundArgs, key: str) -> str:
        issued.append((key, args.amount_cents))
        return f"{args.amount_cents / 100:.2f} refunded to {args.account_id}"

    def gate() -> ApprovalGate:
        notify = lambda role, rid: print(f"  [notify {role}] {rid} needs a decision")
        return ApprovalGate(store, execute, notify, clock=lambda: now[0])

    small = gate().propose("run-1", RefundArgs(account_id="acct-17", amount_cents=4000, reason="late delivery"),
                           "Sorry for the delay; we've refunded 40.00.")
    print("40.00 refund:", small.status)
    paused = gate().propose("run-2", RefundArgs(account_id="acct-17", amount_cents=18000, reason="double charge"),
                            "We have refunded 180.00 for the duplicate charge. Apologies!")
    print("180.00 refund:", paused.status, paused.request_id)

    now[0] += 2 * HOUR  # the process restarts; a new gate reads the same store
    outcome = gate().decide(paused.request_id, Decision(
        actor="ana", role="support_lead", verdict="edit", amount_cents=12000,
        draft="We have refunded 120.00 for the duplicate charge (the other charge was a valid renewal)."))
    print("decision:", outcome.status, "->", outcome.observation)
    second = gate().decide(paused.request_id, Decision(actor="sam", role="support_lead", verdict="approve"))
    print("second click:", second.status, "->", second.observation)

    gate().propose("run-3", RefundArgs(account_id="acct-9", amount_cents=25000, reason="outage"), "Refunded 250.00.")
    now[0] += 5 * HOUR
    print("sweep 5h after run-3's request: ", gate().sweep())
    now[0] += 20 * HOUR
    print("sweep 25h after run-3's request:", gate().sweep())

    print("\nrefunds executed:", issued)
    print("audit for", paused.request_id)
    for entry in ApprovalRequest.model_validate_json(store[paused.request_id]).audit:
        print(f"  {entry.actor:<8} {entry.event:<10} {entry.detail}")
