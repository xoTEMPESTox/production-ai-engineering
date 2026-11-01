# Lesson 28: Cost Tracking - Architecture

This document shows the path of one priced call, how reservations behave under concurrency, the components, and the same busy afternoon under naive and production accounting.

## One Priced Call

```mermaid
flowchart TD
    Req([tenant, feature, run, model, prompt, max_tokens]) --> Cache{exact cache hit?}
    Cache -- yes --> Hit[ledger: mode cache, cost 0] --> Ans1[answer]
    Cache -- no --> Est[worst case = cost of estimated input + max_tokens output]
    Est --> Res{spent + reserved + worst case within limit?}
    Res -- no --> Stop[BudgetExceeded: provider never called]
    Res -- yes --> Hold[reserve worst case]
    Hold --> Call[call the model]
    Call -- error --> Rel[settle 0: release the hold] --> Raise[re-raise]
    Call -- ok --> Settle[settle actual cost, alert once if over threshold]
    Settle --> Led[ledger: live or batch entry with price version]
    Led --> Store[cache the answer] --> Ans2[answer]
```

---

## Why Reservations Matter Under Concurrency

Limit $0.010. Two calls start at the same moment, each with a worst case of $0.006.

```mermaid
sequenceDiagram
    participant A as Call A
    participant B as Call B
    participant Bu as Budget (spent 0, reserved 0)
    A->>Bu: reserve 0.006
    Bu-->>A: ok, reserved 0.006
    B->>Bu: reserve 0.006
    Bu-->>B: BudgetExceeded (0 + 0.006 + 0.006 is over 0.010)
    A->>A: model call, actual cost 0.004
    A->>Bu: settle 0.004, reserved back to 0, spent 0.004
    Note over B: retry or queue: 0.004 + 0.006 now fits
```

Without reservations, both calls would read "spent 0", both would pass, and the worst case would come to $0.012.

---

## Component Layout

```
+----------------------------------------------------------------------+
| CostAwareClient(models, ledger, budget, cache)                       |
|   complete(tenant, feature, run_id, model, prompt, max_tokens, batch)|
|   cascade(tenant, feature, run_id, prompt, accept) -> answer, model  |
+----------------------------------------------------------------------+
| Budget(limits, alert_at, on_alert)                                   |
|   reserve(tenant, worst_case) -> Reservation | BudgetExceeded        |
|   settle(reservation, actual)   spent += actual, alert once          |
+----------------------------------------------------------------------+
| Ledger: append-only [LedgerEntry(run_id, tenant, feature, model,     |
|         input_tokens, output_tokens, cost_usd, mode, price_version)] |
|   total(**filters), by(field)                                        |
+----------------------------------------------------------------------+
| PRICES (versioned, illustrative), call_cost(model, in, out, batch)   |
+----------------------------------------------------------------------+
```

---

## A Busy Afternoon, Two Ways

A bulk report job and the support chat share tenant acme's daily limit.

| | Global counter, checked after each call | Ledger + reservations (this lesson) |
|---|---|---|
| Which feature spent the money? | unknown | `ledger.by("feature")`: report vs support_chat |
| The call that crosses the limit | made, then an alert | refused before the provider is called |
| Five calls in flight at the limit | all five complete | each must fit `spent + reserved + worst case` |
| Alerts | one per call after the limit | one per tenant at the threshold |
| The same question asked twice | paid twice | second is a zero-cost cache entry |
| Easy questions | top-model price | small model, escalating only when the check fails |
| Last month's report after a price change | silently recomputed | entries keep their `price_version` |
