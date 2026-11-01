import asyncio
import time
import unittest

from example import (
    FakeAsyncLLM, blocking_inside_async, max_loop_lag, offloaded_to_thread, pipeline, run_bounded,
)

PROMPTS = [f"q{i}" for i in range(9)]


class TestBoundedFanOut(unittest.IsolatedAsyncioTestCase):
    async def test_concurrency_never_exceeds_the_limit(self) -> None:
        llm = FakeAsyncLLM(latency={}, default_s=0.05)
        results = await run_bounded(PROMPTS, llm.complete, limit=3)
        self.assertEqual(llm.peak, 3)
        self.assertTrue(all(r.ok for r in results))

    async def test_results_keep_input_order_even_when_calls_finish_out_of_order(self) -> None:
        llm = FakeAsyncLLM(latency={"q0": 0.15, "q1": 0.01, "q2": 0.08})
        results = await run_bounded(PROMPTS[:3], llm.complete, limit=3)
        self.assertEqual([r.index for r in results], [0, 1, 2])
        self.assertEqual(results[0].value, "answer to 'q0'")

    async def test_slow_call_times_out_alone(self) -> None:
        llm = FakeAsyncLLM(latency={"q4": 2.0})
        started = time.perf_counter()
        results = await run_bounded(PROMPTS, llm.complete, limit=3, timeout_s=0.2)

        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(results[4].error, "timeout")
        self.assertEqual(sum(r.ok for r in results), 8)
        self.assertEqual(llm.in_flight, 0)  # the timed-out call was cancelled and cleaned up

    async def test_one_failure_does_not_fail_the_batch(self) -> None:
        llm = FakeAsyncLLM(latency={}, fail={"q1"})
        results = await run_bounded(PROMPTS, llm.complete)
        self.assertFalse(results[1].ok)
        self.assertIn("ConnectionError", results[1].error)
        self.assertEqual(sum(r.ok for r in results), 8)


class TestCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_cancels_the_rest_and_they_clean_up(self) -> None:
        llm = FakeAsyncLLM(latency={p: 0.3 for p in PROMPTS})
        started = time.perf_counter()
        results = await run_bounded(PROMPTS, llm.complete, limit=3, timeout_s=5.0, deadline_s=0.4)

        self.assertLess(time.perf_counter() - started, 0.8)
        self.assertEqual(sum(r.ok for r in results), 3)  # the first wave finished
        self.assertEqual(sum(r.error == "cancelled" for r in results), 6)
        self.assertEqual(llm.in_flight, 0)
        self.assertEqual(llm.cleanups, 6)  # 3 finished + 3 cancelled mid-call; 3 never started

    async def test_cancelling_the_caller_propagates_and_leaves_no_orphans(self) -> None:
        llm = FakeAsyncLLM(latency={p: 1.0 for p in PROMPTS})
        batch = asyncio.create_task(run_bounded(PROMPTS, llm.complete, limit=3, timeout_s=5.0))
        await asyncio.sleep(0.05)
        batch.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await batch
        self.assertEqual(llm.in_flight, 0)  # child calls were cancelled too, not left running
        self.assertEqual(llm.cleanups, 3)


class TestBackpressure(unittest.IsolatedAsyncioTestCase):
    async def test_queue_depth_is_bounded_by_its_size(self) -> None:
        async def slow_embed(doc: str) -> None:
            await asyncio.sleep(0.005)

        stats = await pipeline([f"d{i}" for i in range(60)], slow_embed, workers=2, queue_size=3)
        self.assertEqual(stats.processed, 60)
        self.assertLessEqual(stats.max_queue_depth, 3)


class TestEventLoop(unittest.IsolatedAsyncioTestCase):
    async def test_blocking_call_freezes_the_loop_and_to_thread_does_not(self) -> None:
        blocked = await max_loop_lag(blocking_inside_async())
        offloaded = await max_loop_lag(offloaded_to_thread())
        self.assertGreater(blocked, 0.15)
        self.assertLess(offloaded, 0.1)


if __name__ == "__main__":
    unittest.main()
