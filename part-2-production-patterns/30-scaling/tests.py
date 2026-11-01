import asyncio
import unittest

from example import AIMDLimit, Gateway, MicroBatcher, Provider, RateLimited, TieredCache, TokenBucket


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def provider(name: str, rpm: float, tpm: float, clock: Clock, call=lambda p: "ok") -> Provider:
    return Provider(name, TokenBucket(rpm, clock), TokenBucket(tpm, clock), call)


class TestTokenBuckets(unittest.TestCase):
    def test_burst_up_to_capacity_then_refill_over_time(self) -> None:
        clock = Clock()
        bucket = TokenBucket(per_minute=60, clock=clock)
        self.assertEqual(sum(bucket.try_take(1) for _ in range(100)), 60)
        clock.now = 10
        self.assertEqual(sum(bucket.try_take(1) for _ in range(100)), 10)  # 1 per second

    def test_idle_time_does_not_exceed_capacity(self) -> None:
        clock = Clock()
        bucket = TokenBucket(per_minute=60, clock=clock)
        clock.now = 3600
        self.assertEqual(sum(bucket.try_take(1) for _ in range(100)), 60)

    def test_token_limit_applies_as_well_as_request_limit(self) -> None:
        clock = Clock()
        p = provider("p", rpm=100, tpm=1000, clock=clock)
        admitted = sum(p.admit(400, clock.now) for _ in range(10))
        self.assertEqual(admitted, 2)  # tokens run out long before requests do
        self.assertEqual(p.rpm.tokens, 98)  # refused requests gave their request slot back


class TestGateway(unittest.TestCase):
    def test_spills_over_when_the_primary_is_out_of_capacity(self) -> None:
        clock = Clock()
        a, b = provider("a", 10, 1e6, clock), provider("b", 10, 1e6, clock)
        gateway = Gateway([a, b], clock)
        served = [gateway.complete(f"q{i}", 100)[0] for i in range(15)]
        self.assertEqual(served[:10], ["a"] * 10)
        self.assertEqual(served[10:], ["b"] * 5)

    def test_throttled_provider_is_skipped_during_cooldown(self) -> None:
        clock = Clock()

        def overloaded(prompt: str) -> str:
            raise RateLimited("429")

        a, b = provider("a", 100, 1e6, clock, overloaded), provider("b", 100, 1e6, clock)
        gateway = Gateway([a, b], clock, throttle_cooldown_s=30)
        self.assertEqual(gateway.complete("q", 100)[0], "b")
        a.call = lambda p: "ok"  # recovered, but still in cooldown
        clock.now = 10
        self.assertEqual(gateway.complete("q", 100)[0], "b")
        clock.now = 31
        self.assertEqual(gateway.complete("q", 100)[0], "a")

    def test_everything_exhausted_is_an_explicit_error(self) -> None:
        clock = Clock()
        gateway = Gateway([provider("a", 1, 1e6, clock)], clock)
        gateway.complete("q", 100)
        with self.assertRaises(RateLimited):
            gateway.complete("q", 100)


class TestAIMD(unittest.TestCase):
    def test_grows_slowly_and_halves_on_throttle(self) -> None:
        aimd = AIMDLimit(limit=10)
        for _ in range(10):
            aimd.on_success()
        self.assertTrue(10.5 < aimd.limit < 11.5)  # about +1 after a full window of successes
        before = aimd.limit
        aimd.on_throttle()
        self.assertAlmostEqual(aimd.limit, before / 2)

    def test_bounds_are_respected(self) -> None:
        aimd = AIMDLimit(limit=2, minimum=1, maximum=3)
        for _ in range(5):
            aimd.on_throttle()
        self.assertEqual(aimd.limit, 1)
        for _ in range(100):
            aimd.on_success()
        self.assertEqual(aimd.limit, 3)


class TestBatchingAndCaching(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_share_batched_calls(self) -> None:
        async def embed_batch(texts: list[str]) -> list[list[float]]:
            await asyncio.sleep(0.001)
            return [[float(t.split()[-1])] for t in texts]

        batcher = MicroBatcher(embed_batch, max_batch=32)
        vectors = await asyncio.gather(*(batcher.embed(f"chunk {i}") for i in range(100)))
        self.assertEqual(batcher.calls, 4)
        self.assertEqual([v[0] for v in vectors], [float(i) for i in range(100)])  # each got its own vector

    async def test_a_lone_request_is_not_stuck_waiting_for_a_full_batch(self) -> None:
        async def embed_batch(texts: list[str]) -> list[list[float]]:
            return [[1.0] for _ in texts]

        batcher = MicroBatcher(embed_batch, max_batch=32, max_wait_s=0.01)
        self.assertEqual(await asyncio.wait_for(batcher.embed("only one"), timeout=1), [1.0])

    async def test_concurrent_misses_make_one_upstream_call(self) -> None:
        calls = 0

        async def compute() -> str:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return "answer"

        cache = TieredCache(l2={})
        results = await asyncio.gather(*(cache.get_or_compute("k", compute) for _ in range(25)))
        self.assertEqual((calls, set(results)), (1, {"answer"}))

    async def test_l2_is_shared_and_l1_evicts_least_recently_used(self) -> None:
        shared: dict[str, str] = {}
        one, two = TieredCache(shared, l1_size=2), TieredCache(shared, l1_size=2)

        async def value() -> str:
            return "v"

        for key in ("a", "b", "c"):
            await one.get_or_compute(key, value)
        await two.get_or_compute("a", value)
        self.assertEqual(two.stats, {"l1": 0, "l2": 1, "miss": 0})
        await one.get_or_compute("a", value)  # evicted from one's L1 (only b, c kept), found in L2
        self.assertEqual(one.stats["l2"], 1)

    async def test_failures_reach_waiters_and_are_not_cached(self) -> None:
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            await asyncio.sleep(0.01)
            if attempts == 1:
                raise TimeoutError("provider timed out")
            return "ok"

        cache = TieredCache(l2={})
        outcomes = await asyncio.gather(*(cache.get_or_compute("k", flaky) for _ in range(5)), return_exceptions=True)
        self.assertTrue(all(isinstance(o, TimeoutError) for o in outcomes))
        self.assertEqual(await cache.get_or_compute("k", flaky), "ok")  # retried, not served a cached failure


if __name__ == "__main__":
    unittest.main()
