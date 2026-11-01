"""
Lesson 11: Async Execution.

Many model calls at once, safely. A semaphore caps how many are in flight;
each call has its own timeout; one failure becomes a typed result instead of
failing the batch; a deadline for the whole batch cancels what's left and
lets it clean up (cancellation is never swallowed); a bounded queue gives
backpressure so a fast producer can't outrun its consumers; and blocking
work is moved off the event loop with asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol


class AsyncLLMClient(Protocol):
    async def complete(self, prompt: str) -> str: ...


class FakeAsyncLLM:
    """Deterministic stand-in: latency and failures are set per prompt. It counts
    calls in flight (to check the concurrency limit) and finished clean-ups."""

    def __init__(self, latency: dict[str, float], default_s: float = 0.05, fail: Optional[set[str]] = None) -> None:
        self._latency = latency
        self._default = default_s
        self._fail = fail or set()
        self.in_flight = 0
        self.peak = 0
        self.cleanups = 0

    async def complete(self, prompt: str) -> str:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self._latency.get(prompt, self._default))
            if prompt in self._fail:
                raise ConnectionError("provider returned 503 unavailable")
            return f"answer to {prompt!r}"
        finally:  # runs on success, error, timeout and cancellation alike
            self.in_flight -= 1
            self.cleanups += 1


@dataclass(frozen=True)
class CallResult:
    index: int
    ok: bool
    value: str = ""
    error: str = ""  # "timeout", "cancelled" or "ExceptionType: message"
    seconds: float = 0.0


async def run_bounded(prompts: list[str], call: Callable[[str], Awaitable[str]], limit: int = 3,
                      timeout_s: float = 1.0, deadline_s: Optional[float] = None) -> list[CallResult]:
    """Run every prompt with at most `limit` in flight. Results come back in
    input order, one per prompt, whatever happened to each."""
    semaphore = asyncio.Semaphore(limit)
    loop = asyncio.get_running_loop()

    async def one(index: int, prompt: str) -> CallResult:
        async with semaphore:
            started = loop.time()
            try:
                value = await asyncio.wait_for(call(prompt), timeout_s)
                return CallResult(index, True, value, seconds=loop.time() - started)
            except asyncio.TimeoutError:
                return CallResult(index, False, error="timeout", seconds=loop.time() - started)
            except Exception as exc:  # not BaseException: CancelledError must propagate
                return CallResult(index, False, error=f"{type(exc).__name__}: {exc}",
                                  seconds=loop.time() - started)

    tasks = [asyncio.create_task(one(i, p)) for i, p in enumerate(prompts)]
    try:
        await asyncio.wait(tasks, timeout=deadline_s)
    finally:  # deadline reached, or our caller was cancelled: no task outlives this call
        for task in tasks:
            task.cancel()  # a no-op for tasks that already finished
        await asyncio.gather(*tasks, return_exceptions=True)  # let them clean up
    return [CallResult(i, False, error="cancelled") if task.cancelled() else task.result()
            for i, task in enumerate(tasks)]


@dataclass(frozen=True)
class PipelineStats:
    processed: int
    max_queue_depth: int


async def pipeline(docs: list[str], embed: Callable[[str], Awaitable[None]],
                   workers: int = 3, queue_size: int = 4) -> PipelineStats:
    """Producer -> bounded queue -> workers. When the queue is full, put() waits,
    so memory stays bounded however fast documents arrive."""
    queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=queue_size)
    depth, processed = [0], [0]

    async def produce() -> None:
        for doc in docs:
            await queue.put(doc)  # backpressure: waits here while workers are busy
            depth[0] = max(depth[0], queue.qsize())
        for _ in range(workers):
            await queue.put(None)  # one stop signal per worker

    async def consume() -> None:
        while (doc := await queue.get()) is not None:
            await embed(doc)
            processed[0] += 1

    await asyncio.gather(produce(), *(consume() for _ in range(workers)))
    return PipelineStats(processed[0], depth[0])


async def max_loop_lag(work: Awaitable[object], tick_s: float = 0.01) -> float:
    """How long the event loop was frozen while `work` ran: the worst delay
    seen by a ticker that wants to wake every tick_s seconds."""
    loop, worst, running = asyncio.get_running_loop(), 0.0, True

    async def ticker() -> None:
        nonlocal worst
        while running:
            before = loop.time()
            await asyncio.sleep(tick_s)
            worst = max(worst, loop.time() - before - tick_s)

    probe = asyncio.create_task(ticker())
    await asyncio.sleep(0)  # let the ticker start
    await work
    running = False
    await probe
    return worst


def count_tokens_slowly(text: str) -> int:
    """Stands in for blocking work: a sync SDK call, a big parse, a local model."""
    time.sleep(0.2)
    return len(text.split())


async def blocking_inside_async() -> int:
    return count_tokens_slowly("a b c")  # freezes every other task for 0.2s


async def offloaded_to_thread() -> int:
    return await asyncio.to_thread(count_tokens_slowly, "a b c")  # the loop keeps running


async def main() -> None:
    prompts = [f"question {i}" for i in range(8)]
    llm = FakeAsyncLLM(latency={"question 5": 0.5}, fail={"question 2"})
    started = time.perf_counter()
    results = await run_bounded(prompts, llm.complete, limit=3, timeout_s=0.2)
    print(f"8 calls, limit 3, took {time.perf_counter() - started:.2f}s, peak in flight {llm.peak}")
    for r in results:
        print(f"  {r.index}: {'ok' if r.ok else 'FAILED'} {r.value or r.error}")

    slow = FakeAsyncLLM(latency={p: 0.3 for p in prompts})
    cut = await run_bounded(prompts, slow.complete, limit=3, timeout_s=1.0, deadline_s=0.4)
    print(f"\ndeadline 0.4s: {sum(r.ok for r in cut)} ok, {sum(r.error == 'cancelled' for r in cut)} cancelled, "
          f"in flight afterwards {slow.in_flight}")

    async def embed(doc: str) -> None:
        await asyncio.sleep(0.01)

    stats = await pipeline([f"doc {i}" for i in range(50)], embed, workers=3, queue_size=4)
    print(f"pipeline: {stats.processed} docs, queue never deeper than {stats.max_queue_depth}")

    print(f"loop frozen by a blocking call: {await max_loop_lag(blocking_inside_async()):.2f}s; "
          f"with to_thread: {await max_loop_lag(offloaded_to_thread()):.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
