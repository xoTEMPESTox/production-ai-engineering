"""
Lesson 10: Scheduling & Long-Running Tasks.

Work that outlives a request: a job store with due times, leases and
heartbeats so only one live worker runs a job, a checkpoint after every
batch so a crashed job resumes instead of restarting, and a fencing token
so a worker that lost its lease can't overwrite the new owner's progress.
Recurring jobs are scheduled from their slot, not from when they finished,
and missed runs are coalesced into one.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Callable, Literal, Optional


class LeaseLost(RuntimeError):
    """Raised when a worker acts on a lease that has expired or been taken over."""


@dataclass
class Job:
    id: str
    total_items: int
    next_run_at: float
    every_s: Optional[float] = None  # None = one-off
    status: Literal["scheduled", "done"] = "scheduled"
    cursor: int = 0                   # checkpoint: items before this index are done
    lease_owner: Optional[str] = None
    lease_expires_at: float = 0.0
    fencing_token: int = 0            # increments on every claim
    runs_completed: int = 0


@dataclass(frozen=True)
class Lease:
    job_id: str
    owner: str
    token: int


class JobStore:
    """In-memory and thread-safe, shaped like a database table. Each method is
    one atomic operation; Lesson 19 does the same with UPDATE ... WHERE."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def schedule(self, job_id: str, total_items: int, run_at: float, every_s: Optional[float] = None) -> None:
        with self._lock:
            self._jobs[job_id] = Job(job_id, total_items, run_at, every_s)

    def get(self, job_id: str) -> Job:
        with self._lock:
            return Job(**vars(self._jobs[job_id]))  # a copy: callers can't mutate the store

    def claim_due(self, worker: str, lease_s: float) -> Optional[Lease]:
        now = self._clock()
        with self._lock:
            for job in self._jobs.values():
                free = job.lease_owner is None or job.lease_expires_at <= now
                if job.status == "scheduled" and job.next_run_at <= now and free:
                    job.lease_owner, job.lease_expires_at = worker, now + lease_s
                    job.fencing_token += 1
                    return Lease(job.id, worker, job.fencing_token)
        return None

    def _check(self, lease: Lease) -> Job:
        job = self._jobs[lease.job_id]
        if job.fencing_token != lease.token or job.lease_expires_at <= self._clock():
            raise LeaseLost(f"{lease.owner} no longer holds {lease.job_id} (token {lease.token})")
        return job

    def heartbeat(self, lease: Lease, lease_s: float) -> None:
        with self._lock:
            self._check(lease).lease_expires_at = self._clock() + lease_s

    def checkpoint(self, lease: Lease, cursor: int) -> None:
        with self._lock:
            self._check(lease).cursor = cursor

    def complete(self, lease: Lease) -> None:
        now = self._clock()
        with self._lock:
            job = self._check(lease)
            job.runs_completed += 1
            job.cursor, job.lease_owner, job.lease_expires_at = 0, None, 0.0
            if job.every_s is None:
                job.status = "done"
            else:  # next slot after now: missed runs are coalesced, the schedule doesn't drift
                missed = math.floor((now - job.next_run_at) / job.every_s) + 1
                job.next_run_at += missed * job.every_s


@dataclass(frozen=True)
class WorkerReport:
    worker: str
    job_id: Optional[str]
    outcome: Literal["idle", "completed", "crashed", "lease_lost"]
    started_at_cursor: int = 0
    processed: int = 0


def run_worker(store: JobStore, worker: str, process_item: Callable[[str, int], None],
               batch_size: int = 5, lease_s: float = 30.0,
               crash_after_items: Optional[int] = None) -> WorkerReport:
    """Claim one due job and work through it batch by batch.
    crash_after_items simulates the process dying mid-batch (for the demo and tests)."""
    lease = store.claim_due(worker, lease_s)
    if lease is None:
        return WorkerReport(worker, None, "idle")
    job = store.get(lease.job_id)
    cursor = start = job.cursor  # resume from the last checkpoint, not from zero
    processed = 0
    try:
        while cursor < job.total_items:
            end = min(cursor + batch_size, job.total_items)
            for item in range(cursor, end):
                if crash_after_items is not None and processed == crash_after_items:
                    return WorkerReport(worker, job.id, "crashed", start, processed)  # no checkpoint, no heartbeat
                process_item(job.id, item)  # must be idempotent: an unfinished batch is redone
                processed += 1
            cursor = end
            store.checkpoint(lease, cursor)  # progress survives a crash from here on
            store.heartbeat(lease, lease_s)  # still alive: keep the lease
        store.complete(lease)
    except LeaseLost:
        return WorkerReport(worker, job.id, "lease_lost", start, processed)
    return WorkerReport(worker, job.id, "completed", start, processed)


if __name__ == "__main__":
    now = [0.0]  # a fake clock: the demo moves time by setting now[0] (a list, so lambdas see the change)
    store = JobStore(clock=lambda: now[0])
    index: dict[tuple[str, int], int] = {}  # (job, doc) -> times embedded

    def embed_document(job_id: str, doc: int) -> None:
        index[(job_id, doc)] = index.get((job_id, doc), 0) + 1  # an upsert: safe to repeat

    store.schedule("reembed-acme", total_items=23, run_at=0.0)
    print(run_worker(store, "worker-a", embed_document, crash_after_items=12))
    print("worker-b while a's lease is live:", run_worker(store, "worker-b", embed_document).outcome)

    now[0] += 31  # worker-a stopped heartbeating; its lease expires
    print(run_worker(store, "worker-b", embed_document))
    redone = sorted(doc for (_, doc), n in index.items() if n > 1)
    print(f"documents embedded: {len(index)}/23; embedded twice (unfinished batch): {redone}")

    zombie = Lease("reembed-acme", "worker-a", token=1)  # worker-a wakes up and tries to save progress
    try:
        store.checkpoint(zombie, 10)
    except LeaseLost as exc:
        print("zombie checkpoint rejected:", exc)

    day = 86_400.0
    store.schedule("nightly-eval", total_items=3, run_at=now[0], every_s=day)
    run_worker(store, "worker-c", lambda job, item: None)
    now[0] += 3.5 * day  # the scheduler was down for three nights
    run_worker(store, "worker-c", lambda job, item: None)
    nightly = store.get("nightly-eval")
    print(f"nightly-eval: runs={nightly.runs_completed}, next run in "
          f"{(nightly.next_run_at - now[0]) / 3600:.0f}h (missed nights coalesced into one run)")
