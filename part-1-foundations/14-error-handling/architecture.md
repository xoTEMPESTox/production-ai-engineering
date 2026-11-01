# Lesson 14: Error Handling - Architecture

This document shows how one request moves through the fallback chain, the circuit breaker's states, the components, and an outage traced through a naive and a production service.

## One Request Through the Chain

```mermaid
flowchart TD
    Q([question]) --> C{in cache?}
    C -- yes --> Cached[answer, source cache]
    C -- no --> B1{primary breaker allows?}
    B1 -- no, open --> B2
    B1 -- yes --> P[call primary]
    P -- ok --> Store[cache under the real key] --> AP[answer, source primary]
    P -- error --> K1{classify}
    K1 -- policy --> Human[stop: policy reply, needs_human]
    K1 -- permanent --> Alert[stop: static reply, needs_human, alert]
    K1 -- transient or format --> F1[primary breaker: record failure] --> B2{fallback breaker allows?}
    B2 -- yes --> FM[call fallback model]
    FM -- ok --> AF[answer, source fallback_model, NOT cached]
    FM -- error --> S[static answer, NOT cached]
    B2 -- no --> S
```

---

## Circuit Breaker States

```mermaid
stateDiagram-v2
    [*] --> closed
    closed --> closed: success, failures reset to 0
    closed --> open: threshold consecutive transient or format failures
    open --> open: cooldown not over, fail fast without calling
    open --> half_open: cooldown over
    half_open --> closed: the single trial succeeds
    half_open --> open: the trial fails, new cooldown
```

Permanent and policy errors are recorded as successes: the provider answered, and the request was the problem.

---

## Component Layout

```
+---------------------------------------------------------------------+
| AnswerService(primary, fallback, clock)                             |
|   answer(question) -> Answer(text, source, error_kind, needs_human) |
|   cache: key -> primary answers only                                |
|   chain: primary + CircuitBreaker, fallback_model + CircuitBreaker  |
|   events: what happened on each attempt                             |
+---------------------------------------------------------------------+
| classify(exception) -> ErrorKind: transient | format |             |
|                                   permanent | policy               |
| CircuitBreaker(threshold, cooldown_s, clock)                        |
|   allow() / record_success() / record_failure(); one half-open trial|
+---------------------------------------------------------------------+
| ProviderError(status), FormatError, PolicyError                     |
+---------------------------------------------------------------------+
```

---

## An Outage, Two Ways

The primary returns 503 for the first three calls, then recovers. One request arrives every 20 seconds.

| t | `@cache` + retry 5x + fallback | This lesson |
|---|---|---|
| 0 s | 5 attempts (~5 s wait), fallback answer **cached forever** | primary fails, fallback answers, not cached |
| 20 s | cache hit: degraded answer | primary fails, fallback answers |
| 40 s | cache hit: degraded answer | primary fails, breaker opens, fallback answers |
| 60 s | cache hit: degraded answer | breaker open: primary skipped, fallback answers at once |
| 80 s | cache hit: degraded answer, still | trial call succeeds, breaker closes, real answer cached |
| Primary calls | 5 (then never again) | 4 |
| After recovery | degraded answer until the process restarts | real answer from the first request after recovery |
