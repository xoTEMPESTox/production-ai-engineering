import asyncio
import json
import unittest

from example import Tracer, answer, cost_usd, redact, sampled, to_eval_case, trace_summary

QUESTION = "Can I get a refund on annual plans? Call me on +91 98765 43210"


def run(rate: float = 1.0, fail: bool = False) -> Tracer:
    tracer = Tracer(content_sample_rate=rate)
    answer(tracer, QUESTION, "ana@example.com", fail_first_model=fail)
    return tracer


class TestSpans(unittest.TestCase):
    def test_steps_nest_under_one_trace(self) -> None:
        tracer = run()
        root = next(s for s in tracer.finished if s.name == "agent.run")
        children = [s for s in tracer.finished if s is not root]
        self.assertIsNone(root.parent_id)
        self.assertTrue(all(s.parent_id == root.span_id for s in children))
        self.assertEqual({s.trace_id for s in tracer.finished}, {root.trace_id})

    def test_failure_is_recorded_and_still_raised(self) -> None:
        tracer = Tracer()
        with self.assertRaises(ValueError):
            with tracer.span("step"):
                raise ValueError("boom")
        span = tracer.finished[0]
        self.assertEqual((span.status, span.attributes["error.type"]), ("error", "ValueError"))
        self.assertGreaterEqual(span.end, span.start)

    def test_fallback_shows_as_a_failed_span_then_a_good_one(self) -> None:
        chats = [s for s in run(fail=True).finished if s.name == "gen_ai.chat"]
        self.assertEqual([(s.attributes["gen_ai.request.model"], s.status) for s in chats],
                         [("large-model", "error"), ("small-model", "ok")])

    def test_concurrent_runs_do_not_mix_parents(self) -> None:
        tracer = Tracer()

        async def one(name: str) -> None:
            with tracer.span(f"run-{name}"):
                await asyncio.sleep(0.01)
                with tracer.span(f"step-{name}"):
                    await asyncio.sleep(0.01)

        async def both() -> None:
            await asyncio.gather(one("a"), one("b"))

        asyncio.run(both())
        by_name = {s.name: s for s in tracer.finished}
        self.assertEqual(by_name["step-a"].parent_id, by_name["run-a"].span_id)
        self.assertEqual(by_name["step-b"].parent_id, by_name["run-b"].span_id)
        self.assertNotEqual(by_name["run-a"].trace_id, by_name["run-b"].trace_id)


class TestAttributes(unittest.TestCase):
    def test_model_tokens_and_cost(self) -> None:
        chat = next(s for s in run(fail=True).finished if s.name == "gen_ai.chat" and s.status == "ok")
        self.assertEqual(chat.attributes["gen_ai.usage.input_tokens"], 850)
        self.assertEqual(chat.attributes["app.cost_usd"], cost_usd("small-model", 850, 40))
        self.assertAlmostEqual(cost_usd("small-model", 1_000_000, 1_000_000), 0.75)

    def test_trace_summary_adds_up_calls(self) -> None:
        tracer = run(fail=True)
        summary = trace_summary(tracer.finished, tracer.finished[0].trace_id)
        self.assertEqual((summary["spans"], summary["errors"], summary["tokens"]), (4, 1, 890))

    def test_retrieval_records_ids_not_text(self) -> None:
        retrieval = next(s for s in run().finished if s.name == "retrieval")
        self.assertEqual(retrieval.attributes["retrieval.doc_ids"], ["billing-04"])
        self.assertNotIn("refunded in full", json.dumps(retrieval.attributes))


class TestContentCapture(unittest.TestCase):
    def test_nothing_captured_when_not_sampled(self) -> None:
        exported = run(rate=0.0).export_jsonl()
        for key in ('"app.prompt":', '"app.completion":', '"app.question":'):
            self.assertNotIn(key, exported)
        self.assertNotIn("refund on annual plans", exported)

    def test_captured_content_is_redacted(self) -> None:
        exported = run(rate=1.0).export_jsonl()
        for secret in ("ana@example.com", "98765 43210"):
            self.assertNotIn(secret, exported)
        self.assertIn("[PHONE]", exported)
        self.assertIn("[EMAIL]", exported)

    def test_card_numbers_are_redacted(self) -> None:
        self.assertEqual(redact("card 4111 1111 1111 1111 please"), "card [CARD] please")

    def test_sampling_is_per_trace_and_deterministic(self) -> None:
        picks = [sampled(f"trace-{i}", 0.1) for i in range(5_000)]
        self.assertTrue(picks == [sampled(f"trace-{i}", 0.1) for i in range(5_000)])  # no slow list diff
        self.assertTrue(350 < sum(picks) < 650)

    def test_export_is_one_json_object_per_span(self) -> None:
        rows = [json.loads(line) for line in run().export_jsonl().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["duration_ms"] >= 0 for row in rows))


class TestTracesToEvals(unittest.TestCase):
    def test_sampled_trace_becomes_an_unlabelled_eval_case(self) -> None:
        tracer = run(fail=True)
        case = to_eval_case(tracer.finished, tracer.finished[0].trace_id)
        self.assertEqual(case["retrieved_ids"], ["billing-04"])
        self.assertEqual(case["answer_citations"], ["billing-04"])
        self.assertIn("[PHONE]", case["question"])
        self.assertIsNone(case["label"])

    def test_unsampled_trace_gives_no_case(self) -> None:
        tracer = run(rate=0.0)
        self.assertIsNone(to_eval_case(tracer.finished, tracer.finished[0].trace_id))


if __name__ == "__main__":
    unittest.main()
