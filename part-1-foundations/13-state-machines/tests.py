import unittest

from example import (
    GuardFailed, InvalidTransition, MergeConflict, Phase, ScriptedLLM, TicketState, account_check, assess,
    billing_check, merge, naive_merge, transition,
)

TICKET = "I was charged twice this month and now I can't log in"


def gathered() -> TicketState:
    start = transition(TicketState(TICKET), Phase.GATHERING)
    return merge(start, {"billing": billing_check(start), "account": account_check(start)})


def at(phase: Phase, **fields) -> TicketState:
    return TicketState(TICKET, phase=phase, **fields)


class TestTransitions(unittest.TestCase):
    def test_happy_path_reaches_resolved(self) -> None:
        state = transition(gathered(), Phase.ASSESSING)
        state = transition(merge(state, {"assess": {"risk": "low"}}), Phase.DRAFTING)
        state = transition(merge(state, {"writer": {"draft": "Fixed."}}), Phase.AWAITING_REVIEW)
        state = transition(merge(state, {"reviewer": {"approved_by": "lead"}}), Phase.RESOLVED)
        self.assertEqual(state.phase, Phase.RESOLVED)
        self.assertIn("awaiting_review -> resolved", state.log)

    def test_undeclared_edge_is_refused_with_the_allowed_ones(self) -> None:
        with self.assertRaises(InvalidTransition) as ctx:
            transition(at(Phase.RECEIVED), Phase.RESOLVED)
        self.assertIn("allowed: gathering", str(ctx.exception))

    def test_terminal_state_has_no_way_out(self) -> None:
        with self.assertRaises(InvalidTransition) as ctx:
            transition(at(Phase.RESOLVED), Phase.DRAFTING)
        self.assertIn("terminal", str(ctx.exception))

    def test_resolving_without_approval_fails_the_guard(self) -> None:
        with self.assertRaises(GuardFailed) as ctx:
            transition(at(Phase.AWAITING_REVIEW, draft="x"), Phase.RESOLVED)
        self.assertIn("a person must approve", str(ctx.exception))

    def test_high_risk_cannot_skip_escalation(self) -> None:
        high = at(Phase.ASSESSING, risk="high")
        with self.assertRaises(GuardFailed):
            transition(high, Phase.DRAFTING)
        self.assertEqual(transition(high, Phase.ESCALATED).phase, Phase.ESCALATED)

    def test_drafting_requires_an_assessment(self) -> None:
        with self.assertRaises(GuardFailed) as ctx:
            transition(at(Phase.ASSESSING), Phase.DRAFTING)
        self.assertIn("risk not assessed", str(ctx.exception))


class TestMerging(unittest.TestCase):
    def test_naive_merge_drops_a_branch_and_reducers_do_not(self) -> None:
        start = transition(TicketState(TICKET), Phase.GATHERING)
        branches = {"billing": billing_check(start), "account": account_check(start)}

        self.assertEqual(naive_merge(start, branches).flags, ("locked_account",))  # the bug
        merged = merge(start, branches)
        self.assertEqual(merged.flags, ("duplicate_charge", "locked_account"))
        self.assertEqual(merged.context, {"charges_this_month": 2, "failed_logins": 5})

    def test_two_branches_writing_a_plain_key_is_a_conflict(self) -> None:
        with self.assertRaises(MergeConflict) as ctx:
            merge(at(Phase.ASSESSING), {"rules": {"risk": "low"}, "model": {"risk": "high"}})
        self.assertIn("'risk' written by both 'rules' and 'model'", str(ctx.exception))

    def test_conflicting_context_values_are_refused(self) -> None:
        with self.assertRaises(MergeConflict):
            merge(at(Phase.GATHERING), {"a": {"context": {"plan": "annual"}}, "b": {"context": {"plan": "monthly"}}})

    def test_branches_cannot_change_the_phase(self) -> None:
        with self.assertRaises(MergeConflict):
            merge(at(Phase.ASSESSING), {"sneaky": {"phase": Phase.RESOLVED}})

    def test_union_reducer_does_not_duplicate_flags(self) -> None:
        state = merge(at(Phase.GATHERING, flags=("a",)), {"x": {"flags": ["a", "b"]}, "y": {"flags": ["b"]}})
        self.assertEqual(state.flags, ("a", "b"))

    def test_merge_leaves_the_previous_state_untouched(self) -> None:
        before = at(Phase.GATHERING, context={"k": 1})
        merge(before, {"x": {"context": {"j": 2}, "flags": ["f"]}})
        self.assertEqual((before.context, before.flags), ({"k": 1}, ()))

    def test_log_records_which_branch_changed_what(self) -> None:
        self.assertIn("billing changed ['context', 'flags']", gathered().log)

    def test_model_sees_every_merged_finding(self) -> None:
        llm = ScriptedLLM(['{"risk": "medium"}'])
        update = assess(llm, transition(gathered(), Phase.ASSESSING))
        self.assertEqual(update, {"risk": "medium"})
        self.assertIn("duplicate_charge", llm.prompts[0])
        self.assertIn("locked_account", llm.prompts[0])


if __name__ == "__main__":
    unittest.main()
