# Lesson 26: Monitoring

Learn how to know an AI service is healthy without reading every trace: SLOs and error budgets, burn-rate alerts checked over two windows, quality signals like schema-failure, fallback and empty-retrieval rates, one alert per incident, and input drift.

> **Pillars:** Harness · RAG · Agent  |  **Harness layer:** monitoring, meaning the few numbers that say whether the system is doing its job right now
>
> *Alert on what users feel, at the speed it matters, once per incident.*

## Why does this exist?
Traces (Lesson 22) answer *why* one request went wrong. Monitoring answers *whether* things are going wrong right now, across all requests, and wakes someone only when it matters. AI services need two kinds of signal:
- **Service health**: errors and latency, the same as for any API.
- **Answer health**: whether the outputs are still good. Schema failures (Lesson 03), fallbacks (Lesson 14), retrievals that find nothing (Lesson 05), human edits (Lesson 08). An answer can be fast and HTTP 200 while still being wrong.

Alert too eagerly and people learn to ignore the pager. Alert too slowly and users find the problem first.

---

## What problem does it solve?
1. **"Is this bad enough to act on?"**: a target (an SLO) turns that question into numbers.
2. **Paging on real incidents only**: not on a five-minute blip, and not hours after users noticed.
3. **Quality regressions with healthy status codes**: a prompt change that breaks JSON 2.5% of the time.
4. **Noise**: one incident producing fifty notifications, or alerts on three requests.
5. **Drift**: users start asking about things your index and evals don't cover.
6. **Cost of the monitoring itself**: unbounded metric labels.

---

## How would beginners implement this?
```python
@app.middleware("http")
async def watch(request, call_next):
    response = await call_next(request)
    metrics.inc("requests", user_id=request.user.id, path=request.url.path)  # a series per user
    if response.status_code >= 500:
        send_page("an error happened!")                                    # every single one
    return response
```

### Why does that fail?
1. **A label per user** creates a new time series for every user. Storage and query cost explode, and dashboards slow to a crawl.
2. **A page per error.** One bad minute means hundreds of pages, and the next real incident gets ignored.
3. **No target.** Is 0.5% errors fine? Is 2%? Without an SLO, every judgement is a debate.
4. **Only HTTP errors.** A broken prompt returning invalid JSON with status 200 is invisible.
5. **No volume floor.** One failure among three requests is "33% errors".
6. **No view of the inputs.** When users start asking about a new feature, nothing notices that retrieval stopped finding answers.

---

## How is this solved in production?
**1. Counters per minute, with bounded labels.** `Metrics` counts events per minute and keeps latency samples. Labels are restricted to small, fixed sets (`route`, `model`, `prompt_version`), and `user_id` is refused with a pointer to traces. A real time-series store works the same way: a series per label combination, so the number of combinations is the cost.

**2. An SLO with an error budget.** "99% of requests succeed over 30 days" means a 1% error budget. The **burn rate** is how fast you're spending it: `(errors / requests) / (1 − objective)`. A burn rate of 1 uses the budget exactly over the period, and 30% errors is a burn rate of 30.

**3. Alerts on burn rate, over two windows:**
```python
for severity, long_s, short_s, threshold in (("page", 1h, 5m, 14.4), ("ticket", 6h, 30m, 6.0)):
    if burn(long_s) > threshold and burn(short_s) > threshold:
        alert(severity, ...)
```
The long window says the problem is real and not a blip. The short window says it's still happening, so the alert clears within minutes of recovery instead of hours. These thresholds are the commonly used defaults for a 30-day period: a page when about 2% of the monthly budget burns in an hour, a ticket when about 5% burns in six hours. In the demo, a hard outage (30% errors) pages about 29 minutes in, and the page resolves minutes after the provider recovers. A five-minute blip of total failure doesn't page at all.

**4. AI quality signals as first-class alerts.** Over the last hour, and only with at least `min_requests` requests:

| Signal | Source | Limit | Means |
|---|---|---|---|
| schema-failure rate | Lesson 03 validation | 2% → ticket | a prompt or model change broke the output contract |
| fallback rate | Lesson 14 chain | 10% → page | the primary model is failing; users get degraded answers |
| empty-retrieval rate | Lesson 05 search | 15% → ticket | users ask about things the index doesn't cover |
| p95 latency | request timings | 3,000 ms → ticket | slower answers (often a bigger context, or a slower model) |

The prompt change in the demo (hours 40–44) never touches the error rate, yet it opens one schema-failure ticket. Human edit rate (Lesson 08), groundedness failures (Lesson 09) and cost per request (Lesson 28) belong on the same dashboard.

**5. One notification per incident.** `AlertManager` notifies when an alert *starts* firing and once when it resolves. It never repeats on every evaluation. Pages go to whoever is on call, tickets to the team queue.

**6. Drift, measured.** `psi(reference, current)` compares the distribution of a categorical input, such as query topic or language, between last week and this week. As a rule of thumb, PSI below 0.1 is stable, 0.1–0.25 is shifting, and above 0.25 is a real change. After the demo's feature launch, a new `data_export` topic pushes PSI to about 1.6. That's a cue to extend the index and the golden set (Lesson 27) before the empty-retrieval alert fires.

---

## Design Tradeoffs

### Threshold alerts vs. burn-rate alerts
* **Raw threshold** ("errors > 1% for 5 minutes"): simple, but it fires on harmless blips and can't tell a slow leak from a fire.
* **Burn rate over two windows** (this lesson): pages in proportion to how fast users are being hurt, and clears quickly. It needs an agreed SLO, and enough traffic for the windows to mean anything.

### Metrics vs. traces vs. logs
* **Metrics**: cheap, aggregated, and fast to query. They answer "how many?" and "how fast?". No per-request detail.
* **Traces** (Lesson 22): per-request detail and attribution. They answer "why this one?". Costly at full volume, so sample them.
* **Logs**: free-form events, good for debugging, hard to aggregate. Alert on metrics, investigate with traces.

### Fixed quality limits vs. comparing with a baseline
* **Fixed limits** (this lesson): easy to reason about. They need tuning per feature, and drift slowly out of date.
* **Relative to a baseline** (last week, or the stable arm during a canary as in Lesson 24): adapts to each feature's normal level, but a slow decline can become the new baseline. Use fixed floors for the signals that must never degrade.

---

## Failure Cases to Watch For
1. **High-cardinality labels** (user, request or document IDs on metrics).
2. **Paging on every error**: alert fatigue, and the real incident is missed.
3. **No SLO**: arguments instead of decisions.
4. **Only HTTP status monitored**: broken answers with status 200.
5. **No minimum volume**: night-time traffic turns one failure into a page.
6. **Alerts that never clear**: long windows only, so on-call can't tell when it's over.
7. **Unwatched drift**: retrieval and evals quietly fall behind what users ask.
8. **Dashboards without owners**: nobody looks until the incident review.

---

## Try It
```sh
cd part-2-production-patterns/26-monitoring
python example.py            # simulates two days of traffic in well under a second
python -m pytest tests.py -q
```
What to watch for: the `user_id` label is refused. During the hour-30 outage, the latency ticket and the fallback page fire first, then the fast-burn page about 29 minutes in, then the slow-burn ticket. All four resolve within an hour of recovery. The bad prompt at hour 40 opens exactly one schema-failure ticket, which resolves when it's reverted. And topic PSI jumps from 0.0 to about 1.57 after the launch.

Experiment: in `evaluate_rules`, drop the short-window condition (`and short_rate > threshold`) and run the tests. `test_alert_clears_soon_after_recovery` fails: ten minutes after the provider recovered, the page is still firing, because the one-hour window remembers the outage.

**Next:** Lesson 27 turns evaluation into a release gate: versioned datasets, cached outputs, confidence intervals, and a regression check in CI.
