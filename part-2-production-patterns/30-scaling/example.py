"""
Lesson 30: Scaling.

Throughput for an AI service is bounded by provider limits more than by
CPU. Token buckets enforce both requests-per-minute and tokens-per-minute
limits before a call is made; adaptive (AIMD) concurrency finds the
provider's real capacity, growing slowly and halving on a 429; embedding
requests are micro-batched; a two-tier cache (per-instance LRU in front of a
shared store) collapses concurrent misses for the same key into one call;
and a gateway spills traffic to a second provider when the first is out of
capacity, not only when it's down.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional


class TokenBucket:
    def __init__(self, per_minute: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = per_minute
        self.rate = per_minute / 60.0  # tokens added per second
        self._clock = clock
        self.tokens = per_minute
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
        self._last = now

    def try_take(self, amount: float) -> bool:
        self._refill()
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False


@dataclass
class Provider:
    """A model provider with request and token limits. call() raises on a 429."""
    name: str
    rpm: TokenBucket
    tpm: TokenBucket
    call: Callable[[str], str]
    throttled_until: float = 0.0
    served: int = 0

    def admit(self, tokens: int, now: float) -> bool:
        if now < self.throttled_until:
            return False
        if not self.rpm.try_take(1):
            return False
        if not self.tpm.try_take(tokens):
            self.rpm.tokens += 1  # give the request slot back
            return False
        return True


class RateLimited(Exception):
    pass


class Gateway:
    """Priority order, with spill-over: a provider that's out of capacity or throttled is skipped."""

    def __init__(self, providers: list[Provider], clock: Callable[[], float] = time.monotonic,
                 throttle_cooldown_s: float = 30.0) -> None:
        self.providers = providers
        self._clock = clock
        self._cooldown = throttle_cooldown_s

    def complete(self, prompt: str, estimated_tokens: int) -> tuple[str, str]:
        now = self._clock()
        for provider in self.providers:
            if not provider.admit(estimated_tokens, now):
                continue  # no capacity here: try the next provider instead of queueing behind this one
            try:
                answer = provider.call(prompt)
            except RateLimited:  # the provider disagrees with our estimate: back off from it for a while
                provider.throttled_until = now + self._cooldown
                continue
            provider.served += 1
            return provider.name, answer
        raise RateLimited("all providers are at capacity: shed load or queue (Lesson 11 backpressure)")


@dataclass
class AIMDLimit:
    """Additive increase, multiplicative decrease: probe for capacity, back off hard on throttling."""
    limit: float = 4.0
    minimum: float = 1.0
    maximum: float = 64.0
    history: list[int] = field(default_factory=list)

    def on_success(self) -> None:
        self.limit = min(self.maximum, self.limit + 1 / self.limit)  # about +1 per full window of calls

    def on_throttle(self) -> None:
        self.limit = max(self.minimum, self.limit / 2)


class MicroBatcher:
    """Collects single embedding requests into one call of up to max_batch, waiting at most max_wait_s."""

    def __init__(self, embed_batch: Callable[[list[str]], Awaitable[list[list[float]]]],
                 max_batch: int = 32, max_wait_s: float = 0.01) -> None:
        self._embed, self._max_batch, self._max_wait = embed_batch, max_batch, max_wait_s
        self._pending: list[tuple[str, asyncio.Future]] = []
        self._timer: Optional[asyncio.Task] = None
        self.calls = 0

    async def embed(self, text: str) -> list[float]:
        future = asyncio.get_running_loop().create_future()
        self._pending.append((text, future))
        if len(self._pending) >= self._max_batch:
            await self._flush()
        elif self._timer is None:
            self._timer = asyncio.create_task(self._flush_later())
        return await future

    async def _flush_later(self) -> None:
        await asyncio.sleep(self._max_wait)
        self._timer = None
        await self._flush()

    async def _flush(self) -> None:
        batch, self._pending = self._pending[:self._max_batch], self._pending[self._max_batch:]
        if not batch:
            return
        self.calls += 1
        vectors = await self._embed([text for text, _ in batch])
        for (_, future), vector in zip(batch, vectors):
            future.set_result(vector)


class TieredCache:
    """L1: a small LRU per instance. L2: shared across instances (Redis in production, Lesson 18).
    Concurrent misses for one key wait on a single upstream call."""

    def __init__(self, l2: dict[str, str], l1_size: int = 100) -> None:
        self._l1: OrderedDict[str, str] = OrderedDict()
        self._l1_size, self._l2 = l1_size, l2
        self._inflight: dict[str, asyncio.Future] = {}
        self.stats = {"l1": 0, "l2": 0, "miss": 0}

    async def get_or_compute(self, key: str, compute: Callable[[], Awaitable[str]]) -> str:
        if key in self._l1:
            self._l1.move_to_end(key)
            self.stats["l1"] += 1
            return self._l1[key]
        if key in self._l2:
            self.stats["l2"] += 1
            return self._store_l1(key, self._l2[key])
        if key in self._inflight:  # someone is already computing it: wait for their result
            return await self._inflight[key]
        self.stats["miss"] += 1
        self._inflight[key] = asyncio.get_running_loop().create_future()
        try:
            value = await compute()
            self._l2[key] = value
            self._inflight[key].set_result(value)
            return self._store_l1(key, value)
        except Exception as exc:
            self._inflight[key].set_exception(exc)  # waiters see the failure; nothing is cached
            raise
        finally:
            del self._inflight[key]

    def _store_l1(self, key: str, value: str) -> str:
        self._l1[key] = value
        self._l1.move_to_end(key)
        if len(self._l1) > self._l1_size:
            self._l1.popitem(last=False)
        return value


if __name__ == "__main__":
    now = [0.0]  # a fake clock: the demo moves time by setting now[0] (a list, so lambdas see the change)
    clock = lambda: now[0]
    primary = Provider("provider-a", TokenBucket(600, clock), TokenBucket(400_000, clock), lambda p: "a")
    backup = Provider("provider-b", TokenBucket(300, clock), TokenBucket(200_000, clock), lambda p: "b")
    gateway, rejected = Gateway([primary, backup], clock), 0
    for i in range(1200):  # a burst of 1,200 requests in 10 seconds
        now[0] = i / 120
        try:
            gateway.complete(f"q{i}", estimated_tokens=500)
        except RateLimited:
            rejected += 1
    print(f"burst: {primary.name} served {primary.served}, {backup.name} served {backup.served}, "
          f"rejected {rejected} (the caller queues or sheds these)")

    aimd = AIMDLimit()
    capacity = 20  # the provider throttles above 20 concurrent calls
    for _ in range(400):
        if aimd.limit > capacity:
            aimd.on_throttle()
        else:
            aimd.on_success()
        aimd.history.append(round(aimd.limit))
    print("AIMD concurrency, every 25th of 400 steps:", aimd.history[::25],
          "(sawtooth just under the provider's capacity)")

    async def demo_async() -> None:
        async def embed_batch(texts: list[str]) -> list[list[float]]:
            await asyncio.sleep(0.005)
            return [[float(len(t))] for t in texts]

        batcher = MicroBatcher(embed_batch, max_batch=32)
        await asyncio.gather(*(batcher.embed(f"chunk {i}") for i in range(100)))
        print(f"100 embedding requests -> {batcher.calls} provider calls")

        calls = 0

        async def answer() -> str:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return "Refunds within 30 days [billing-04]."

        shared: dict[str, str] = {}  # the L2 store both replicas use
        instance_1, instance_2 = TieredCache(l2=shared), TieredCache(l2=shared)
        await asyncio.gather(*(instance_1.get_or_compute("refund window?", answer) for _ in range(50)))
        await instance_2.get_or_compute("refund window?", answer)
        print(f"50 concurrent identical questions + 1 on another replica -> {calls} model call; "
              f"replica 1 {instance_1.stats}, replica 2 {instance_2.stats}")

    asyncio.run(demo_async())
