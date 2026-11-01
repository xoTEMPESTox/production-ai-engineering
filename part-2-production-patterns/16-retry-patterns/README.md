# Lesson 16: Retry Patterns

Learn how to retry without making things worse: only transient errors, exponential backoff with full jitter, honouring `Retry-After`, respecting the caller's deadline, a retry budget, and one idempotency key per logical operation.

> **Pillars:** Harness · Agent  |  **Harness layer:** recovery, meaning how a failed call gets another chance without overloading anything
>
> *Retry the right errors, at the right time, a limited number of times, and safely.*

## Why does this exist?
Model providers and the tools around them fail transiently all the time: rate limits, overloads, timeouts, a dropped connection. A retry turns most of these into successes. Done badly, retries turn a provider's bad minute into a bad hour:
- every client retries at the same instant, so the load spikes exactly when the service is weakest;
- each request is tried four times, so an outage quadruples the traffic;
- a refund whose response was lost is applied twice.

Lesson 14 decided *which* errors may be retried. This lesson decides *how*.

---

## What problem does it solve?
1. **Picking what to retry**: a timeout may succeed next time, but a `400 prompt too long` never will.
2. **Timing**: wait long enough for the service to recover, without making the user wait for nothing.
3. **Synchronised retries**: many clients that failed together mustn't come back together.
4. **Amplification**: during an outage, retries mustn't multiply the load.
5. **Duplicate side effects**: a retried write that already succeeded must not be applied again.

---

## How would beginners implement this?
```python
def call(fn, *args):
    for attempt in range(5):
        try:
            return fn(*args)
        except Exception:
            time.sleep(2 ** attempt)  # 1, 2, 4, 8, 16 seconds, for everyone, for everything
    raise RuntimeError("failed after 5 attempts")
```

### Why does that fail?
1. **Everything is retried**, including permanent errors, so a bad request costs five calls and 31 seconds before it fails anyway.
2. **No jitter.** Every client that failed at 12:00:00 retries at exactly 12:00:01, 12:00:03, 12:00:07: synchronised waves against a struggling service.
3. **`Retry-After` is ignored.** The server said "come back in 30 seconds", and the client knocks again after one.
4. **There's no deadline.** The waits add up to 31 seconds even when the user's request had 10, and the last 16 are spent after the final attempt, waiting for nothing.
5. **There's no budget.** During an outage every request becomes five, so the retries themselves keep the service down.
6. **There's no idempotency.** If `fn` is a refund whose response got lost, it runs again.

---

## How is this solved in production?
The loop in `call_with_retry`, condensed:
```python
budget.on_request()                                      # normal traffic earns retry allowance
for attempt in range(1, policy.max_attempts + 1):
    try:
        return fn(idempotency_key)                       # same key on every attempt
    except Exception as exc:
        if not is_retryable(exc):          stop("not retryable")
        if attempt == policy.max_attempts: stop("attempts exhausted")
        wait = rng.uniform(0, min(cap_s, base_s * 2 ** (attempt - 1)))   # full jitter
        wait = max(wait, exc.retry_after or 0)           # the server knows best
        if elapsed + wait > policy.deadline_s:  stop("would pass the deadline")
        if not budget.try_spend():              stop("retry budget exhausted")
        sleep(wait)
```

**1. Retry only what can succeed.** `is_retryable` accepts transient errors (timeouts, connection errors, 429, 5xx) and nothing else. A permanent error returns after one attempt with no wait (Lesson 14's taxonomy).

**2. Exponential backoff with full jitter.** The ceiling doubles with each attempt, up to `cap_s`, and the actual wait is drawn uniformly between zero and that ceiling. In the demo, 1,000 clients at their third attempt spread across 0–2 seconds instead of all arriving at exactly 2.00 s.

**3. Honour `Retry-After`.** When the server says when to come back, the wait is at least that long.

**4. Respect the caller's deadline.** A wait that would end after `deadline_s` isn't started. The call gives up immediately with a clear reason, so the caller can fall back (Lesson 14) instead of sleeping past its own timeout. A 60-second `Retry-After` against a 30-second deadline fails fast.

**5. A retry budget.** Each request adds `ratio` (here 0.1) to a small allowance, and each retry spends one. Retries are therefore limited to roughly 10% of traffic once the allowance is used up. In the demo's outage, 100 requests make 119 provider calls with the budget and 400 without it.

**6. One idempotency key per logical operation.** The key is created once, by the caller, and passed to every attempt. When the first refund succeeds but its response is lost, the retry carries the same key, and the billing API returns the original result instead of refunding again (Lessons 04 and 10).

Sleep, clock and randomness are all injected, so every rule is tested deterministically without waiting. In async code, release any concurrency slot while backing off, so a sleeping retry doesn't block other work (Lesson 11).

---

## Design Tradeoffs

### Full jitter vs. equal jitter vs. none
* **No jitter**: predictable waits, and synchronised retry storms.
* **Full jitter** (this lesson): spreads load the most. Some retries happen almost immediately.
* **Equal jitter** (half the ceiling fixed, half random): guarantees a minimum wait while still spreading the load. Use it when an immediate retry is known to be pointless.

### Retrying at each layer vs. at one layer
* **Every layer retries** (SDK, client, service, gateway): each looks reasonable alone, and together three layers of four attempts make 64 calls.
* **One designated layer**: usually the one closest to the user's deadline. Set the others to zero retries, or make them budget-aware.

### A retry budget vs. a circuit breaker
* **Retry budget**: caps the *extra* load from retries, but still sends every first attempt.
* **Circuit breaker** (Lesson 14): stops calling a dependency that is down, first attempts included. Use both: the budget for partial failure, the breaker for full outages.

---

## Failure Cases to Watch For
1. **Retrying permanent errors**: wasted money and latency, with the same failure at the end.
2. **Synchronised retries**: fixed backoff with no jitter.
3. **Ignoring `Retry-After`**: rate-limited harder, or blocked.
4. **Sleeping past the deadline**: the user's request times out while the retry loop is still waiting.
5. **Retry amplification**: no budget, and retries at several layers.
6. **A new idempotency key per attempt**: deduplication can't work, so writes repeat.
7. **Holding resources while sleeping**: a semaphore slot, a database transaction or a lock held across a backoff.
8. **Retrying non-idempotent operations without a key**: emails and payments sent twice.

---

## Try It
```sh
cd part-2-production-patterns/16-retry-patterns
python example.py            # fake clock: no real waiting
python -m pytest tests.py -q
```
What to watch for: the jittered waits on the 503s, a wait of exactly 7.0 s for the 429, no retry for the 400, and an immediate give-up when `Retry-After` exceeds the deadline. In the outage, 400 calls drop to 119 with the budget. The refund is applied once despite the lost response. And 1,000 clients spread across the whole 0–2 s window.

Experiment: in `call_with_retry`, pass `f"{idempotency_key}-{attempt}"` to `fn` instead of the key itself, and run the tests. `test_retry_after_a_lost_response_applies_the_write_once` fails: each attempt now looks like a new refund to the billing API.

**Next:** Lesson 17 moves work to background workers: a job queue, a worker pool, visibility timeouts, a dead-letter queue, and one run at a time per record.
