"""
Lesson 12: Event-Driven Agents.

Agents that react to events instead of requests. The producer writes the
change and its event in one database transaction (a transactional outbox),
so an event can't be lost or invented. A relay publishes outbox rows and
may publish one twice if it crashes, so delivery is at-least-once. The
consumer, an agent that re-indexes and summarises the changed document,
is idempotent: it records each event id in the same transaction as its
effect, skips duplicates, and ignores versions older than the one it has.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable, Literal


@dataclass(frozen=True)
class Event:
    event_id: str   # the dedup key: same change -> same id, however often it's delivered
    type: str
    doc_id: str
    version: int
    text: str


class DocumentService:
    """The producer. Its database holds the documents and the outbox."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE documents (id TEXT PRIMARY KEY, text TEXT NOT NULL, version INTEGER NOT NULL);
            CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                                 payload TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
        """)

    def update_document(self, doc_id: str, text: str, fail_after_write: bool = False) -> int:
        """Write the change and its event atomically: both commit or neither does."""
        with self.db:  # one transaction
            row = self.db.execute("SELECT version FROM documents WHERE id = ?", (doc_id,)).fetchone()
            version = (row[0] if row else 0) + 1
            self.db.execute("INSERT INTO documents VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE "
                            "SET text = excluded.text, version = excluded.version", (doc_id, text, version))
            event = Event(f"doc-{doc_id}-v{version}", "document.updated", doc_id, version, text)
            self.db.execute("INSERT INTO outbox (event_id, payload) VALUES (?, ?)",
                            (event.event_id, json.dumps(vars(event))))
            if fail_after_write:
                raise RuntimeError("crash before commit")  # rolls back the document AND the event
        return version


class Broker:
    """In-memory stand-in for a message broker with at-least-once delivery."""

    def __init__(self) -> None:
        self.pending: list[Event] = []

    def publish(self, event: Event) -> None:
        self.pending.append(event)

    def drain(self) -> list[Event]:
        events, self.pending = self.pending, []
        return events


def relay_once(service: DocumentService, broker: Broker, crash_before_marking: bool = False) -> int:
    """Publish unsent outbox rows in order, then mark them sent. A crash between
    the two means the next relay publishes them again: at-least-once."""
    rows = service.db.execute("SELECT seq, payload FROM outbox WHERE sent = 0 ORDER BY seq").fetchall()
    for _, payload in rows:
        broker.publish(Event(**json.loads(payload)))
    if crash_before_marking:
        return len(rows)
    with service.db:
        service.db.executemany("UPDATE outbox SET sent = 1 WHERE seq = ?", [(seq,) for seq, _ in rows])
    return len(rows)


Outcome = Literal["applied", "duplicate", "stale"]


class ReindexAgent:
    """The consumer: re-indexes the document and asks a model to summarise it.
    Its effect and its 'processed' marker commit in the same transaction."""

    def __init__(self, summarize: Callable[[str], str]) -> None:
        self.summarize = summarize
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE search_index (doc_id TEXT PRIMARY KEY, text TEXT, summary TEXT, version INTEGER);
            CREATE TABLE processed_events (event_id TEXT PRIMARY KEY);
        """)

    def handle(self, event: Event, crash_before_commit: bool = False) -> Outcome:
        with self.db:
            if self.db.execute("SELECT 1 FROM processed_events WHERE event_id = ?", (event.event_id,)).fetchone():
                return "duplicate"
            row = self.db.execute("SELECT version FROM search_index WHERE doc_id = ?", (event.doc_id,)).fetchone()
            self.db.execute("INSERT INTO processed_events VALUES (?)", (event.event_id,))
            if row and row[0] >= event.version:
                return "stale"  # an older version arrived late: never overwrite newer data
            summary = self.summarize(event.text)  # the expensive part, done once per event
            self.db.execute("INSERT INTO search_index VALUES (?, ?, ?, ?) ON CONFLICT(doc_id) DO UPDATE SET "
                            "text = excluded.text, summary = excluded.summary, version = excluded.version",
                            (event.doc_id, event.text, summary, event.version))
            if crash_before_commit:
                raise RuntimeError("consumer crashed mid-transaction")  # effect and marker roll back together
        return "applied"

    def indexed(self, doc_id: str) -> tuple[str, int] | None:
        return self.db.execute("SELECT summary, version FROM search_index WHERE doc_id = ?", (doc_id,)).fetchone()


if __name__ == "__main__":
    calls: list[str] = []

    def summarize(text: str) -> str:  # stands in for an LLM call that costs money
        calls.append(text)
        return f"summary: {text[:40]}"

    service, broker, agent = DocumentService(), Broker(), ReindexAgent(summarize)

    service.update_document("refund-policy", "Annual plans are refundable within 30 days.")
    try:
        service.update_document("refund-policy", "Typo draft", fail_after_write=True)
    except RuntimeError:
        print("failed update rolled back; outbox rows:", service.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])
    service.update_document("refund-policy", "Annual plans are refundable within 45 days.")

    relay_once(service, broker, crash_before_marking=True)  # published, then the relay crashed
    relay_once(service, broker)                             # restarted relay publishes them again
    events = broker.drain()
    print(f"delivered: {[e.event_id for e in events]}")

    for event in [events[1], *events]:  # v2 arrives before v1, and everything twice
        print(f"  {event.event_id}: {agent.handle(event)}")
    print(f"index: {agent.indexed('refund-policy')}; summaries paid for: {len(calls)}")
