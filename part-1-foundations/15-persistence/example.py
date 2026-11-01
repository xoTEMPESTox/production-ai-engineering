"""
Lesson 15: Persistence.

An agent run that survives restarts. After every step the run's state is
written to SQLite as JSON with a schema version, so a crashed run resumes
from its last checkpoint without repeating model calls, and any earlier
step can be reloaded. Checkpoints written by older code are migrated step by
step before validation; a checkpoint from newer code is refused instead of
silently losing fields. Writes are atomic, a step can be written only once,
and oversized state is rejected.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Callable, Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 3
MAX_STATE_BYTES = 64_000  # large payloads belong in blob storage, referenced by id


class Message(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str


class RunState(BaseModel):
    """Schema v3. v1 kept 'history' as plain strings; v2 called the plan 'todo'."""
    model_config = ConfigDict(extra="forbid")
    run_id: str
    task: str
    step: int = 0
    messages: list[Message] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)
    tokens_used: int = 0


class SchemaTooNew(ValueError):
    pass


class CheckpointConflict(ValueError):
    pass


# --- Migrations: one function per version bump, applied in order ---

def _v1_to_v2(data: dict) -> dict:
    """v1 stored history as alternating user/assistant strings."""
    out = {k: v for k, v in data.items() if k != "history"}
    out["messages"] = [{"role": "user" if i % 2 == 0 else "assistant", "content": text}
                       for i, text in enumerate(data.get("history", []))]
    return out


def _v2_to_v3(data: dict) -> dict:
    """v3 renamed 'todo' to 'plan' and started counting tokens."""
    out = {k: v for k, v in data.items() if k != "todo"}
    out["plan"] = data.get("todo", [])
    out.setdefault("tokens_used", 0)
    return out


MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: _v1_to_v2, 2: _v2_to_v3}


def migrate(data: dict, version: int) -> dict:
    if version > SCHEMA_VERSION:
        raise SchemaTooNew(f"checkpoint has schema v{version}; this code understands up to v{SCHEMA_VERSION}")
    while version < SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
    return data


class CheckpointStore:
    def __init__(self, path: str = ":memory:") -> None:
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS checkpoints (
            run_id TEXT NOT NULL, step INTEGER NOT NULL, schema_version INTEGER NOT NULL,
            state TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY (run_id, step))""")

    def save(self, state: RunState) -> None:
        payload = state.model_dump_json()
        if len(payload.encode()) > MAX_STATE_BYTES:
            raise ValueError(f"state is {len(payload.encode())} bytes; store large data by reference")
        try:
            with self.db:  # atomic: the whole checkpoint or nothing
                self.db.execute("INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?)",
                                (state.run_id, state.step, SCHEMA_VERSION, payload, time.time()))
        except sqlite3.IntegrityError:
            raise CheckpointConflict(f"{state.run_id} step {state.step} was already written") from None

    def load(self, run_id: str, step: Optional[int] = None) -> Optional[RunState]:
        """The latest checkpoint, or a specific step (rewind). Migrated, then validated."""
        query = "SELECT schema_version, state FROM checkpoints WHERE run_id = ?"
        params: tuple = (run_id,)
        if step is not None:
            query, params = query + " AND step = ?", (run_id, step)
        row = self.db.execute(query + " ORDER BY step DESC LIMIT 1", params).fetchone()
        if row is None:
            return None
        version, payload = row
        return RunState.model_validate(migrate(json.loads(payload), version))

    def steps(self, run_id: str) -> list[int]:
        return [r[0] for r in self.db.execute("SELECT step FROM checkpoints WHERE run_id = ? ORDER BY step", (run_id,))]

    def close(self) -> None:
        self.db.close()


# --- A run that checkpoints after every step ---

class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class CountingLLM:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return f"result of step {prompt.split()[-1]}"


class Crash(RuntimeError):
    pass


def run_agent(llm: LLMClient, store: CheckpointStore, run_id: str, task: str, total_steps: int,
              crash_after_step: Optional[int] = None) -> RunState:
    state = store.load(run_id) or RunState(run_id=run_id, task=task, plan=[f"step {i + 1}" for i in range(total_steps)])
    while state.step < total_steps:  # resumes where the last checkpoint left off
        reply = llm.complete(f"{task}: do step {state.step + 1}")
        state = state.model_copy(update={
            "step": state.step + 1,
            "messages": state.messages + [Message(role="assistant", content=reply)],
            "tokens_used": state.tokens_used + 120,
        })
        store.save(state)  # checkpoint after every step
        if crash_after_step == state.step:
            raise Crash(f"process died after step {state.step}")
    return state


if __name__ == "__main__":
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "runs.db")
        llm = CountingLLM()
        first = CheckpointStore(path)
        try:
            run_agent(llm, first, "run-7", "reconcile invoices", total_steps=5, crash_after_step=3)
        except Crash as exc:
            print("crash:", exc)
            first.close()  # the dead process's connection is gone

        store = CheckpointStore(path)  # a new process opens the same file
        final = run_agent(llm, store, "run-7", "reconcile invoices", total_steps=5)
        print(f"resumed and finished: step {final.step}, model calls {llm.calls} (not 8), "
              f"checkpoints {store.steps('run-7')}")
        print("rewind to step 2:", [m.content for m in store.load("run-7", step=2).messages])

        old = {"run_id": "run-1", "task": "summarise", "step": 2, "history": ["hi", "hello"], "todo": ["reply"]}
        with store.db:
            store.db.execute("INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?)", ("run-1", 2, 1, json.dumps(old), 0.0))
        print("v1 checkpoint loaded as v3:", store.load("run-1").model_dump())

        with store.db:
            store.db.execute("INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?)", ("run-9", 1, 4, "{}", 0.0))
        try:
            store.load("run-9")
        except SchemaTooNew as exc:
            print("refused:", exc)
        store.close()
