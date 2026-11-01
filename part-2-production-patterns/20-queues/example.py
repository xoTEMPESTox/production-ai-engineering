"""
Lesson 20: Queues.

A partitioned log, the model behind most event streams. Records with the
same key (here, a conversation) always go to the same partition, so they're
processed in order; different keys spread across partitions, so they're
processed in parallel. A consumer group gives each partition to exactly one
consumer and moves partitions when members join or leave. Offsets are
committed after processing, so delivery is at-least-once and consumers must
be idempotent. A record that keeps failing is dead-lettered so its
partition keeps moving.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Record:
    partition: int
    offset: int
    key: str
    value: dict

    @property
    def record_id(self) -> str:
        return f"{self.partition}:{self.offset}"  # unique and stable: the dedup key


class PartitionedLog:
    def __init__(self, partitions: int = 4) -> None:
        self._partitions: list[list[Record]] = [[] for _ in range(partitions)]

    @property
    def partition_count(self) -> int:
        return len(self._partitions)

    def partition_for(self, key: str) -> int:
        """A stable hash (not Python's hash(), which changes between processes)."""
        return zlib.crc32(key.encode()) % len(self._partitions)

    def append(self, key: str, value: dict) -> Record:
        partition = self.partition_for(key)
        record = Record(partition, len(self._partitions[partition]), key, dict(value))
        self._partitions[partition].append(record)
        return record

    def read(self, partition: int, offset: int, max_records: int = 100) -> list[Record]:
        return self._partitions[partition][offset:offset + max_records]


@dataclass
class ConsumerGroup:
    log: PartitionedLog
    committed: dict[int, int] = field(default_factory=dict)  # partition -> next offset to process
    assignment: dict[str, list[int]] = field(default_factory=dict)

    def rebalance(self, members: list[str]) -> dict[str, list[int]]:
        """Every partition to exactly one member, spread round-robin. Progress lives in
        `committed`, not in the consumer, so a partition can move without losing its place."""
        ordered = sorted(members)
        self.assignment = {m: [p for p in range(self.log.partition_count) if p % len(ordered) == i]
                           for i, m in enumerate(ordered)}
        return self.assignment

    def commit(self, partition: int, next_offset: int) -> None:
        self.committed[partition] = max(self.committed.get(partition, 0), next_offset)


@dataclass
class DeadLetter:
    record: Record
    error: str
    attempts: int


@dataclass
class Consumer:
    name: str
    group: ConsumerGroup
    handler: Callable[[Record], None]
    max_attempts: int = 3
    dead_letters: list[DeadLetter] = field(default_factory=list)

    def poll(self, max_records: int = 100, crash_after: int = -1) -> int:
        """Process this consumer's partitions in order. crash_after simulates dying
        right after handling the N-th record, before its offset is committed."""
        processed = 0
        for partition in self.group.assignment.get(self.name, []):
            for record in self.group.log.read(partition, self.group.committed.get(partition, 0), max_records):
                self._handle(record)
                processed += 1
                if processed == crash_after:
                    return processed  # handled but not committed: it will be delivered again
                self.group.commit(partition, record.offset + 1)  # commit only after processing
        return processed

    def _handle(self, record: Record) -> None:
        for attempt in range(1, self.max_attempts + 1):
            try:
                self.handler(record)
                return
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        # Still failing: park it and move on, or this partition is stuck for every later record.
        self.dead_letters.append(DeadLetter(record, error, self.max_attempts))


class ConversationAgent:
    """The consumer's business logic: apply each conversation's events in order, once."""

    def __init__(self) -> None:
        self.carts: dict[str, list[str]] = {}
        self.seen: set[str] = set()  # record ids already applied (a table in production)
        self.skipped: list[str] = []  # redeliveries absorbed by the check below
        self.order_by_key: dict[str, list[int]] = {}

    def __call__(self, record: Record) -> None:
        if record.record_id in self.seen:
            self.skipped.append(record.record_id)
            return  # redelivered after a crash: already applied
        if record.value.get("corrupt"):
            raise ValueError("cannot parse event payload")
        cart = self.carts.setdefault(record.key, [])
        action, item = record.value["action"], record.value.get("item")
        if action == "add":
            cart.append(item)
        elif action == "remove" and item in cart:
            cart.remove(item)
        self.seen.add(record.record_id)
        self.order_by_key.setdefault(record.key, []).append(record.value["seq"])


if __name__ == "__main__":
    log = PartitionedLog(partitions=4)
    events = [("conv-a", "add", "laptop"), ("conv-e", "add", "dock"), ("conv-a", "add", "bag"),
              ("conv-d", "add", "mouse"), ("conv-a", "remove", "laptop"), ("conv-d", "add", "pad")]
    for seq, (conv, action, item) in enumerate(events):
        log.append(conv, {"seq": seq, "action": action, "item": item})
    log.append("conv-e", {"seq": 6, "action": "add", "corrupt": True})
    log.append("conv-e", {"seq": 7, "action": "add", "item": "cable"})
    print("partition per conversation:", {k: log.partition_for(k) for k in ("conv-a", "conv-d", "conv-e")})

    group, agent = ConsumerGroup(log), ConversationAgent()
    print("partitions per consumer:", group.rebalance(["consumer-1", "consumer-2"]))
    c1, c2 = Consumer("consumer-1", group, agent), Consumer("consumer-2", group, agent)
    c1.poll()
    c2.poll(crash_after=1)  # handles conv-e's first event, dies before committing it
    print("consumer-2 crashed; committed offsets (partition: next offset to read):", group.committed)

    print("after rebalance, partitions per consumer:", group.rebalance(["consumer-1"]))
    c1.poll()  # resumes consumer-2's partitions from their committed offsets
    print("redelivered and skipped as already applied:", agent.skipped)
    print("carts:", agent.carts)
    print("order seen per conversation (seq numbers, as sent):", agent.order_by_key)
    print("dead letters (conversation, offset, error):", [(d.record.key, d.record.offset, d.error) for d in c1.dead_letters])
