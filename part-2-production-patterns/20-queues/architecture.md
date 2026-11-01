# Lesson 20: Queues - Architecture

This document shows how keys map to partitions and consumers, the crash-and-rebalance sequence, the components, and naive vs. production delivery.

## Keys, Partitions, Consumers

```mermaid
flowchart LR
    subgraph Producers
        A[conv-a events]
        D[conv-d events]
        E[conv-e events]
    end
    subgraph Log["Partitioned log: crc32 of key mod 4"]
        P0[(partition 0: conv-a)]
        P1[(partition 1: conv-e)]
        P2[(partition 2)]
        P3[(partition 3: conv-d)]
    end
    A --> P0
    E --> P1
    D --> P3
    P0 --> C1[consumer-1]
    P2 --> C1
    P1 --> C2[consumer-2]
    P3 --> C2
    G[(group: committed offset per partition)] -.-> C1
    G -.-> C2
```

Order holds inside each partition. Partitions are processed in parallel by different consumers.

---

## Crash, Rebalance, Redelivery

```mermaid
sequenceDiagram
    participant C2 as consumer-2
    participant G as Group (offsets)
    participant L as Log
    participant C1 as consumer-1
    C2->>L: read partition 1 from offset 0
    C2->>C2: handle conv-e add dock (cart has dock)
    Note over C2: dies before commit(1, 1)
    G->>G: rebalance: consumer-1 now owns 0, 1, 2, 3
    C1->>L: read partition 1 from committed offset 0
    C1->>C1: add dock again: record 1:0 already seen, skip
    C1->>C1: corrupt event: 3 attempts fail, dead-letter, commit
    C1->>C1: add cable: applied
    C1->>G: commit(1, 3)
```

---

## Component Layout

```
+-------------------------------------------------------------------+
| PartitionedLog(partitions)                                        |
|   partition_for(key) = crc32(key) % n    append(key, value)       |
|   read(partition, offset, max) -> [Record(partition, offset,      |
|                                          key, value)]             |
+-------------------------------------------------------------------+
| ConsumerGroup(log): rebalance(members) -> partition assignment    |
|                     committed: partition -> next offset           |
+-------------------------------------------------------------------+
| Consumer(name, group, handler, max_attempts)                      |
|   poll(): read from committed -> handle -> commit                 |
|   dead_letters: [DeadLetter(record, error, attempts)]             |
+-------------------------------------------------------------------+
| ConversationAgent: seen record ids (dedup), carts, order_by_key   |
+-------------------------------------------------------------------+
```

---

## Naive vs. Production

| Situation | Any worker, ack first | Partitioned log + group + commit after + dedup |
|---|---|---|
| "add laptop" then "remove laptop" | may run in either order: laptop stays | same partition, in order: laptop removed |
| Two conversations at once | parallel, but unordered within each | parallel across partitions, ordered within |
| Crash during processing | event lost (already acked) | redelivered from the committed offset |
| Crash after processing, before commit | n/a | redelivered and skipped as already seen |
| A consumer leaves | its in-flight events vanish | its partitions move; the new owner resumes at the offset |
| Corrupt event | lost, or retried forever | 3 attempts, dead-lettered, partition continues |
| Replay after a bug fix | impossible | reset offsets; idempotency keeps old effects single |
