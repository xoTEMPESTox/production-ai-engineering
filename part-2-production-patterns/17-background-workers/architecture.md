# Lesson 17: Background Workers - Architecture

This document shows a message's life in the queue, the burst of duplicate uploads handled by single-flight, the components, and the same afternoon's uploads handled by a naive and a production worker.

## A Message's Life

```mermaid
stateDiagram-v2
    [*] --> visible: send
    visible --> hidden: receive (receive_count + 1, hidden for the visibility timeout)
    hidden --> hidden: extend (long job heartbeat)
    hidden --> [*]: ack (done, or coalesced into a running job)
    hidden --> visible: timeout passes (worker crashed or the job failed)
    visible --> dead_letter: received max_receives times already
    dead_letter --> [*]: inspected, fixed, redriven or dropped
```

---

## Three Uploads of One Document

```mermaid
sequenceDiagram
    participant Q as Queue
    participant W1 as Worker 1
    participant W2 as Worker 2
    participant W3 as Worker 3
    participant F as SingleFlight
    Q->>W1: msg 1 doc-1
    Q->>W2: msg 2 doc-1
    Q->>W3: msg 3 doc-1
    W1->>F: try_start doc-1, ok
    W2->>F: try_start doc-1, refused, doc-1 marked dirty
    W2->>Q: ack msg 2 (coalesced)
    W3->>F: try_start doc-1, refused, already dirty
    W3->>Q: ack msg 3 (coalesced)
    W1->>W1: parse, chunk, embed doc-1
    W1->>Q: ack msg 1
    W1->>F: finish doc-1, succeeded, was dirty, so re-run
    W1->>Q: send doc-1 (changed while processing)
    Q->>W2: msg 6 doc-1
    W2->>F: try_start, ok, then embed the latest version, finish, not dirty
```

Two runs in total, never two at the same time, and the last one sees the latest upload.

---

## Component Layout

```
+-------------------------------------------------------------------+
| InMemoryQueue(clock, visibility_timeout_s, max_receives)          |
|   send(body) / receive() / ack(id) / fail(id, error) / extend()   |
|   messages: id -> Message(body, receive_count, visible_at,        |
|                           last_error)                             |
|   dead_letters: [Message]                                         |
+-------------------------------------------------------------------+
| SingleFlight: try_start(entity) -> bool                           |
|               finish(entity, succeeded) -> needs a follow-up run? |
+-------------------------------------------------------------------+
| Worker(queue, flight, process)                                    |
|   handle(message) -> processed | coalesced | failed               |
| run_pool(worker, threads)   N threads: receive -> handle          |
+-------------------------------------------------------------------+
```

---

## Naive vs. Production

| Situation | `jobs.pop(0)` loop | Queue + DLQ + single-flight |
|---|---|---|
| Worker dies mid-job | the job is gone | message reappears after the timeout |
| Corrupt PDF | crashes the loop, or retries forever | 3 attempts, then the dead-letter queue with its error |
| Other documents meanwhile | stuck behind it | processed normally |
| Three uploads of doc-1 at once | three concurrent runs racing to write | one run + one follow-up, never concurrent |
| Which version ends up indexed | whichever run finished last | the latest (the follow-up runs after the burst) |
| Embedding cost for the burst | 3x | 2x at most |
| After a restart | queue lost | queue is external; work resumes |
