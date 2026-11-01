# Lesson 30: Scaling

Learn how to grow an AI service's throughput when the bottleneck is the model provider, not your CPUs: request and token limits enforced before calling, adaptive concurrency, micro-batching, a two-tier cache that collapses duplicate work, and a gateway that spills over to a second provider.

> **Pillars:** Harness · RAG · Agent  |  **Harness layer:** capacity, meaning how much work gets through, and what happens to the rest
>
> *Respect the limits before the provider has to, batch what can wait a few milliseconds, never compute the same answer twice at once, and have somewhere else to go.*

## Why does this exist?
Adding replicas scales a normal web service. An AI service hits a different wall first: the model provider's **rate limits**, usually requests per minute *and* tokens per minute, per model and per account. Ten more replicas don't raise those limits. They just collect 429s ten times faster. Embedding a large corpus (Lesson 05), running evals (Lesson 27) and fanning out sub-agents (Lesson 07) all slam into the same limit.

Scaling therefore means using capacity well (staying just under the limits, packing requests into fewer calls, not paying twice for the same answer) and adding capacity (a second provider or model), while the stateless parts (API replicas and workers, Lessons 17 and 23) scale horizontally around them.

---

## What problem does it solve?
1. **Rate limits**: stay under requests-per-minute and tokens-per-minute limits instead of discovering them through 429s.
2. **Unknown capacity**: the safe concurrency for a provider changes over the day, so it can't be hard-coded.
3. **Per-call overhead**: one embedding call per chunk wastes requests that a batch would cover.
4. **Duplicate work**: popular questions and cache stampedes, where many concurrent misses for the same key each call the model.
5. **A single point of capacity**: when one provider is saturated or throttling, traffic needs somewhere else to go.

---

## How would beginners implement this?
```python
# "Scale it": more replicas, more threads, retry on 429
async def handle(question):
    for _ in range(5):
        try:
            return await llm.complete(question)  # every replica, every thread, at once
        except RateLimitError:
            await asyncio.sleep(1)

vectors = [await embed(chunk) for chunk in chunks]  # one call per chunk
```

### Why does that fail?
1. **More replicas means more 429s, not more throughput.** The limit is shared, and retries add load on top (Lesson 16).
2. **Limits are discovered by failing.** Every 429 is a wasted round trip and added latency.
3. **Fixed concurrency is wrong both ways**: too high and you're throttled, too low and you waste capacity.
4. **One call per chunk**: 10,000 chunks take 10,000 requests, against the requests-per-minute limit.
5. **No cache, no request collapsing**: a viral question computed hundreds of times, in parallel.
6. **One provider**: when it's saturated, users wait or fail, even with capacity sitting unused elsewhere.

---

## How is this solved in production?
**1. Admit calls against both limits, before calling.** Each `Provider` has two `TokenBucket`s, one counting requests per minute and one counting tokens per minute (using an estimate of the call's tokens). `admit` takes from both, or from neither, giving the request slot back if the token bucket says no. A call that wouldn't fit is never sent.

**2. A gateway that spills over:**
```python
for provider in self.providers:              # in priority order
    if not provider.admit(estimated_tokens, now):
        continue                             # out of capacity here: try the next one
    try:
        return provider.name, provider.call(prompt)
    except RateLimited:                      # it throttled anyway: back off from it for a while
        provider.throttled_until = now + cooldown
raise RateLimited("all providers at capacity")   # the caller queues or sheds (Lesson 11)
```
In the demo's burst of 1,200 requests in ten seconds, provider A serves its capacity (about 700), provider B takes the overflow (about 320), and the remaining 179 are rejected explicitly for the caller to queue or shed. Nothing hammers a provider that's already full. Each provider still needs its own breaker and fallback rules (Lesson 14), and an answer from the second provider may need its own prompt version (Lesson 25).

**3. Adaptive concurrency (AIMD).** `AIMDLimit` adds about one to the concurrency limit per window of successes and **halves** it on a 429, the same rule TCP uses to find a network's capacity. The demo's trajectory is a sawtooth just under the provider's real capacity of 20: 4, 8, 11, 13, 15, 16, 18, 19, then back to 11. Use it as the size of Lesson 11's semaphore.

**4. Micro-batching.** `MicroBatcher.embed(text)` looks like a single call, but requests are collected for up to `max_wait_s` (10 ms) or until `max_batch` (32) are waiting, then sent as one batch call, and each caller gets back its own vector. 100 concurrent embedding requests become 4 provider calls. A lone request is flushed by the timer, so it never waits for a full batch.

**5. A two-tier cache with single-flight.** `TieredCache` checks a small per-replica LRU (L1, no network hop), then a store shared by all replicas (L2, Redis in practice, Lesson 18), and only then computes. Concurrent misses for the same key wait on **one** in-flight computation: 50 simultaneous identical questions plus one on another replica cost exactly one model call. A failed computation is passed to every waiter and isn't cached, so the next request retries (Lesson 14's rule).

**6. Everything else scales horizontally.** Stateless API replicas behind a load balancer, workers scaled on queue depth (Lesson 17), state in Redis and Postgres (Lessons 18 and 19). Rate limits are **global**, though: N replicas each holding a full-size local bucket would send N times the limit. Either give each replica its share (limit / N), or keep the buckets in a shared store.

---

## Design Tradeoffs

### Local buckets vs. a shared rate limiter
* **Local, divided by replica count**: no extra network hop, and simple. Capacity is wasted when traffic is uneven across replicas, and it breaks when the replica count changes unless the share is recomputed.
* **Shared** (Redis counters, a gateway service): exact global limits, at the cost of a network call per request and a dependency that becomes critical.

### Batching: latency vs. efficiency
* **Bigger batches, longer waits**: fewer calls and better throughput, which suits offline work (indexing, evals, Lesson 28's batch APIs).
* **Small batches, short waits** (a few milliseconds): nearly invisible to users while still collapsing concurrent requests. Never make a lone interactive request wait for a full batch.

### More providers vs. one provider plus queueing
* **Several providers or models**: more total capacity and resilience. Answers can differ between them, so each needs its own prompt versions and evals, and costs differ.
* **One provider plus a queue**: consistent outputs, but bursts wait (and some time out), and an outage has nowhere to go.

---

## Failure Cases to Watch For
1. **Scaling replicas into a fixed provider limit**: more 429s, more retries, no more throughput.
2. **Per-replica buckets each sized to the global limit**: N times over the limit.
3. **Counting requests but not tokens**: a few huge prompts exhaust the token limit while the request count looks fine.
4. **Fixed concurrency**: throttled at peak, idle off-peak.
5. **Cache stampedes**: a popular key expires and hundreds of concurrent misses all call the model.
6. **Caching failures**, or caching answers across tenants whose context differs (Lesson 28).
7. **Batches that wait for fullness**: the lone request at 3 a.m. waits forever.
8. **Silent overload**: requests dropped or timing out instead of an explicit "at capacity" the caller can act on.

---

## Try It
```sh
cd part-2-production-patterns/30-scaling
python example.py            # fake providers and clock; real asyncio for batching and caching
python -m pytest tests.py -q
```
What to watch for: in the burst, provider A serves about 700, provider B about 320, and 179 are rejected explicitly. The AIMD trajectory climbs toward 20 and halves on throttling. 100 embedding requests make 4 calls. 51 requests for the same answer, across two replicas, make one model call.

Experiment: in `TieredCache.get_or_compute`, disable the in-flight check (`if key in self._inflight:` → `if False:`) and run the tests. `test_concurrent_misses_make_one_upstream_call` fails. All 25 concurrent requests for the same question now start their own model call (a cache stampede), and the shared in-flight bookkeeping trips over itself with a `KeyError`. Both show why concurrent misses need exactly one owner.

**Next:** Part 3 puts the lessons together in complete systems: a customer support agent, a research assistant and a coding agent, each built from the patterns in Parts 1 and 2.
