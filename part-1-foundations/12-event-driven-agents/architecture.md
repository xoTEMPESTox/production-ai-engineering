# Lesson 12: Event-Driven Agents - Architecture

This document shows the path of one change from the producer's transaction to the consumer's, where duplicates come from and where they stop, the components, and the naive and production behaviour side by side.

## From Change to Index

```mermaid
sequenceDiagram
    participant U as Editor
    participant P as DocumentService (producer DB)
    participant R as Relay
    participant B as Broker
    participant C as ReindexAgent (consumer DB)
    participant M as Model
    U->>P: update refund-policy
    P->>P: one transaction: documents v2 + outbox row doc-refund-policy-v2
    R->>P: read unsent outbox rows
    R->>B: publish v1, v2
    Note over R: crash before marking sent
    R->>P: (restarted) read unsent rows again
    R->>B: publish v1, v2 again, then mark sent
    B->>C: v2
    C->>M: summarise v2 text
    C->>C: one transaction: processed marker + index row v2
    B->>C: v1 (late)
    C->>C: marker recorded, version 1 is older than 2, so stale
    B->>C: v2, v1 again
    C->>C: already processed, so duplicate (no model call)
```

---

## Where Duplicates Come From and Where They Stop

```mermaid
flowchart LR
    TX[producer transaction: change + outbox row] --> Relay
    Relay -- publish, crash before mark --> Dup1[same event published twice]
    Broker -- redelivery after consumer timeout --> Dup2[same event delivered twice]
    Dup1 --> Consumer
    Dup2 --> Consumer
    Consumer --> Check{event_id in processed_events?}
    Check -- yes --> Skip[duplicate: nothing happens]
    Check -- no --> Version{event version newer than indexed?}
    Version -- no --> Stale[stale: marker only]
    Version -- yes --> Apply[model call + index write + marker, one transaction]
```

Duplicates are created upstream on purpose (at-least-once) and absorbed in exactly one place: the consumer's transaction.

---

## Component Layout

```
Producer database                     Consumer database
+----------------------------+        +-----------------------------------+
| documents(id, text,        |        | search_index(doc_id, text,        |
|           version)         |        |              summary, version)    |
| outbox(seq, event_id UNIQUE|        | processed_events(event_id PK)     |
|        payload, sent)      |        +-----------------------------------+
+-------------+--------------+                       ^
              | relay_once(service, broker)          | ReindexAgent.handle(event)
              v                                      |   -> applied | duplicate | stale
        +-----------+      Event(event_id, type,     |
        |  Broker   |------ doc_id, version, text) --+
        +-----------+
```

---

## Naive vs. Production

| Situation | Commit, then publish; plain handler | Outbox + idempotent, versioned consumer |
|---|---|---|
| Crash after commit, before publish | event lost; index stale forever | event is in the outbox; relay publishes it |
| Write fails after the event was sent | consumers act on a change that never happened | impossible: both roll back together |
| Relay or broker redelivers | model call and upsert run again | `duplicate`: skipped, no model call |
| v1 arrives after v2 | index regresses to v1 | `stale`: ignored |
| Consumer crashes mid-handling | partial effect, or lost if the event was acked | effect and marker roll back; redelivery applies once |
| Model call before a consumer crash | paid again | still paid again: cache by event id if expensive |
