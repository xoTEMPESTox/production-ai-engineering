import asyncio
import json
import unittest

from example import QUESTION, Harness, ResearchAssistant, RuleBasedLLM, score, search


def run(llm=None, tenant: str = "acme", question: str = QUESTION, **kwargs):
    assistant = ResearchAssistant(llm or RuleBasedLLM(), **kwargs)
    return assistant, asyncio.run(assistant.run(question, tenant))


class TestRetrieval(unittest.TestCase):
    def test_other_tenants_documents_never_come_back(self) -> None:
        self.assertTrue(all(d.tenant == "acme" for d in search("refunds plan refunded", "acme", k=10)))
        self.assertEqual([d.id for d in search("refunds plan", "globex")], ["gx-refund"])

    def test_injected_document_is_quarantined_not_shown(self) -> None:
        assistant, report = run()
        self.assertEqual(report.quarantined, ["wiki-export"])
        shown = [a["doc_ids"] for name, a in assistant.harness.trace if name == "retrieval"]
        self.assertTrue(all("wiki-export" not in ids for ids in shown))
        self.assertNotIn("disabled", report.text)


class TestPipeline(unittest.TestCase):
    def test_full_run_answers_every_sub_question_with_citations(self) -> None:
        assistant, report = run()
        self.assertTrue(report.complete)
        self.assertEqual(report.citations, ["pol-export", "pol-refund"])
        self.assertTrue(score(report, ["30 days", "Settings > Export"], {"pol-refund", "pol-export"})["passed"])

    def test_fabricated_citation_is_repaired_once(self) -> None:
        assistant, report = run(RuleBasedLLM(fabricate_once=True))
        rejected = [a for name, a in assistant.harness.trace if name == "finding.rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertIn("pol-made-up", rejected[0]["error"])
        self.assertNotIn("pol-made-up", report.text)
        self.assertTrue(report.complete)

    def test_unanswerable_sub_question_is_marked_missing_not_invented(self) -> None:
        class NoSources(RuleBasedLLM):
            async def complete(self, role, prompt):
                if role == "researcher":
                    return json.dumps({"answer": "probably 60 days", "citations": []})  # no evidence
                return await super().complete(role, prompt)

        assistant, report = run(NoSources())
        self.assertFalse(report.complete)
        self.assertNotIn("60 days", report.text)
        self.assertIn("Not verified:", report.text)

    def test_slow_sub_agent_times_out_and_the_rest_still_answer(self) -> None:
        assistant, report = run(RuleBasedLLM(slow_on="export"), timeout_s=0.2)
        self.assertEqual(report.missing, ["How does a workspace owner export their data?"])
        self.assertIn("30 days", report.text)
        self.assertIn(("subagent.failed", {"sub_question": report.missing[0], "error": "TimeoutError"}),
                      assistant.harness.trace)

    def test_report_cites_only_sources_some_finding_verified(self) -> None:
        assistant, report = run(RuleBasedLLM(slow_on="export"), timeout_s=0.2)
        retrieved = {i for name, a in assistant.harness.trace if name == "retrieval" for i in a["doc_ids"]}
        self.assertTrue(set(report.citations) <= retrieved)

    def test_invalid_plan_degrades_to_one_step(self) -> None:
        class BadPlanner(RuleBasedLLM):
            async def complete(self, role, prompt):
                return "Step 1: research" if role == "planner" else await super().complete(role, prompt)

        assistant, report = run(BadPlanner(), question="How long do refunds take?")
        plan = next(a for name, a in assistant.harness.trace if name == "plan")
        self.assertEqual(plan["sub_questions"], ["How long do refunds take?"])


class TestHarness(unittest.TestCase):
    def test_budget_stops_runaway_calls(self) -> None:
        assistant = ResearchAssistant(RuleBasedLLM(fabricate_once=True), harness=Harness(max_calls=3))
        with self.assertRaises(Exception):
            asyncio.run(assistant.run(QUESTION, "acme"))  # the writer has no budget left
        self.assertEqual(assistant.harness.calls, 3)

    def test_every_step_is_traced_and_priced(self) -> None:
        assistant, report = run()
        names = [name for name, _ in assistant.harness.trace]
        for step in ("llm.planner", "plan", "retrieval", "llm.researcher", "llm.writer", "report"):
            self.assertIn(step, names)
        self.assertEqual(assistant.harness.cost_usd, round(assistant.harness.calls * 0.002, 6))

    def test_score_fails_when_a_required_fact_is_missing(self) -> None:
        assistant, report = run(RuleBasedLLM(slow_on="export"), timeout_s=0.2)
        result = score(report, ["30 days", "Settings > Export"], {"pol-refund", "pol-export"})
        self.assertFalse(result["passed"])
        self.assertEqual(result["facts"], 0.5)


if __name__ == "__main__":
    unittest.main()
