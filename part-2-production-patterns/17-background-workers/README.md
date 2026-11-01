# Lesson 17: Background Workers

Learn how to move slow AI work off the request path: a queue drained by a worker pool, visibility timeouts so a crash doesn't lose work, a dead-letter queue for messages that keep failing, and single-flight processing per record.

> **Pillars:** Harness · RAG · Agent  |  **Harness layer:** execution off the request path, meaning who does the slow work, how often, and what happens when it keeps failing
>
> *Hide messages, don't delete them; park what keeps failing; one run per record at a time.*

## Why does this exist?
Parsing a PDF, chunking and embedding it (Lesson 05), summarising a long thread, or running an agent for minutes doesn't fit inside an HTTP request. The request puts a job on a queue and returns, and workers do the work. A queue gives you retries and load-levelling almost for free.

Its semantics also create three classic failures:
- work lost when a worker dies mid-job;
- a "poison" message retried forever;
- several runs for the same record racing each other, for example when three uploads of the same document arrive within a second.

---

## What problem does it solve?
1. **Durability**: a job survives a crash and is finished by another worker.
2. **Failure isolation**: one message that always fails mustn't block or starve the rest.
3. **Duplicate work**: a burst of events for the same record should cost one run, not one per event, and should still end with the latest version processed.
4. **Throughput**: several workers in parallel, scaled with the backlog.

---

## How would beginners implement this?
```python
jobs = []  # a list in the web process

@app.post("/documents/{doc_id}")
def upload(doc_id):
    jobs.append(doc_id)
    return {"status": "queued"}

def worker():
    while True:
        if jobs:
            doc_id = jobs.pop(0)   # removed before it's processed
            embed_document(doc_id) # a crash here loses the job
```

### Why does that fail?
1. **An in-process list** is lost on restart and can't be shared between machines.
2. **Pop, then process** is at-most-once: a crash mid-job loses the job.
3. **A document that always fails** either crashes the loop or, if it's re-appended, retries forever and starves everything behind it.
4. **Three uploads of one document start three embedding runs.** They race to write the index, the last writer wins (possibly with an older version), and you pay three times.
5. **Nothing records what failed or why.**
6. **`while True: if jobs` spins the CPU** when there's nothing to do.

---

## How is this solved in production?
**1. A queue with receive and acknowledge.** Managed queues, Redis streams and a Postgres table claimed with `SKIP LOCKED` (Lessons 19 and 20) all share these semantics. Receiving a message **hides** it for `visibility_timeout_s`. Acknowledging it deletes it. If the worker dies without acknowledging, the message reappears for another worker, so delivery is at-least-once.

**2. Long jobs extend their visibility.** `extend(message_id, seconds)` is the heartbeat (like Lesson 10's lease). Choose a timeout longer than a typical job, and shorter than you're willing to wait after a crash.

**3. Failures retry through the queue, then dead-letter.** A failed job is left unacknowledged with its error recorded, and it reappears after the timeout. On receive, a message that has already been delivered `max_receives` times moves to `dead_letters` with its last error instead of being handed out again. In the demo the corrupt PDF is parked after three attempts, while the healthy documents carry on.

**4. Single-flight per record:**
```python
def handle(self, message):
    doc_id = message.body["doc_id"]
    if not self.flight.try_start(doc_id):  # a run for this document is already going
        self.queue.ack(message.id)         # absorb the duplicate...
        return "coalesced"                 # ...the running job will re-run once
    try:
        self.process(doc_id); self.queue.ack(message.id); succeeded = True
    finally:
        if self.flight.finish(doc_id, succeeded):  # duplicates arrived during the run
            self.queue.send({"doc_id": doc_id, "reason": "changed while processing"})
```
A duplicate arriving while a run is in progress doesn't start a second run. It marks the document *dirty*, and when the run succeeds, exactly **one** follow-up run is queued, so the latest version always gets processed. Three simultaneous uploads therefore cost two runs at most, never three racing ones. After a failure no follow-up is queued, because the redelivered message will run anyway. Across machines, the in-memory `SingleFlight` becomes a unique "running" row per record, or a lock with an expiry (Lessons 18 and 19), with Lesson 10's fencing tokens for runs that can outlive their lock.

**5. Handlers are idempotent anyway.** At-least-once delivery means a job can run twice after a crash, so processing writes by document ID (an upsert), never "append another copy".

**6. A pool of workers.** `run_pool` starts N threads that each loop receive → handle until nothing is visible. Production workers are separate processes or containers, scaled with queue depth (Lesson 30).

---

## Design Tradeoffs

### Short vs. long visibility timeouts
* **Short**: a crashed worker's job comes back quickly. A slow but healthy job reappears while it's still running, so two workers process it, unless the job heartbeats.
* **Long**: few accidental duplicates, but after a crash the job waits out the whole timeout.

### Coalescing vs. processing every event vs. waiting on the running job
* **Every event**: simple, with every version processed in order, but N times the cost and racing writes.
* **Coalesce plus one follow-up** (this lesson): at most two runs per burst, always ending on the latest version. Intermediate versions are never processed. That's right for re-indexing, and wrong when every event must be acted on (each payment, each message).
* **Waiters subscribe to the running job**: callers that need the result wait for the run already in flight (the "single-flight" pattern found in several standard libraries). Needs a way to deliver the result to every waiter.

### Automatic redrive vs. manual inspection of the dead-letter queue
* **Automatic redrive** (move dead letters back after a fix or a cool-down): recovers from temporary causes without a person, but redriving a real poison message just fails again.
* **Manual inspection**: someone looks at each failure and its error, which is safe and slow. Alert on dead-letter depth either way.

---

## Failure Cases to Watch For
1. **Acknowledging before processing**: at-most-once delivery, so a crash loses the job.
2. **A visibility timeout shorter than the job**: two workers process the same message at once.
3. **No dead-letter queue**: endless retries, and in an ordered queue a poison message blocks everything behind it.
4. **A dead-letter queue nobody watches**: silent data loss. Alert on its depth.
5. **Duplicate runs per record**: racing writes, a stale version winning, multiplied cost.
6. **Non-idempotent handlers**: redelivery appends a second copy or sends a second email.
7. **Retrying permanent errors through the queue**: a corrupt file is retried `max_receives` times for nothing. Classify errors (Lesson 14) and dead-letter permanent ones at once.
8. **A per-record lock without expiry**: a crashed worker holds the record forever.

---

## Try It
```sh
cd part-2-production-patterns/17-background-workers
python example.py            # in-memory queue, real threads, fake clock
python -m pytest tests.py -q
```
What to watch for: two of the three `doc-1` messages are `coalesced`, and `doc-1` is embedded twice in total: once, plus one follow-up for the changes that arrived mid-run. `doc-corrupt` fails three times, then sits in the dead-letter queue with its error, without holding up `doc-2`. Nothing is left in the queue at the end.

Experiment: in `SingleFlight.try_start`, return `True` without checking `_running` (always start), and run the tests. `test_same_document_never_runs_twice_at_once` fails with two simultaneous runs of the same document, which is the race this lesson exists to prevent.

**Next:** Lesson 18 puts checkpoints and locks in Redis: key design, TTLs, and `SET NX` locks with expiry, with an in-memory fake by default.
