# Lesson 04: Tool Calling - Architecture

This document shows the fixed pipeline every tool call goes through, how the components fit together, and how a slow, repeated refund plays out in a naive and a production dispatcher.

## The Call Pipeline

```mermaid
flowchart TD
    Call([model: tool name + raw arguments]) --> Known{tool registered?}
    Known -- no --> Unknown[unknown_tool: list real tools]
    Known -- yes --> Valid{arguments valid for the Pydantic model?}
    Valid -- no --> Invalid[invalid_args: field-level errors]
    Valid -- yes --> Tier{tier}
    Tier -- destructive --> Approve{approver says yes?}
    Approve -- no --> Denied[denied: tell the user, don't retry]
    Approve -- yes --> Key
    Tier -- write --> Key[idempotency key = hash of run, tool, args]
    Tier -- read --> Exec
    Key --> Done{key already completed?}
    Done -- yes --> Replayed[replayed: first result, handler not called]
    Done -- no --> Exec[execute in worker thread, with timeout]
    Exec -- returns --> Store[writes: store output under key]
    Exec -- raises --> Error[error: not cached]
    Exec -- too slow --> Timeout[timeout: for writes, outcome unknown]
    Store --> Trunc[truncate to max_result_chars + hint]
    Trunc --> Ok[ok]
```

Every terminal box is a `ToolResult(outcome, content)`. The content is the observation the agent loop from Lesson 02 appends for the model, so every failure is something the model can read and act on.

---

## Component Layout

```
+--------------------------------------------------------------------+
| ToolRegistry                                                       |
|   register(ToolSpec)          duplicate names refused              |
|   schemas() -> [{name, description, parameters: JSON Schema}]      |
|   call(name, raw_args, run_id) -> ToolResult                       |
|   approver: (tool, validated_args) -> bool    default: deny_all    |
|   _completed: idempotency key -> output   (Redis/Postgres later)   |
|   _pool: ThreadPoolExecutor               (timeouts)               |
+-----------------------------+--------------------------------------+
                              |
              +---------------+----------------+
              v                                v
+-----------------------------+   +--------------------------------+
| ToolSpec (frozen)           |   | ToolContext                    |
|   name, description         |   |   run_id                       |
|   args: type[BaseModel]     |   |   idempotency_key  -> forward  |
|   handler(args, ctx) -> str |   |   it to the downstream API     |
|   tier: READ|WRITE|DESTR.   |   +--------------------------------+
|   timeout_s                 |
+-----------------------------+
```

A tool from an MCP server plugs in as one more `ToolSpec`: its `inputSchema` becomes the argument model (or is validated directly), and the handler forwards the call to the server. The tier and timeout are still set by your harness.

---

## Naive vs. Production: the Slow, Repeated Refund

The billing API is slow today. The model calls `issue_refund`, doesn't see a result in time, and calls it again with the same arguments.

```mermaid
sequenceDiagram
    participant M as Model
    participant D as Dispatcher
    participant B as Billing API
    Note over M,B: Naive: no timeout, no key
    M->>D: issue_refund(acct-17, 1999)
    D->>B: refund
    Note over D: blocks for as long as B takes
    M->>D: issue_refund(acct-17, 1999) again, on its next turn
    D->>B: refund
    B-->>D: ok, ok (two refunds)
    Note over M,B: Production: timeout + idempotency key
    M->>D: issue_refund(acct-17, 1999)
    D->>B: refund, Idempotency-Key k1
    D-->>M: timeout, outcome unknown, check status first
    M->>D: issue_refund(acct-17, 1999) again
    D->>B: refund, Idempotency-Key k1 (same run + args gives the same key)
    B-->>D: already done for k1
    D-->>M: ok, one refund (a third repeat is replayed locally)
```

Two layers of deduplication are at work. The registry's `_completed` map catches repeats after a write has finished. The key forwarded to the billing API catches the cases the registry can't see: a timed-out first attempt that still finished, or a crash between the API acting and the result being stored.
