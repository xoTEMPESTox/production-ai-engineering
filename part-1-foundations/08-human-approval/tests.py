import unittest

from example import HOUR, ApprovalGate, ApprovalRequest, Decision, RefundArgs, draft_similarity


class Harness:
    """A gate with a fake clock, an in-memory store and a recording executor."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.store: dict[str, str] = {}
        self.executed: list[tuple[str, int]] = []
        self.notified: list[tuple[str, str]] = []

    def gate(self) -> ApprovalGate:  # a fresh instance each time, like a restarted process
        return ApprovalGate(self.store, lambda args, key: self.executed.append((key, args.amount_cents)) or "done",
                            lambda role, rid: self.notified.append((role, rid)), clock=lambda: self.now)

    def request(self, run_id: str = "r1", cents: int = 18_000) -> str:
        outcome = self.gate().propose(run_id, RefundArgs(account_id="acct-17", amount_cents=cents,
                                                         reason="double charge"), "We refunded you.")
        return outcome.request_id

    def saved(self, request_id: str) -> ApprovalRequest:
        return ApprovalRequest.model_validate_json(self.store[request_id])


LEAD = dict(actor="ana", role="support_lead")


class TestPauseAndResume(unittest.TestCase):
    def test_small_refund_is_auto_approved_and_audited(self) -> None:
        h = Harness()
        outcome = h.gate().propose("r1", RefundArgs(account_id="acct-17", amount_cents=4_000, reason="late"), "x")
        self.assertEqual(outcome.status, "auto_approved")
        self.assertEqual(h.executed, [("apr-r1", 4_000)])
        self.assertEqual(h.saved("apr-r1").audit[-1].actor, "policy")

    def test_large_refund_pauses_and_nothing_executes(self) -> None:
        h = Harness()
        request_id = h.request()
        self.assertEqual(h.executed, [])
        self.assertEqual(h.saved(request_id).status, "pending")
        self.assertEqual(h.notified, [("support_lead", request_id)])

    def test_decision_after_restart_resumes_from_stored_state(self) -> None:
        h = Harness()
        request_id = h.request()
        h.now += HOUR
        outcome = h.gate().decide(request_id, Decision(**LEAD, verdict="approve"))  # new gate instance
        self.assertEqual(outcome.status, "approved")
        self.assertEqual(h.executed, [(request_id, 18_000)])
        self.assertEqual(h.saved(request_id).signed_by, "ana")


class TestDecisions(unittest.TestCase):
    def test_edit_changes_what_runs_and_records_the_diff(self) -> None:
        h = Harness()
        request_id = h.request()
        outcome = h.gate().decide(request_id, Decision(**LEAD, verdict="edit", amount_cents=12_000,
                                                       draft="We refunded 120.00."))
        saved = h.saved(request_id)
        self.assertEqual(outcome.status, "edited")
        self.assertEqual(h.executed, [(request_id, 12_000)])
        self.assertEqual(saved.final_draft, "We refunded 120.00.")
        self.assertIn("amount 18000 -> 12000", saved.audit[-2].detail)

    def test_invalid_edit_is_refused_and_request_stays_pending(self) -> None:
        h = Harness()
        request_id = h.request()
        outcome = h.gate().decide(request_id, Decision(**LEAD, verdict="edit", amount_cents=-500))
        self.assertEqual(outcome.status, "refused")
        self.assertIn("invalid edit", outcome.observation)
        self.assertEqual(h.saved(request_id).status, "pending")
        self.assertEqual(h.executed, [])

    def test_reject_executes_nothing_and_tells_the_agent_why(self) -> None:
        h = Harness()
        request_id = h.request()
        outcome = h.gate().decide(request_id, Decision(**LEAD, verdict="reject", reason="not a duplicate"))
        self.assertEqual(outcome.status, "rejected")
        self.assertIn("not a duplicate", outcome.observation)
        self.assertEqual(h.executed, [])

    def test_a_request_is_decided_at_most_once(self) -> None:
        h = Harness()
        request_id = h.request()
        h.gate().decide(request_id, Decision(**LEAD, verdict="approve"))
        again = h.gate().decide(request_id, Decision(actor="sam", role="support_lead", verdict="approve"))
        self.assertEqual(again.status, "refused")
        self.assertEqual(len(h.executed), 1)

    def test_insufficient_role_is_refused(self) -> None:
        h = Harness()
        request_id = h.request()
        outcome = h.gate().decide(request_id, Decision(actor="bot", role="agent", verdict="approve"))
        self.assertEqual(outcome.status, "refused")
        self.assertEqual(h.executed, [])


class TestTimeouts(unittest.TestCase):
    def test_overdue_request_escalates_once(self) -> None:
        h = Harness()
        request_id = h.request()
        h.now += 5 * HOUR
        self.assertEqual(h.gate().sweep(), [f"{request_id} escalated"])
        self.assertEqual(h.gate().sweep(), [])  # not twice
        self.assertIn(("billing_manager", request_id), h.notified)

    def test_expiry_rejects_and_never_executes(self) -> None:
        h = Harness()
        request_id = h.request()
        h.now += 25 * HOUR
        self.assertEqual(h.gate().sweep(), [f"{request_id} expired"])
        late = h.gate().decide(request_id, Decision(**LEAD, verdict="approve"))
        self.assertEqual(late.status, "refused")
        self.assertEqual(h.executed, [])

    def test_late_decision_is_refused_even_if_the_sweeper_never_ran(self) -> None:
        h = Harness()
        request_id = h.request()
        h.now += 25 * HOUR
        late = h.gate().decide(request_id, Decision(**LEAD, verdict="approve"))
        self.assertEqual(late.status, "refused")
        self.assertEqual(h.saved(request_id).status, "expired")
        self.assertEqual(h.executed, [])


class TestSignOff(unittest.TestCase):
    def test_similarity_measures_how_much_the_person_changed(self) -> None:
        self.assertEqual(draft_similarity("same text", "same text"), 1.0)
        self.assertLess(draft_similarity("We refunded 180.00.", "We refunded 120.00, not 180.00."), 0.9)


if __name__ == "__main__":
    unittest.main()
