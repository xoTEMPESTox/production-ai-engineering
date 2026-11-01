"""
Lesson 17: Background Workers.

Slow AI work (parsing, embedding, summarising) moves off the request path
into a queue drained by a pool of workers. A received message is hidden for
a visibility timeout and reappears if the worker dies before acknowledging
it; long jobs extend the timeout. A message that keeps failing goes to a
dead-letter queue instead of blocking or looping forever. And work is
single-flight per entity: three uploads of the same document arriving
together start one run, plus at most one follow-up run for the latest
version, never three runs racing each other.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional


@dataclass
class Message:
    id: int
    body: dict
    receive_count: int = 0
    visible_at: float = 0.0
    last_error: str = ""


class InMemoryQueue:
    """The semantics of a managed queue: receive hides, ack deletes, no ack means
    redelivery, too many receives means dead-letter. Thread-safe."""

    def __init__(self, clock: Callable[[], float], visibility_timeout_s: float = 30.0, max_receives: int = 3) -> None:
        self._clock, self.visibility_timeout_s, self.max_receives = clock, visibility_timeout_s, max_receives
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.messages: dict[int, Message] = {}
        self.dead_letters: list[Message] = []

    def send(self, body: dict) -> int:
        with self._lock:
            message = Message(next(self._ids), dict(body))
            self.messages[message.id] = message
            return message.id

    def receive(self) -> Optional[Message]:
        now = self._clock()
        with self._lock:
            for message in sorted(self.messages.values(), key=lambda m: m.id):
                if message.visible_at > now:
                    continue
                if message.receive_count >= self.max_receives:  # poison: park it, keep the queue moving
                    self.dead_letters.append(self.messages.pop(message.id))
                    continue
                message.receive_count += 1
                message.visible_at = now + self.visibility_timeout_s
                return Message(message.id, dict(message.body), message.receive_count, message.visible_at)
        return None

    def ack(self, message_id: int) -> None:
        with self._lock:
            self.messages.pop(message_id, None)

    def fail(self, message_id: int, error: str) -> None:
        with self._lock:
            if message_id in self.messages:
                self.messages[message_id].last_error = error  # stays hidden until the timeout, then retried

    def extend(self, message_id: int, seconds: float) -> None:
        """Heartbeat for long jobs, so the message doesn't reappear mid-work."""
        with self._lock:
            if message_id in self.messages:
                self.messages[message_id].visible_at = self._clock() + seconds


class SingleFlight:
    """At most one run per entity. A request for an entity that is already running
    marks it dirty instead; when the run finishes, a dirty entity gets exactly one
    follow-up run. Across machines this is a unique row or a lock per entity (Lessons 18-19)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._dirty: set[str] = set()

    def try_start(self, entity: str) -> bool:
        with self._lock:
            if entity in self._running:
                self._dirty.add(entity)
                return False
            self._running.add(entity)
            return True

    def finish(self, entity: str, succeeded: bool) -> bool:
        """True when a follow-up run is needed. After a failure the redelivered
        message will run again anyway, so no extra run is scheduled."""
        with self._lock:
            self._running.discard(entity)
            rerun = succeeded and entity in self._dirty
            self._dirty.discard(entity)
            return rerun


Outcome = Literal["processed", "coalesced", "failed"]


@dataclass
class Worker:
    queue: InMemoryQueue
    flight: SingleFlight
    process: Callable[[str], None]  # the slow, expensive part (e.g. parse + chunk + embed a document)
    log: list[str] = field(default_factory=list)

    def handle(self, message: Message) -> Outcome:
        doc_id = message.body["doc_id"]
        if not self.flight.try_start(doc_id):
            self.queue.ack(message.id)  # absorbed by the run in progress (which will re-run once)
            self.log.append(f"msg {message.id} {doc_id}: coalesced into the running job")
            return "coalesced"
        succeeded = False
        try:
            self.process(doc_id)
            self.queue.ack(message.id)
            succeeded = True
            self.log.append(f"msg {message.id} {doc_id}: processed")
            return "processed"
        except Exception as exc:  # no ack: the queue redelivers after the timeout, or dead-letters it
            self.queue.fail(message.id, f"{type(exc).__name__}: {exc}")
            self.log.append(f"msg {message.id} {doc_id}: failed (receive {message.receive_count})")
            return "failed"
        finally:
            if self.flight.finish(doc_id, succeeded):
                self.queue.send({"doc_id": doc_id, "reason": "changed while processing"})


def run_pool(worker: Worker, threads: int = 3) -> None:
    """Drain everything currently visible with a pool of threads."""
    def loop() -> None:
        while (message := worker.queue.receive()) is not None:
            worker.handle(message)

    pool = [threading.Thread(target=loop) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()


if __name__ == "__main__":
    now = [0.0]  # a fake clock: the demo moves time by setting now[0] (a list, so lambdas see the change)
    queue = InMemoryQueue(clock=lambda: now[0], visibility_timeout_s=30, max_receives=3)
    runs: Counter = Counter()

    def embed_document(doc_id: str) -> None:
        if doc_id == "doc-corrupt":
            raise ValueError("PDF has no text layer")
        runs[doc_id] += 1
        time.sleep(0.05)  # pretend to parse and embed

    for doc_id in ["doc-1", "doc-1", "doc-1", "doc-2", "doc-corrupt"]:  # three uploads of doc-1 at once
        queue.send({"doc_id": doc_id})
    worker = Worker(queue, SingleFlight(), embed_document)

    for round_number in range(4):
        run_pool(worker, threads=3)
        now[0] += 31  # failed messages become visible again after the timeout
    for line in worker.log:
        print(line)
    print(f"\nembedding runs: {dict(runs)} (doc-1: one run plus one follow-up, not three)")
    print(f"dead letters: {[(m.body['doc_id'], m.receive_count, m.last_error) for m in queue.dead_letters]}")
    print(f"left in queue: {len(queue.messages)}")
