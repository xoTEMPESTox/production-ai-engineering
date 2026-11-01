import json
import unittest

from pydantic import ValidationError

from example import (
    GOLDEN, OUTPUTS, GoldenCase, ScriptedLLM, SystemOutput, calibrate, cohens_kappa, facts_found,
    judge_pairwise, judge_pointwise, precision_at_k, reciprocal_rank, recall_at_k, score_case, self_review,
)


class TestRetrievalMetrics(unittest.TestCase):
    def test_worked_example(self) -> None:
        # 10 relevant chunks exist; the top 5 contain 4 of them; the first relevant is at rank 2.
        relevant = frozenset(f"r{i}" for i in range(10))
        retrieved = ("x", "r0", "r1", "r2", "r3", "y")
        self.assertAlmostEqual(precision_at_k(retrieved, relevant, 5), 0.8)
        self.assertAlmostEqual(recall_at_k(retrieved, relevant, 5), 0.4)
        self.assertAlmostEqual(reciprocal_rank(retrieved, relevant), 0.5)

    def test_nothing_relevant_retrieved(self) -> None:
        self.assertEqual(reciprocal_rank(("a", "b"), frozenset({"c"})), 0.0)
        self.assertEqual(recall_at_k(("a", "b"), frozenset({"c"}), 2), 0.0)


class TestAnswerScoring(unittest.TestCase):
    def test_required_facts_ignore_case_and_punctuation(self) -> None:
        self.assertEqual(facts_found("Refunds within 30 Days!", ("30 days",)), 1.0)
        self.assertEqual(facts_found("Refunds within a month.", ("30 days",)), 0.0)

    def test_good_case_passes(self) -> None:
        self.assertEqual(score_case(GOLDEN[0], OUTPUTS["g1"]).diagnosis, "pass")

    def test_retrieval_miss_is_blamed_on_retrieval_not_the_model(self) -> None:
        card = score_case(GOLDEN[1], OUTPUTS["g2"])
        self.assertFalse(card.passed)
        self.assertEqual(card.diagnosis, "retrieval_miss")

    def test_generation_miss_when_the_evidence_was_retrieved(self) -> None:
        out = SystemOutput(("billing-04",), "You can get a refund [billing-04].", ("billing-04",))
        self.assertEqual(score_case(GOLDEN[0], out).diagnosis, "generation_miss")

    def test_citing_something_not_retrieved_is_ungrounded(self) -> None:
        out = SystemOutput(("billing-04",), "Within 30 days [billing-99].", ("billing-99",))
        self.assertEqual(score_case(GOLDEN[0], out).diagnosis, "ungrounded")

    def test_no_citation_is_ungrounded(self) -> None:
        out = SystemOutput(("billing-04",), "Within 30 days.", ())
        self.assertFalse(score_case(GOLDEN[0], out).grounded)


class TestCompleteness(unittest.TestCase):
    def test_missing_item_is_found_outside_the_model(self) -> None:
        card = score_case(GOLDEN[2], OUTPUTS["g3"])
        self.assertEqual(card.missing_items, ("inv-1044",))
        self.assertEqual(card.diagnosis, "incomplete")

    def test_self_review_does_not_change_the_verdict(self) -> None:
        review = self_review(ScriptedLLM(['{"complete": true, "count": 4}']), GOLDEN[2], OUTPUTS["g3"])
        self.assertTrue(review["complete"])
        self.assertEqual(review["count"], len(OUTPUTS["g3"].items))  # self-consistent, still incomplete
        self.assertFalse(score_case(GOLDEN[2], OUTPUTS["g3"]).passed)

    def test_extra_items_are_not_counted_as_missing(self) -> None:
        case = GoldenCase("c", "q", frozenset({"d"}), expected_items=frozenset({"a", "b"}))
        out = SystemOutput(("d",), "a, b, z [d]", ("d",), items=("a", "b", "z"))
        self.assertEqual(score_case(case, out).missing_items, ())


class TestJudge(unittest.TestCase):
    def test_position_biased_judge_is_caught_by_the_swap(self) -> None:
        biased = ScriptedLLM([json.dumps({"better": 1})])
        self.assertEqual(judge_pairwise(biased, "q", "short", "long"), "inconsistent")
        self.assertEqual(len(biased.prompts), 2)
        self.assertIn("Answer 1: short", biased.prompts[0])
        self.assertIn("Answer 1: long", biased.prompts[1])

    def test_consistent_preference_survives_the_swap(self) -> None:
        # Prefers "long" in both orders: answer 2 first time, answer 1 second time.
        judge = ScriptedLLM([json.dumps({"better": 2}), json.dumps({"better": 1})])
        self.assertEqual(judge_pairwise(judge, "q", "short", "long"), "b")

    def test_out_of_range_score_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            judge_pointwise(ScriptedLLM(['{"score": 7, "reason": "great"}']), "q", "a")

    def test_rubric_is_in_the_prompt(self) -> None:
        llm = ScriptedLLM(['{"score": 4, "reason": "misses the deadline"}'])
        self.assertEqual(judge_pointwise(llm, "q", "a").score, 4)
        self.assertIn("4: correct, but misses a condition", llm.prompts[0])


class TestCalibration(unittest.TestCase):
    def test_always_pass_judge_has_high_agreement_but_no_skill(self) -> None:
        result = calibrate([True] * 10, [True] * 9 + [False])
        self.assertEqual(result.agreement, 0.9)
        self.assertEqual(result.kappa, 0.0)
        self.assertFalse(result.trusted)

    def test_judge_that_tracks_humans_is_trusted(self) -> None:
        human = [True, True, False, True, False, False, True, True, False, True]
        judge = [True, True, False, True, False, True, True, True, False, True]  # one disagreement
        result = calibrate(judge, human)
        self.assertGreater(result.kappa, 0.6)
        self.assertTrue(result.trusted)

    def test_kappa_of_perfect_agreement_is_one(self) -> None:
        labels = [True, False, True, False]
        self.assertEqual(cohens_kappa(labels, labels), 1.0)


if __name__ == "__main__":
    unittest.main()
