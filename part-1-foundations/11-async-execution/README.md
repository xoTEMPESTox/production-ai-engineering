# Lesson 11: Async Execution

Learn how to keep many model and tool calls in flight without losing control: a concurrency limit, per-call timeouts, deadlines with clean cancellation, backpressure, and an event loop that never blocks.

> **Pillars:** Harness · Agent · RAG  |  **Harness layer:** concurrency, meaning many calls at once while every one of them stays bounded
>
> *Bound it, time it, cancel it, and never block the loop.*

## Why does this exist?
A model call takes seconds, and almost all of that time is spent waiting on the network. Running 500 golden cases (Lesson 09), embedding 10,000 chunks (Lesson 05), running sub-agents in parallel (Lesson 07) or querying three indexes at once is painfully slow one call at a time. With `asyncio`, one process can keep many calls in flight while it waits.

Concurrency without limits creates new failures, though: rate-limit errors, memory growth, one slow call holding up everything, and tasks that keep running (and spending) after nobody needs their result. This lesson builds the controls.

---

## What problem does it solve?
1. **Throughput vs. provider limits**: unlimited fan-out triggers rate-limit and overload errors.
2. **Slow calls**: one hung call must not hold up the whole batch.
3. **Partial failure**: one error must not throw away 499 good results.
4. **Cancellation**: when the deadline passes or the user leaves, in-flight calls must stop, and their clean-up must still run.
5. **Memory**: a fast producer (a crawler, a file reader) can enqueue work far faster than workers finish it.
6. **Blocking calls**: one synchronous call inside async code freezes every other task in the process.

---

## How would beginners implement this?
```python
async def answer_all(questions):
    return await asyncio.gather(*(llm.complete(q) for q in questions))  # 5,000 at once

async def handler(question):
    tokens = tokenizer.count(question)  # synchronous, 200 ms of CPU
    time.sleep(1)                       # "a simple backoff"
    return await llm.complete(question)
```

### Why does that fail?
1. **There is no limit.** 5,000 requests start at once: rate-limit errors, thousands of open sockets, memory spikes.
2. **There are no timeouts.** One stuck call keeps the `gather` waiting forever.
3. **One exception fails everything.** By default `gather` raises the first exception while the other calls keep running, unobserved. With `return_exceptions=True` you get back a list that mixes values and exception objects.
4. **There is no deadline or cancellation.** If the user disconnects, all 5,000 calls keep spending.
5. **`time.sleep` and synchronous CPU work block the event loop.** Every other request served by that process stalls for the duration.
6. **A bare `except:` (or `except BaseException`) in a wrapper swallows `CancelledError`**, so the task can no longer be cancelled.

---

## How is this solved in production?
The core of `run_bounded` in `example.py`:
```python
semaphore = asyncio.Semaphore(limit)

async def one(index, prompt):
    async with semaphore:                                     # 1. at most `limit` in flight
        try:
            value = await asyncio.wait_for(call(prompt), timeout_s)   # 2. per-call timeout
            return CallResult(index, True, value)
        except asyncio.TimeoutError:
            return CallResult(index, False, error="timeout")  # 3. a failure is one typed row
        except Exception as exc:                              #    Exception, not BaseException
            return CallResult(index, False, error=f"{type(exc).__name__}: {exc}")

tasks = [asyncio.create_task(one(i, p)) for i, p in enumerate(prompts)]
try:
    await asyncio.wait(tasks, timeout=deadline_s)             # 4. a deadline for the batch
finally:                                                      #    ...or our caller was cancelled
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)      #    let them clean up
```

1. **A semaphore caps calls in flight.** The demo's peak is exactly 3. Set the limit from the provider's rate limits; Lesson 30 makes it adapt.
2. **Every call has a timeout.** `asyncio.wait_for` cancels the timed-out call, so its `finally` block runs. (On Python 3.11+, `asyncio.timeout()` does the same as a context manager.)
3. **Every prompt gets a typed result, in input order**, whether it succeeded, failed or timed out. The demo's batch of eight returns six answers, one `ConnectionError` and one `timeout`.
4. **No task outlives the call.** The `finally` block cancels unfinished tasks whether the batch deadline passed or the caller itself was cancelled, then waits for their clean-up. On Python 3.11+, `asyncio.TaskGroup` gives the same no-orphans guarantee. Catching `Exception`, not `BaseException`, lets cancellation propagate.
5. **A bounded queue provides backpressure.** In `pipeline`, `await queue.put(doc)` waits whenever the queue already holds `queue_size` items, so a producer of 50 documents never has more than four waiting, however fast it runs.
6. **The loop is never blocked.** Blocking I/O and synchronous SDK calls go through `await asyncio.to_thread(fn, ...)`, and CPU-heavy work goes to a process pool. `max_loop_lag` measures how long the loop was frozen: about 0.2 s for a blocking call, about 0.01 s with `to_thread`.

The fake client's latencies use real `asyncio.sleep`, so the demo and the tests show real concurrency, scaled down to tenths of a second.

---

## Design Tradeoffs

### asyncio vs. threads vs. processes
* **asyncio**: thousands of concurrent waits in one thread at very low cost. Everything on the path must be async-aware, and a single blocking call stalls them all.
* **Threads** (as Lessons 04 and 07 use): work with synchronous libraries and are fine for tens of concurrent calls. Each thread costs memory, and CPU-bound Python code doesn't speed up.
* **Processes**: for CPU-bound work such as local models, heavy parsing or tokenising big corpora. Start-up and data transfer are expensive.

### Fixed vs. adaptive concurrency
* **Fixed limit** (this lesson): simple and predictable, but it has to be tuned by hand and goes wrong when provider limits change.
* **Adaptive** (cut the limit on rate-limit errors and raise it slowly while calls succeed, or budget by tokens per minute): tracks real capacity, at the cost of more moving parts (Lesson 30).

### Per-call timeout vs. batch deadline
* **Per-call timeout**: protects against one hung call, but a batch of many slow-but-not-hung calls can still take too long.
* **Batch deadline**: bounds what the user waits for, and returns partial results marked `cancelled`.
* Use both. The deadline is the user-facing promise, and the timeout protects each slot.

---

## Failure Cases to Watch For
1. **Unbounded `gather`** over a user-sized list.
2. **Missing timeouts** on network calls.
3. **Lost exceptions**: a fire-and-forget `create_task` whose result nobody awaits, so its error is never seen (and an unreferenced task can be garbage-collected mid-run).
4. **Orphaned tasks**: the caller is cancelled but the calls it started keep running and spending. The first draft of this lesson had exactly this bug, found because a test ran longer than it should have.
5. **Swallowed cancellation**: `except BaseException` or a bare `except:` around an `await`.
6. **Blocking the event loop**: `time.sleep`, synchronous HTTP clients, synchronous SDK calls or large parses inside `async def`.
7. **Unbounded queues** between a fast producer and slow consumers.
8. **A semaphore around the wrong scope**: held across retry backoff sleeps, so a slot sits idle while it waits (Lesson 16).

---

## Try It
```sh
cd part-1-foundations/11-async-execution
python example.py            # about a second of real (scaled-down) waiting
python -m pytest tests.py -q
```
What to watch for: the peak in flight is 3, and two of the eight calls fail in their own rows without affecting the rest. Under the 0.4-second deadline, the first wave finishes, the rest are cancelled, and nothing is left in flight. The pipeline's queue never holds more than four documents. And the blocking call freezes the loop for about 0.2 s, against about 0.01 s with `to_thread`.

Experiment: replace `async with semaphore:` with `if True:` and run the tests. `test_concurrency_never_exceeds_the_limit` reports a peak of 9: every prompt was in flight at once.

**Next:** Lesson 12 makes agents react to events: a transactional outbox, at-least-once delivery, and consumers that are safe to run twice.
