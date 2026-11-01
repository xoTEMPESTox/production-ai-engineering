# Lesson 28: Cost Tracking

Learn how to know and control what an AI system spends: a per-call ledger priced from a versioned table, budgets that stop spending *before* it happens, and the three cheapest savings: caching, cascades and batch.

> **Pillars:** Harness · Agent · RAG  |  **Harness layer:** spend control, meaning what each call cost, who it was for, and when to stop
>
> *Price every call, reserve before you spend, and send cheap work to cheap models.*

## Why does this exist?
Model calls are metered, and agents multiply them: loops, sub-agents, retries, evaluations. One runaway loop or one bulk job can spend in minutes what a feature is budgeted for a month. Finance asks "what does this feature cost per customer?". Engineering asks "why did yesterday cost three times as much?". Both questions need a **ledger**, and a limit has to stop spending before it happens, not report it afterwards.

---

## What problem does it solve?
1. **Attribution**: cost per tenant, feature, run and model.
2. **Hard limits that really stop**: per tenant, per day or month, enforced before the call.
3. **Concurrency**: ten parallel calls that each check "are we under budget?" all pass, and overspend together.
4. **Cutting cost without cutting quality**: caching, routing and batching.
5. **Price changes**: historical costs must use the price that applied at the time.

---

## How would beginners implement this?
```python
total_spent = 0.0

def call(model, prompt):
    global total_spent
    reply = llm(model, prompt)
    total_spent += reply.usage.total_tokens * 0.00001  # one blended price
    if total_spent > MONTHLY_BUDGET:
        alert("over budget!")                          # after the money is gone
    return reply
```

### Why does that fail?
1. **One global counter.** There's no breakdown by tenant or feature, and a restart resets it.
2. **One blended price.** Input and output tokens are priced differently, often by several times, and models differ by more than ten times.
3. **The check happens after the call.** The call that crosses the limit, and every call running alongside it, has already been paid for.
4. **The alert fires on every call after the limit** (an alert storm), or never resets.
5. **Nothing is cached, routed or batched.** Every repeated question and every easy one is paid for at top-model prices.
6. **There's no price version.** When prices change, historical reports silently recompute.

---

## How is this solved in production?
**1. A versioned price table.** Input and output are priced separately per model, and the table has a version (`PRICE_TABLE_VERSION`). The prices in this lesson are illustrative, not real list prices.

**2. An append-only ledger.** One `LedgerEntry` per call: run, tenant, feature, model, input and output tokens, cost, mode (`live`, `batch` or `cache`) and price version. Totals are always computed from entries (`ledger.total(tenant="acme")`, `ledger.by("feature")`), never kept in a separate counter. In production this is a database table, or the same fields carried on Lesson 22's spans.

**3. Reserve the worst case, then settle the actual cost:**
```python
worst_case = call_cost(model, estimate_tokens(prompt), max_tokens)  # max_tokens = the most it can output
reservation = budget.reserve(tenant, worst_case)    # BudgetExceeded here: the provider is never called
try:
    result = model.complete(prompt, max_tokens)
except Exception:
    budget.settle(reservation, 0.0)                 # release the hold
    raise
budget.settle(reservation, call_cost(model, result.input_tokens, result.output_tokens))
```
`reserve` refuses when `spent + reserved + worst_case > limit`, so concurrent calls can't each see the same remaining budget. One side effect shows up in the demo: the hard stop arrives at 74% spent, because the *next* call's worst case doesn't fit. Tighter `max_tokens` values bring estimates closer to reality.

**4. Alert once.** Each tenant is alerted the first time it crosses `alert_at` (at 62% in the demo, with the threshold set to 50%), not on every call afterwards.

**5. An exact-match cache, counted in the ledger.** The cache key includes the model and `max_tokens` along with the prompt. A hit is recorded as a zero-cost `cache` entry, so hit rate and savings show up in the same reports as spend.

**6. A cascade: cheap model first.** `cascade` asks the small model, runs a deterministic check (here "has a citation"; it could be Lesson 03's schema and citation validation), and escalates to the large model only if the check fails. A rejected answer costs both calls, and the ledger shows that too. In the demo, three questions cost $0.00228, against $0.00451 for sending each distinct question to the large model.

**7. Batch for work that can wait.** Evaluations (Lesson 27), backfills and bulk re-summarisation rarely need an answer within seconds. Many providers discount asynchronous batch processing, often by around half (check yours), and the ledger records those calls as `batch`.

Other levers covered elsewhere: prompt-prefix caching (Lesson 03), trimming context (Lesson 05), step and token budgets per run (Lesson 02) and retry budgets (Lesson 16).

---

## Design Tradeoffs

### Worst-case reservation vs. accounting after the fact
* **Reservation** (this lesson): a hard stop that holds under concurrency, and no surprise bills. The usable budget shrinks by the gap between worst case and typical case, and you need a token estimate before each call.
* **Accounting afterwards**: uses the whole budget and is simple. It can overshoot by however much is in flight when the limit is crossed, so it suits soft limits and alerts, not hard caps.

### Cascade vs. a router vs. a single model
* **Single strong model**: the simplest and highest quality, and the most expensive per call.
* **Cascade** (this lesson): cheap by default, with escalation decided by a check on the *answer*. It needs a reliable check, and pays twice on escalation.
* **Router** (a classifier picks the model from the *question*): one call per request. A wrong route either wastes money or lowers quality, and the router itself needs evaluation (Lesson 27).

### Exact cache vs. semantic cache
* **Exact** (this lesson): never returns a wrong answer for a different question, but only catches identical prompts.
* **Semantic** (embedding similarity): far more hits, but two similar questions can need different answers ("cancel my *annual* plan" vs. "cancel my *monthly* plan"). Use it only where near-duplicates really share an answer, and evaluate it like any other component.

---

## Failure Cases to Watch For
1. **A single blended token price**: costs off by several times on output-heavy workloads.
2. **Budget checks after the call**: limits that report rather than stop.
3. **Check-then-call races**: concurrent calls overspending together.
4. **Unattributed spend**: no tenant or feature on the record, so nobody can tell what caused a spike.
5. **History recomputed at new prices**: last quarter's costs change when prices do.
6. **Caching the wrong things**: fallback answers (Lesson 14), or answers that depend on tenant data not present in the prompt. Include the tenant in the key whenever outputs can differ by tenant.
7. **Cascades with no real check**: everything escalates (paying twice) or nothing does (quality drops).
8. **Hidden multipliers**: retries, agent loops and sub-agents multiplying calls without per-run budgets.
9. **Ignoring billed failures**: some providers charge input tokens on failed or cancelled calls. Record usage whenever it's reported.

---

## Try It
```sh
cd part-2-production-patterns/28-cost-tracking
python example.py            # fake models with illustrative prices
python -m pytest tests.py -q
```
What to watch for: the export question escalates to the large model while the refund question stays on the small one. The repeated question is a zero-cost `cache` entry in the ledger. The bulk "report" loop is stopped by `BudgetExceeded` before the call that wouldn't fit, the alert fires exactly once, and batch mode halves the cost of the eval job.

Experiment: in `Budget.reserve`, drop `+ self.reserved(tenant)` from the check and run the tests. `test_reservations_stop_concurrent_calls_overspending` fails: two in-flight calls can now both pass the check against the same remaining budget.

**Next:** Lesson 29 secures the agent: prompt injection from documents and tools, least-privilege tools, no executing model output, PII tokenisation, secrets and rate limits.
