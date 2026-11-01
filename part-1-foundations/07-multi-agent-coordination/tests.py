import json
import threading
import time
import unittest

from example import CORPORA, TASKS, KeyedLLM, Message, Orchestrator, SubTask


def finding(task_id: str, citations: list[str], answer: str = "ok") -> str:
    return json.dumps({"task_id": task_id, "answer": answer, "citations": citations})


GOOD = {"Task t1": finding("t1", ["billing-04"]),
        "Task t2": finding("t2", ["privacy-02"]),
        "SYNTHESIZE": "Refund allowed [billing-04]; export first [privacy-02]."}


def prompt_text(messages: list[Message]) -> str:
    return "\n".join(m.content for m in messages)


class TestFanOutFanIn(unittest.TestCase):
    def test_all_findings_merge_into_a_complete_answer(self) -> None:
        report = Orchestrator(KeyedLLM(GOOD), CORPORA).run("goal", TASKS)

        self.assertTrue(report.complete)
        self.assertEqual([r.finding.citations for r in report.results], [["billing-04"], ["privacy-02"]])
        self.assertEqual(report.answer, GOOD["SYNTHESIZE"])

    def test_each_sub_agent_sees_only_its_task_and_scope(self) -> None:
        llm = KeyedLLM(GOOD)
        Orchestrator(llm, CORPORA).run("goal", TASKS)
        t1_prompt = prompt_text(next(m for m in llm.seen if "Task t1" in m[-1].content))

        self.assertIn("billing-04", t1_prompt)
        self.assertNotIn("privacy-02", t1_prompt)       # another scope's documents
        self.assertNotIn(TASKS[1].question, t1_prompt)  # another agent's task
        self.assertNotIn("goal", t1_prompt)             # the orchestrator's context

    def test_orchestrator_sees_findings_not_raw_sources(self) -> None:
        llm = KeyedLLM(GOOD)
        Orchestrator(llm, CORPORA).run("goal", TASKS)
        synth_prompt = prompt_text(next(m for m in llm.seen if "SYNTHESIZE" in m[-1].content))

        self.assertIn("billing-04", synth_prompt)                         # the citation travels
        self.assertNotIn("within 30 days of purchase", synth_prompt)      # the source text doesn't


class TestHandOffContract(unittest.TestCase):
    def check_rejected(self, t1_reply: str, reason: str) -> None:
        report = Orchestrator(KeyedLLM({**GOOD, "Task t1": t1_reply}), CORPORA).run("goal", TASKS)
        t1 = report.results[0]
        self.assertIsNone(t1.finding)
        self.assertIn(reason, t1.error)
        self.assertEqual(report.missing, ("t1",))
        self.assertIsNotNone(report.results[1].finding)  # one bad hand-off doesn't sink the others
        self.assertFalse(report.complete)

    def test_prose_instead_of_a_finding(self) -> None:
        self.check_rejected("Annual plans are refundable.", "invalid hand-off")

    def test_citation_outside_the_sub_agents_scope(self) -> None:
        self.check_rejected(finding("t1", ["privacy-02"]), "cites sources it was not given")

    def test_finding_for_the_wrong_task(self) -> None:
        self.check_rejected(finding("t2", ["billing-04"]), "expected t1")

    def test_overlong_summary_is_rejected(self) -> None:
        self.check_rejected(finding("t1", ["billing-04"], answer="x" * 500), "invalid hand-off")


class TestBoundsAndCost(unittest.TestCase):
    def test_deadline_returns_partial_results_quickly(self) -> None:
        llm = KeyedLLM(GOOD, delays={"Task t2": 1.0})
        started = time.monotonic()
        report = Orchestrator(llm, CORPORA, deadline_s=0.2).run("goal", TASKS)

        self.assertLess(time.monotonic() - started, 0.8)
        self.assertEqual(report.missing, ("t2",))
        self.assertIn("no result within", report.results[1].error)

    def test_answer_citing_an_unverified_source_is_flagged(self) -> None:
        llm = KeyedLLM(GOOD, delays={"Task t2": 1.0})
        report = Orchestrator(llm, CORPORA, deadline_s=0.2).run("goal", TASKS)
        self.assertEqual(report.unsupported, ("privacy-02",))

    def test_parallelism_is_bounded(self) -> None:
        active, peak, lock = [0], [0], threading.Lock()

        class Counting(KeyedLLM):
            def complete(self, messages):
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                time.sleep(0.05)
                with lock:
                    active[0] -= 1
                return super().complete(messages)

        tasks = [SubTask(id=f"t{i}", question="annual refund", scope="billing") for i in range(1, 7)]
        replies = {f"Task t{i}": finding(f"t{i}", ["billing-04"]) for i in range(1, 7)}
        Orchestrator(Counting({**replies, "SYNTHESIZE": "done"}), CORPORA, max_parallel=2).run("goal", tasks)
        self.assertEqual(peak[0], 2)

    def test_no_synthesis_when_nothing_was_verified(self) -> None:
        llm = KeyedLLM({"Task t1": "nope", "Task t2": "nope", "SYNTHESIZE": "invented"})
        report = Orchestrator(llm, CORPORA).run("goal", TASKS)

        self.assertIsNone(report.answer)
        self.assertFalse(any("SYNTHESIZE" in m[-1].content for m in llm.seen))

    def test_tokens_are_accounted_per_agent(self) -> None:
        report = Orchestrator(KeyedLLM(GOOD), CORPORA).run("goal", TASKS)
        self.assertEqual(set(report.tokens_by_agent), {"t1", "t2", "orchestrator"})
        self.assertTrue(all(n > 0 for n in report.tokens_by_agent.values()))


if __name__ == "__main__":
    unittest.main()
