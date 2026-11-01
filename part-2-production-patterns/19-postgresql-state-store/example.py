"""
Lesson 19: PostgreSQL State Store.

Durable run state in a relational database: a runs table holding the
current state as JSON with a version column, a steps table (one row per
step, written once), and an append-only events table for audit. Updates
use optimistic concurrency (UPDATE ... WHERE version = expected), so two
workers editing the same run can't silently overwrite each other. Jobs are
claimed atomically, so two workers never take the same one; on PostgreSQL
that's FOR UPDATE SKIP LOCKED. SQLite is the default; the PostgreSQL
adapter is used only when PAIE_POSTGRES_URL is set.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Optional, Protocol


class StaleWrite(Exception):
    """Someone else updated the run after you read it. Reload, re-apply, retry."""


@dataclass(frozen=True)
class Run:
    id: str
    tenant: str
    status: str
    state: dict[str, Any]
    version: int


class StateStore(Protocol):
    def create_run(self, run_id: str, tenant: str, state: dict[str, Any]) -> Run: ...
    def get_run(self, run_id: str) -> Optional[Run]: ...
    def update_run(self, run_id: str, expected_version: int, state: dict[str, Any], status: str) -> Run: ...
    def add_step(self, run_id: str, step: int, kind: str, payload: dict[str, Any]) -> None: ...
    def events(self, run_id: str) -> list[tuple[str, dict[str, Any]]]: ...
    def find_runs(self, tenant: str, field: str, value: str) -> list[str]: ...
    def enqueue(self, run_id: str) -> int: ...
    def claim_job(self, worker: str) -> Optional[tuple[int, str]]: ...


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, tenant TEXT NOT NULL, status TEXT NOT NULL,
                                 state TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS steps (run_id TEXT NOT NULL REFERENCES runs(id), step INTEGER NOT NULL,
                                  kind TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (run_id, step));
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                                   type TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                                 status TEXT NOT NULL DEFAULT 'queued', claimed_by TEXT);
"""


class SqliteStateStore:
    """The default. Same tables and statements as PostgreSQL, minus JSONB and row locks."""

    def __init__(self, path: str = ":memory:") -> None:
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)  # explicit BEGINs
        self.db.executescript(SQLITE_SCHEMA)
        self._lock = threading.Lock()  # one connection shared by threads in the demo and tests

    def _tx(self, statements: list[tuple[str, tuple]]) -> list[sqlite3.Cursor]:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")  # take the write lock up front
            try:
                cursors = [self.db.execute(sql, params) for sql, params in statements]
                self.db.execute("COMMIT")
                return cursors
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def create_run(self, run_id: str, tenant: str, state: dict[str, Any]) -> Run:
        self._tx([("INSERT INTO runs VALUES (?, ?, 'running', ?, 1)", (run_id, tenant, json.dumps(state))),
                  ("INSERT INTO events (run_id, type, payload) VALUES (?, 'run.created', ?)", (run_id, "{}"))])
        return Run(run_id, tenant, "running", state, 1)

    def get_run(self, run_id: str) -> Optional[Run]:
        with self._lock:
            row = self.db.execute("SELECT id, tenant, status, state, version FROM runs WHERE id = ?",
                                  (run_id,)).fetchone()
        return Run(row[0], row[1], row[2], json.loads(row[3]), row[4]) if row else None

    def update_run(self, run_id: str, expected_version: int, state: dict[str, Any], status: str) -> Run:
        event = json.dumps({"from_version": expected_version, "status": status})
        update, _ = self._tx([
            ("UPDATE runs SET state = ?, status = ?, version = version + 1 WHERE id = ? AND version = ?",
             (json.dumps(state), status, run_id, expected_version)),
            ("INSERT INTO events (run_id, type, payload) SELECT ?, 'run.updated', ? WHERE changes() = 1",
             (run_id, event)),
        ])
        if update.rowcount != 1:  # zero rows: the version moved on since we read it
            raise StaleWrite(f"{run_id} is no longer at version {expected_version}")
        run = self.get_run(run_id)
        assert run is not None
        return run

    def add_step(self, run_id: str, step: int, kind: str, payload: dict[str, Any]) -> None:
        self._tx([("INSERT INTO steps VALUES (?, ?, ?, ?)", (run_id, step, kind, json.dumps(payload)))])

    def events(self, run_id: str) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            rows = self.db.execute("SELECT type, payload FROM events WHERE run_id = ? ORDER BY id", (run_id,))
            return [(t, json.loads(p)) for t, p in rows.fetchall()]

    def find_runs(self, tenant: str, field: str, value: str) -> list[str]:
        """Query inside the JSON state; tenant first, always (Lesson 05's rule, in SQL)."""
        with self._lock:
            rows = self.db.execute("SELECT id FROM runs WHERE tenant = ? AND json_extract(state, ?) = ? ORDER BY id",
                                   (tenant, f"$.{field}", value))
            return [r[0] for r in rows.fetchall()]

    def enqueue(self, run_id: str) -> int:
        (cursor,) = self._tx([("INSERT INTO jobs (run_id) VALUES (?)", (run_id,))])
        return int(cursor.lastrowid)

    def claim_job(self, worker: str) -> Optional[tuple[int, str]]:
        """Atomic claim: the write lock serialises claimers here. PostgreSQL does it per row (see CLAIM_SQL)."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT id, run_id FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE jobs SET status = 'claimed', claimed_by = ? WHERE id = ?", (worker, row[0]))
            self.db.execute("COMMIT")
        return (row[0], row[1]) if row else None


# --- PostgreSQL: the same interface. Used only when PAIE_POSTGRES_URL is set. ---

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id text PRIMARY KEY, tenant text NOT NULL, status text NOT NULL,
                                 state jsonb NOT NULL, version integer NOT NULL);
CREATE TABLE IF NOT EXISTS steps (run_id text NOT NULL REFERENCES runs(id), step integer NOT NULL,
                                  kind text NOT NULL, payload jsonb NOT NULL, PRIMARY KEY (run_id, step));
CREATE TABLE IF NOT EXISTS events (id bigserial PRIMARY KEY, run_id text NOT NULL, type text NOT NULL,
                                   payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS jobs (id bigserial PRIMARY KEY, run_id text NOT NULL,
                                 status text NOT NULL DEFAULT 'queued', claimed_by text);
CREATE INDEX IF NOT EXISTS runs_tenant ON runs (tenant);
CREATE INDEX IF NOT EXISTS runs_state ON runs USING gin (state);  -- serves state @> '{...}' lookups
"""

CLAIM_SQL = """
UPDATE jobs SET status = 'claimed', claimed_by = $1
WHERE id = (SELECT id FROM jobs WHERE status = 'queued' ORDER BY id
            FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, run_id
"""


class PostgresStateStore:
    """asyncpg behind a synchronous facade (one private event loop, one connection)."""

    def __init__(self, dsn: str, schema: str = "public") -> None:
        import asyncpg  # only needed when a real server is configured

        if not schema.replace("_", "").isalnum():
            raise ValueError(f"invalid schema name {schema!r}")
        self._loop = asyncio.new_event_loop()
        self._conn = self._run(asyncpg.connect(dsn))
        self._run(self._conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}; SET search_path TO {schema};"))
        self._run(self._conn.execute(POSTGRES_SCHEMA))

    def _run(self, coroutine: Any) -> Any:
        return self._loop.run_until_complete(coroutine)

    def create_run(self, run_id: str, tenant: str, state: dict[str, Any]) -> Run:
        async def go() -> None:
            async with self._conn.transaction():
                await self._conn.execute("INSERT INTO runs VALUES ($1, $2, 'running', $3::jsonb, 1)",
                                         run_id, tenant, json.dumps(state))
                await self._conn.execute("INSERT INTO events (run_id, type, payload) VALUES ($1, 'run.created', '{}')",
                                         run_id)
        self._run(go())
        return Run(run_id, tenant, "running", state, 1)

    def get_run(self, run_id: str) -> Optional[Run]:
        row = self._run(self._conn.fetchrow("SELECT id, tenant, status, state::text, version FROM runs WHERE id = $1",
                                            run_id))
        return Run(row[0], row[1], row[2], json.loads(row[3]), row[4]) if row else None

    def update_run(self, run_id: str, expected_version: int, state: dict[str, Any], status: str) -> Run:
        async def go() -> str:
            async with self._conn.transaction():
                result = await self._conn.execute(
                    "UPDATE runs SET state = $1::jsonb, status = $2, version = version + 1 "
                    "WHERE id = $3 AND version = $4", json.dumps(state), status, run_id, expected_version)
                if result == "UPDATE 1":
                    await self._conn.execute("INSERT INTO events (run_id, type, payload) VALUES ($1, 'run.updated', $2::jsonb)",
                                             run_id, json.dumps({"from_version": expected_version, "status": status}))
                return result
        if self._run(go()) != "UPDATE 1":
            raise StaleWrite(f"{run_id} is no longer at version {expected_version}")
        run = self.get_run(run_id)
        assert run is not None
        return run

    def add_step(self, run_id: str, step: int, kind: str, payload: dict[str, Any]) -> None:
        self._run(self._conn.execute("INSERT INTO steps VALUES ($1, $2, $3, $4::jsonb)",
                                     run_id, step, kind, json.dumps(payload)))

    def events(self, run_id: str) -> list[tuple[str, dict[str, Any]]]:
        rows = self._run(self._conn.fetch("SELECT type, payload::text FROM events WHERE run_id = $1 ORDER BY id",
                                          run_id))
        return [(r[0], json.loads(r[1])) for r in rows]

    def find_runs(self, tenant: str, field: str, value: str) -> list[str]:
        rows = self._run(self._conn.fetch("SELECT id FROM runs WHERE tenant = $1 AND state @> $2::jsonb ORDER BY id",
                                          tenant, json.dumps({field: value})))
        return [r[0] for r in rows]

    def claim_job(self, worker: str) -> Optional[tuple[int, str]]:
        row = self._run(self._conn.fetchrow(CLAIM_SQL, worker))
        return (row[0], row[1]) if row else None

    def enqueue(self, run_id: str) -> int:
        return self._run(self._conn.fetchval("INSERT INTO jobs (run_id) VALUES ($1) RETURNING id", run_id))


def update_with_retry(store: StateStore, run_id: str, change: Any, attempts: int = 3) -> Run:
    """The optimistic-concurrency loop: read, apply the change to fresh state, write if unchanged."""
    for _ in range(attempts):
        run = store.get_run(run_id)
        assert run is not None
        try:
            return store.update_run(run_id, run.version, change(dict(run.state)), run.status)
        except StaleWrite:
            continue  # someone else won the race: re-read and re-apply
    raise StaleWrite(f"{run_id}: still conflicting after {attempts} attempts")


if __name__ == "__main__":
    store = SqliteStateStore()
    store.create_run("run-7", "acme", {"customer_id": "cus-17", "notes": []})
    store.create_run("run-8", "acme", {"customer_id": "cus-99", "notes": []})

    a, b = store.get_run("run-7"), store.get_run("run-7")  # two workers read version 1
    store.update_run("run-7", a.version, {**a.state, "notes": ["refund approved"]}, "running")
    try:
        store.update_run("run-7", b.version, {**b.state, "notes": ["customer called"]}, "running")
    except StaleWrite as exc:
        print("blind overwrite refused:", exc)
    final = update_with_retry(store, "run-7", lambda s: {**s, "notes": s["notes"] + ["customer called"]})
    print(f"after retry: version {final.version}, notes {final.state['notes']}")

    store.add_step("run-7", 1, "tool_call", {"tool": "issue_refund"})
    try:
        store.add_step("run-7", 1, "tool_call", {"tool": "issue_refund"})
    except sqlite3.IntegrityError:
        print("step 1 can only be written once")
    print("events:", [t for t, _ in store.events("run-7")])
    print("acme runs for cus-17:", store.find_runs("acme", "customer_id", "cus-17"),
          "| globex:", store.find_runs("globex", "customer_id", "cus-17"))

    for run_id in ("run-7", "run-8", "run-7"):
        store.enqueue(run_id)
    claims: list = []
    workers = [threading.Thread(target=lambda w=w: claims.append((w, store.claim_job(w)))) for w in "ABCD"]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    print("claims:", sorted((w, c) for w, c in claims))
