# Lesson 10: Scheduling & Long-Running Tasks

Learn how to run work that outlives a request: scheduled jobs, leases and heartbeats for exclusive ownership, a checkpoint after every batch, and fencing tokens against zombie workers.

> **Pillars:** Harness · Agent · RAG  |  **Harness layer:** durable execution, meaning work that survives the process that started it
>
> *Whoever holds the lease does the work. Whoever holds the newest token may write.*

## Why does this exist?
More and more AI work doesn't fit inside a request: a research agent that runs for twenty minutes, a nightly evaluation run, re-embedding a whole document collection after an embedding-model change (Lesson 05), a scheduled check-in with a customer. This work runs on workers, and workers crash, get redeployed, or pause for long enough to look dead.

So long-running work needs three things a function call doesn't: a **schedule** (when it should run), an **owner** (exactly one live worker at a time), and **progress that survives** (a crash at item 12,000 of 20,000 resumes at 12,000).

---

## What problem does it solve?
1. **Scheduling**: run at a time or on a period, without drifting later each day and without a storm of catch-up runs after downtime.
2. **Exclusive ownership**: two workers running the same job means double cost and conflicting writes.
3. **Crash recovery**: restart from the last completed batch, not from zero.
4. **Zombies**: a worker that was paused, not dead, wakes up after its job was reassigned and writes stale progress over the new owner's work.

---

## How would beginners implement this?
```python
while True:  # a scheduler loop inside a web process
    for job in jobs:
        if time.time() >= job["run_at"] and not job["running"]:
            job["running"] = True                 # check, then set
            for doc in job["docs"]:
                embed(doc)                        # all or nothing
            job["running"] = False
            job["run_at"] = time.time() + 86400   # next run: a day after this one finished
    time.sleep(60)
```

### Why does that fail?
1. **Check-then-set isn't atomic.** Two workers both read `running == False` and both start the job.
2. **A crash leaves `running = True` forever.** The flag never expires, so the job is stuck until someone edits the database by hand.
3. **There are no checkpoints.** A crash at 99% starts again at 0%.
4. **The schedule drifts.** Each run is scheduled from when the previous one *finished*, so a nightly 02:00 job creeps later every day.
5. **There is no defence against a paused worker.** When it wakes up it carries on writing, even though someone else now owns the job.

---

## How is this solved in production?
**1. A job store with atomic operations.** `JobStore.claim_due` finds a job that is due and has no live lease, and in one step sets the owner, the lease expiry and a new fencing token. Here a lock makes that atomic. In SQL it is a single conditional `UPDATE ... WHERE lease_expires_at < now()` (Lesson 19), and in Redis it is `SET key value NX PX ttl` (Lesson 18).

**2. Leases instead of flags.** Ownership expires on its own. If a worker dies, its job becomes claimable again `lease_s` seconds after the last heartbeat. There's no flag to clean up.

**3. A heartbeat and a checkpoint after every batch:**
```python
lease = store.claim_due(worker, lease_s)
cursor = store.get(lease.job_id).cursor              # resume from the last checkpoint
while cursor < total:
    for item in range(cursor, min(cursor + batch_size, total)):
        process_item(job_id, item)                   # idempotent: an unfinished batch is redone
    cursor = min(cursor + batch_size, total)
    store.checkpoint(lease, cursor)                  # progress survives a crash from here on
    store.heartbeat(lease, lease_s)                  # still alive: keep the lease
store.complete(lease)
```
(Shortened from `run_worker`.) A batch must finish well inside the lease. Steps that can't be split need a heartbeat from a background thread.

**4. Steps are idempotent, because resume is at-least-once.** In the demo, worker A crashes after 12 documents with its checkpoint at 10. Worker B resumes at 10, so documents 10 and 11 are embedded twice. That is harmless because embedding is an upsert by document ID. A non-idempotent step, such as sending an email, needs an idempotency key (Lesson 04).

**5. Fencing tokens stop zombies.** Every claim increments the job's token. Every checkpoint, heartbeat and completion must present the current token, and the lease must not have expired; otherwise the store raises `LeaseLost`. When worker A wakes up and tries to save progress with token 1, the write is rejected, because worker B holds token 2. If a downstream store supports conditional writes, pass the token there too.

**6. Recurring jobs are scheduled from the slot, not from completion.** The next run is the first slot after now (`next_run_at += k * every_s`), so a job that started ten minutes late still runs next at the usual time. If the scheduler was down for three nights, the missed runs are **coalesced** into one catch-up run instead of three back to back.

**Where agents fit.** A long agent run is a job: each step is a checkpointed unit (Lesson 15 persists the full state), the lease stops two workers from driving the same run, and a pause for human approval (Lesson 08) completes the step and releases the lease until the decision arrives.

---

## Design Tradeoffs

### Short vs. long leases
* **Short** (seconds): a dead worker's job is picked up quickly. Heartbeats are frequent, and a slow step or a garbage-collection pause can lose the lease mid-work.
* **Long** (minutes): fewer heartbeats and fewer false takeovers. When a worker dies, the job waits out the whole lease.
* Rule of thumb: the lease should be several times the longest gap between heartbeats.

### How often to checkpoint
* **Every item**: little repeated work after a crash, but a store write per item.
* **Every batch** (this lesson): bounded repeated work (at most one batch) and a sensible write rate.
* **Never**: simplest, but a crash repeats everything. That's acceptable only for short, cheap jobs.

### Coalesce vs. backfill missed runs
* **Coalesce** (this lesson): one catch-up run. Right when only the latest state matters, such as re-indexing or a "current quality" eval.
* **Backfill**: one run per missed slot. Right when each run covers its own time window, such as a daily report for each day. Run backfills with limited concurrency, so they don't become a storm.

---

## Failure Cases to Watch For
1. **"Running" flags without expiry**: one crash and the job is stuck forever.
2. **Non-atomic claims**: two workers run the same job.
3. **A lease shorter than a step, without heartbeats**: the job is reassigned mid-step and two workers do the same work.
4. **No fencing**: a paused worker overwrites the new owner's progress.
5. **Non-idempotent steps**: resuming re-sends the email or re-charges the card.
6. **Clock skew**: workers comparing lease times against their own clocks. Use the store's clock (`now()` in the database).
7. **Schedule drift and catch-up storms**: scheduling from completion time, or replaying every missed slot at once.
8. **Checkpoint granularity**: too rare loses hours of work, and too frequent turns the job store into the bottleneck.

---

## Try It
```sh
cd part-1-foundations/10-scheduling-long-running-tasks
python example.py            # fake clock; runs instantly
python -m pytest tests.py -q
```
What to watch for: worker B is `idle` while worker A's lease is live, then resumes at `started_at_cursor=10` once it expires. Only documents 10 and 11 are embedded twice. The zombie checkpoint is rejected with a stale token. And `nightly-eval` runs once, not three times, after three missed nights, with the next run on its usual slot.

Experiment: in `JobStore._check`, compare only the expiry time, not the token (`if job.lease_expires_at <= self._clock():`), and run the tests. `test_zombie_worker_cannot_overwrite_progress` fails. Without the token, the check reads the *new* owner's expiry, which is in the future, so the zombie's stale write is accepted.

**Next:** Lesson 11 runs many of these calls at once with `asyncio`: a concurrency limit, timeouts, cancellation, backpressure, and never blocking the event loop.
