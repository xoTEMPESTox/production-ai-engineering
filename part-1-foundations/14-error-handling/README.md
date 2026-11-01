# Lesson 14: Error Handling

Learn how to decide what to do about each failure: an error taxonomy (transient, permanent, format, policy), a fallback chain that never pollutes the cache, and a circuit breaker per provider.

> **Pillars:** Harness · RAG · Agent  |  **Harness layer:** failure policy, meaning what happens when a dependency, the model or the request goes wrong
>
> *Sort errors by what to do about them, not by where they came from.*

## Why does this exist?
Every external call fails sometimes, and AI systems have more ways to fail than most: provider outages and rate limits, output that doesn't parse, refusals, a retrieval service that is down. A single `try/except: retry` treats all of these the same, and every one of them gets it wrong in a different way:
- retrying a malformed request wastes money;
- sending a refusal to another model works around the policy;
- caching a fallback answer stretches the outage past its end.

---

## What problem does it solve?
1. **Choosing the response per error**: retry or move on, fall back, stop, or bring in a person.
2. **Degrading gracefully**: give the user something useful during an outage, and label it as degraded.
3. **Not making an outage worse**: hammering a failing provider adds load, and makes every request wait for timeouts.
4. **Not letting degraded results outlive the problem**: caches last longer than outages.

---

## How would beginners implement this?
```python
@functools.cache
def answer(question):
    for attempt in range(5):
        try:
            return primary.complete(question)
        except Exception:
            time.sleep(1)
    return fallback.complete(question)  # cached forever under the real key
```

### Why does that fail?
1. **Every error is retried five times.** A `400 prompt too long` is retried five times, then sent to another model.
2. **Refusals are shopped around.** A refusal from the primary goes to a different model until one agrees.
3. **Every request pays for the outage.** While the primary is down, each request waits through five attempts before falling back. Latency multiplies, and so does the load on the struggling provider.
4. **The fallback is cached under the real key.** After the outage the degraded answer keeps being served (and `functools.cache` never evicts it).
5. **Nothing says which path served the answer**, so the fallback rate can't be monitored.

---

## How is this solved in production?
**1. A taxonomy keyed to actions:**

| Kind | Examples | What to do |
|---|---|---|
| Transient | timeout, connection reset, 408, 429, 5xx | try the next option (retry with backoff in Lesson 16); counts against the provider's health |
| Format | empty or unparseable output | one repair (Lesson 03), then the next model; counts against health |
| Permanent | 400, 401, 403, 404, unknown errors | stop; no retry, no fallback; alert engineers, because it's a bug |
| Policy | refusal, content filter, blocked request | stop; don't try another model; send to a person |

`classify()` maps exceptions to kinds. Unknown exceptions are treated as permanent, so an unexpected error is never hammered with retries.

**2. A fallback chain with labelled answers.** The primary model, then a fallback model, then a static answer. Every `Answer` carries its `source` (`cache`, `primary`, `fallback_model`, `static`) and the `error_kind` that caused a fallback. The fallback rate per hour becomes a monitoring signal (Lesson 26).

**3. Stop on permanent and policy errors.** Both return `needs_human=True` without touching the fallback model. A permanent error means the request is wrong, so another model would only repeat the mistake at a different price. A policy refusal is a decision, not a fault.

**4. Never cache a fallback under the real key:**
```python
breaker.record_success()
if name == "primary":
    self.cache[key] = text  # only full-quality answers are cached
return Answer(text, name, last_kind)
```
The demo's degraded answer ("about a week") is served during the outage but never cached, so the first request after recovery gets the real answer ("5 business days"), and that is what gets cached. If you do want to cache degraded answers to absorb load, use a separate key with a short TTL that a normal lookup never reads.

**5. A circuit breaker per provider.** It opens after `threshold` consecutive transient or format failures. While open it fails fast for `cooldown_s` (the request goes straight to the fallback, with no wait for a timeout). After the cooldown it lets exactly **one** trial call through, which closes the circuit on success and reopens it on failure. In the demo, five requests during and after an outage reach the primary only four times: three failures, then one successful trial.

**6. Bad requests don't take a healthy provider offline.** Permanent and policy errors are recorded as successes for health purposes, because the provider responded and the request was the problem. Otherwise a burst of malformed requests from one buggy caller would trip the breaker for everyone.

Retrying itself (backoff, jitter, `Retry-After`, retry budgets) is Lesson 16. Here a transient error moves straight to the next option.

---

## Design Tradeoffs

### Fall back vs. fail
* **Fall back**: better availability, but quality can drop without anyone noticing. Label the answer and watch the fallback rate.
* **Fail**: for answers that must not degrade (a price, a dosage, a legal deadline), a clear "can't answer right now" beats a plausible degraded one.

### Consecutive failures vs. error rate over a window
* **Consecutive** (this lesson): simple, and quick to react to a hard outage. A partial outage, such as 30% of calls failing, may never trip it.
* **Error rate over a window**: handles partial outages, but needs a minimum request volume to be meaningful and more state to track.

### Classifying by status code vs. by error type
* **Status codes** are coarse. A 400 can mean "prompt too long" (permanent for this model, but maybe fine on a fallback with a larger context window) or "malformed request" (permanent everywhere).
* **Provider error types and bodies** distinguish those cases. Use them where available, and default conservatively where not.

---

## Failure Cases to Watch For
1. **Retrying everything**: permanent errors retried until the retry limit, then passed to another model.
2. **Shopping refusals**: trying models until one says yes.
3. **Caching fallbacks under the real key**: the outage ends, the degraded answer stays.
4. **Unlabelled fallbacks**: users and dashboards can't tell a degraded answer from a real one.
5. **Permanent errors tripping the breaker**: one bad caller takes a provider offline for everyone.
6. **No half-open state**: the breaker never recovers, or every waiting request rushes the provider at once when the cooldown ends. Allow one trial at a time.
7. **A static fallback that pretends to be an answer**: it must clearly say it is not one.
8. **Swallowed errors**: an `except` that returns a fallback without recording the error kind.

---

## Try It
```sh
cd part-1-foundations/14-error-handling
python example.py            # scripted providers and a fake clock
python -m pytest tests.py -q
```
What to watch for: the breaker opens at t=40s, and from then on the primary is skipped instead of waited on. At t=80s one trial call goes through, the circuit closes, and only then does anything get cached: the primary's answer, not the fallback's. The policy and permanent cases end with `needs_human=True` and `fallback model calls=0`.

Experiment: in `AnswerService.answer`, change `if name == "primary":` to `if True:` and run the tests. `test_transient_error_falls_back_and_is_not_cached` fails: the degraded answer is now cached, and the recovered primary is never asked again.

**Next:** Lesson 15 makes a whole run survive a restart: checkpoint and restore, versioned state schemas, and migrations, on SQLite.
