"""
Lesson 18: Redis Checkpointing.

Redis as the fast, short-lived layer for run state: namespaced keys built
in one place, checkpoints that expire on their own, and a per-run lock taken
with SET NX PX and a random token. Releasing or extending the lock is a
compare-and-act on that token (a Lua script on real Redis), so a worker
whose lock already expired can't delete or extend the new owner's lock.
An in-memory fake with the same semantics is the default; a thin adapter
over redis-py is used only when PAIE_REDIS_URL is set.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from typing import Any, Callable, Optional, Protocol


class KVStore(Protocol):
    """The handful of Redis operations this lesson needs."""

    def set(self, key: str, value: str, px: Optional[int] = None, nx: bool = False) -> bool: ...
    def get(self, key: str) -> Optional[str]: ...
    def pttl(self, key: str) -> int: ...  # ms left; -1 = no expiry; -2 = no such key
    def delete_if_equals(self, key: str, value: str) -> bool: ...
    def pexpire_if_equals(self, key: str, value: str, px: int) -> bool: ...


class FakeRedis:
    """In-memory, thread-safe, with expiry driven by an injectable clock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._data: dict[str, tuple[str, Optional[float]]] = {}  # key -> (value, expires_at)

    def _live(self, key: str) -> Optional[tuple[str, Optional[float]]]:
        entry = self._data.get(key)
        if entry and entry[1] is not None and entry[1] <= self._clock():
            del self._data[key]  # expired keys behave as missing
            return None
        return entry

    def set(self, key: str, value: str, px: Optional[int] = None, nx: bool = False) -> bool:
        with self._lock:
            if nx and self._live(key) is not None:
                return False
            self._data[key] = (value, self._clock() + px / 1000 if px else None)
            return True

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            entry = self._live(key)
            return entry[0] if entry else None

    def pttl(self, key: str) -> int:
        with self._lock:
            entry = self._live(key)
            if entry is None:
                return -2
            return -1 if entry[1] is None else int((entry[1] - self._clock()) * 1000)

    def delete_if_equals(self, key: str, value: str) -> bool:
        with self._lock:  # atomic, like the Lua script below
            entry = self._live(key)
            if entry is None or entry[0] != value:
                return False
            del self._data[key]
            return True

    def pexpire_if_equals(self, key: str, value: str, px: int) -> bool:
        with self._lock:
            entry = self._live(key)
            if entry is None or entry[0] != value:
                return False
            self._data[key] = (value, self._clock() + px / 1000)
            return True


DELETE_IF_EQUALS = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
PEXPIRE_IF_EQUALS = ("if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) "
                     "else return 0 end")


class RedisAdapter:
    """The same interface over a real server. Compare-and-act runs as Lua, so it's atomic."""

    def __init__(self, url: str) -> None:
        import redis  # only needed when a real server is configured

        self._client = redis.Redis.from_url(url, decode_responses=True)

    def set(self, key: str, value: str, px: Optional[int] = None, nx: bool = False) -> bool:
        return bool(self._client.set(key, value, px=px, nx=nx))

    def get(self, key: str) -> Optional[str]:
        return self._client.get(key)

    def pttl(self, key: str) -> int:
        return int(self._client.pttl(key))

    def delete_if_equals(self, key: str, value: str) -> bool:
        return bool(self._client.eval(DELETE_IF_EQUALS, 1, key, value))

    def pexpire_if_equals(self, key: str, value: str, px: int) -> bool:
        return bool(self._client.eval(PEXPIRE_IF_EQUALS, 1, key, value, px))


# --- Key design: one function builds every key ---

RUN_ID = re.compile(r"^[a-z0-9-]{1,64}$")


class Keys:
    """paie:{env}:v1:run:{<run_id>}:... The braces are a Redis Cluster hash tag:
    every key of one run lands in the same slot. The version lets a new state
    format coexist with the old one during a deploy."""

    def __init__(self, env: str, version: str = "v1") -> None:
        self.prefix = f"paie:{env}:{version}"

    def _run(self, run_id: str) -> str:
        if not RUN_ID.match(run_id):  # never build keys from raw user input
            raise ValueError(f"invalid run id {run_id!r}")
        return f"{self.prefix}:run:{{{run_id}}}"

    def checkpoint(self, run_id: str, step: int) -> str:
        return f"{self._run(run_id)}:checkpoint:{step}"

    def latest(self, run_id: str) -> str:
        return f"{self._run(run_id)}:checkpoint:latest"

    def lock(self, run_id: str) -> str:
        return f"{self._run(run_id)}:lock"


class RunLock:
    def __init__(self, store: KVStore, keys: Keys) -> None:
        self._store, self._keys = store, keys

    def acquire(self, run_id: str, ttl_ms: int) -> Optional[str]:
        token = secrets.token_hex(16)  # proves ownership on release
        return token if self._store.set(self._keys.lock(run_id), token, px=ttl_ms, nx=True) else None

    def refresh(self, run_id: str, token: str, ttl_ms: int) -> bool:
        return self._store.pexpire_if_equals(self._keys.lock(run_id), token, ttl_ms)

    def release(self, run_id: str, token: str) -> bool:
        return self._store.delete_if_equals(self._keys.lock(run_id), token)


class CheckpointStore:
    """Hot checkpoints with a TTL. Durable history belongs in a database (Lesson 19)."""

    def __init__(self, store: KVStore, keys: Keys, ttl_ms: int = 7 * 24 * 3600 * 1000) -> None:
        self._store, self._keys, self._ttl = store, keys, ttl_ms

    def save(self, run_id: str, step: int, state: dict[str, Any]) -> None:
        payload = json.dumps({"step": step, "state": state})
        self._store.set(self._keys.checkpoint(run_id, step), payload, px=self._ttl)
        self._store.set(self._keys.latest(run_id), payload, px=self._ttl)  # real Redis: one MULTI or Lua

    def load(self, run_id: str, step: Optional[int] = None) -> Optional[dict[str, Any]]:
        key = self._keys.latest(run_id) if step is None else self._keys.checkpoint(run_id, step)
        raw = self._store.get(key)
        return json.loads(raw) if raw else None


if __name__ == "__main__":
    now = [0.0]  # a fake clock: the demo moves time by setting now[0] (a list, so lambdas see the change)
    store = FakeRedis(clock=lambda: now[0])
    keys = Keys(env="dev")
    locks, checkpoints = RunLock(store, keys), CheckpointStore(store, keys)

    print("lock key:", keys.lock("run-7"))
    token_a = locks.acquire("run-7", ttl_ms=30_000)
    print("worker A acquires:", bool(token_a), "| worker B acquires:", bool(locks.acquire("run-7", 30_000)))
    for step in (1, 2):
        checkpoints.save("run-7", step, {"messages": step * 2, "plan": ["fetch", "summarise"]})
    print("latest checkpoint:", checkpoints.load("run-7"), "| ttl days:",
          round(store.pttl(keys.latest("run-7")) / 86_400_000, 1))

    now[0] += 31  # worker A stalls (GC pause, network) past its lock TTL
    token_b = locks.acquire("run-7", ttl_ms=30_000)
    print("\nafter A's TTL: worker B acquires:", bool(token_b), "and resumes from step",
          checkpoints.load("run-7")["step"])
    print("A wakes and releases with its old token:", locks.release("run-7", token_a),
          "| B still holds the lock:", store.get(keys.lock("run-7")) == token_b)
    print("A tries to refresh:", locks.refresh("run-7", token_a, 30_000))

    now[0] += 8 * 24 * 3600  # a week later
    print("\ncheckpoint after the TTL:", checkpoints.load("run-7"))
