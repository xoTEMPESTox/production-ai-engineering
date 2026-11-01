import random
import unittest

from example import (
    FakeBilling, FakeProvider, PermanentError, RetryBudget, RetryPolicy, TransientError, call_with_retry,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def run(fn, policy: RetryPolicy = RetryPolicy(), budget: RetryBudget | None = None, seed: int = 0):
    clock = Clock()
    result = call_with_retry(fn, "op-1", policy, budget or RetryBudget(), clock.sleep, lambda: clock.now,
                             random.Random(seed))
    return result, clock


class TestWhenToRetry(unittest.TestCase):
    def test_transient_errors_are_retried_until_success(self) -> None:
        provider = FakeProvider([TransientError("503"), TimeoutError("slow")])
        result, clock = run(provider)
        self.assertTrue(result.ok)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(len(clock.sleeps), 2)

    def test_permanent_errors_are_not_retried(self) -> None:
        provider = FakeProvider([PermanentError("400")])
        result, clock = run(provider)
        self.assertEqual((result.ok, provider.calls, clock.sleeps), (False, 1, []))
        self.assertEqual(result.stop_reason, "not retryable")

    def test_attempts_are_capped(self) -> None:
        provider = FakeProvider([TransientError("503")] * 10)
        result, _ = run(provider, RetryPolicy(max_attempts=3))
        self.assertEqual((provider.calls, result.stop_reason), (3, "attempts exhausted"))


class TestHowLongToWait(unittest.TestCase):
    def test_full_jitter_stays_under_the_exponential_ceiling(self) -> None:
        policy, rng = RetryPolicy(base_s=0.5, cap_s=20), random.Random(3)
        for attempt in range(1, 10):
            ceiling = min(20, 0.5 * 2 ** (attempt - 1))
            delays = [policy.backoff(attempt, rng) for _ in range(200)]
            self.assertTrue(all(0 <= d <= ceiling for d in delays))
            self.assertGreater(max(delays) - min(delays), ceiling * 0.5)  # spread out, not all at once

    def test_jitter_is_reproducible_with_a_seed(self) -> None:
        a = [RetryPolicy().backoff(2, random.Random(9)) for _ in range(3)]
        b = [RetryPolicy().backoff(2, random.Random(9)) for _ in range(3)]
        self.assertEqual(a, b)

    def test_retry_after_is_honoured(self) -> None:
        result, clock = run(FakeProvider([TransientError("429", retry_after=7.0)]))
        self.assertTrue(result.ok)
        self.assertGreaterEqual(clock.sleeps[0], 7.0)

    def test_no_wait_that_would_pass_the_deadline(self) -> None:
        provider = FakeProvider([TransientError("429", retry_after=60.0)])
        result, clock = run(provider, RetryPolicy(deadline_s=30))
        self.assertEqual((result.ok, clock.sleeps, provider.calls), (False, [], 1))
        self.assertIn("deadline", result.stop_reason)


class TestRetryBudget(unittest.TestCase):
    def test_outage_is_not_multiplied_by_the_retry_count(self) -> None:
        down, budget = FakeProvider([TransientError("503")] * 10_000), RetryBudget(ratio=0.1, max_tokens=10)
        for i in range(200):
            run(down, budget=budget, seed=i)
        # 200 first attempts, plus the initial allowance, plus 10% of requests: far below 200 x 4
        self.assertLessEqual(down.calls, 200 + 10 + 20 + 1)

    def test_budget_refills_with_normal_traffic(self) -> None:
        budget = RetryBudget(ratio=0.5, max_tokens=2, tokens=0)
        self.assertFalse(budget.try_spend())
        budget.on_request()
        budget.on_request()
        self.assertTrue(budget.try_spend())


class TestIdempotency(unittest.TestCase):
    def test_every_attempt_carries_the_same_key(self) -> None:
        keys: list[str] = []

        def flaky(key: str) -> str:
            keys.append(key)
            if len(keys) < 3:
                raise TimeoutError("slow")
            return "ok"

        run(flaky)
        self.assertEqual(keys, ["op-1", "op-1", "op-1"])

    def test_retry_after_a_lost_response_applies_the_write_once(self) -> None:
        billing = FakeBilling(time_out_after_applying=2)
        result, _ = run(billing.refund)
        self.assertTrue(result.ok)
        self.assertEqual(len(billing.applied), 1)


if __name__ == "__main__":
    unittest.main()
