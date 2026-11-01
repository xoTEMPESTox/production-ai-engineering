# Lesson 27: Evaluation Pipelines - Architecture

This document shows the pipeline from a pull request to a gate decision, how the cache and scoring are separated, the components, and the demo's candidate judged by a naive and a production gate.

## From Pull Request to Gate

```mermaid
flowchart LR
    PR([PR changes prompt v1 to v2]) --> Load[load dataset support-golden version 2026-11-01]
    Load --> GenB[generate baseline v1]
    Load --> GenC[generate candidate v2]
    GenB <--> Cache[(output cache, key = hash of model, prompt, settings, dataset version, example)]
    GenC <--> Cache
    GenB --> Score[score: exact_match per example]
    GenC --> Score
    Score --> Report[per-slice means + bootstrap CIs]
    Score --> Gate{paired gate}
    Gate -- delta CI below 0, or a slice dropped more than 0.10 --> Fail[exit 1: block merge]
    Gate -- otherwise --> Pass[exit 0]
    Prod[(production traces)] --> Sample[should_sample: hash bucket or flagged]
    Sample --> Review[human review + labels] --> Load
```

---

## Generation vs. Scoring

```mermaid
sequenceDiagram
    participant R as generate()
    participant C as OutputCache
    participant M as Model
    participant S as score()
    R->>C: get(key for billing-0)
    C-->>R: miss
    R->>M: complete(prompt)
    M-->>R: TimeoutError
    Note over R: recorded in errors, scored as wrong, NOT cached
    R->>C: get(key for billing-1)
    C-->>R: miss
    R->>M: complete(prompt)
    M-->>R: output
    R->>C: put(key, output)
    Note over R,C: next run: 39 hits, 1 retried call
    S->>C: (via outputs) any number of new metrics, zero model calls
```

---

## Component Layout

```
+---------------------------------------------------------------------+
| Dataset(name, version, examples[Example(id, question, expected,     |
|                                          slice)])                   |
| RunConfig(model, prompt_version, prompt_template, temperature, seed)|
|   cache_key(dataset, example)                                       |
+---------------------------------------------------------------------+
| generate(model, config, dataset, cache) -> outputs, RunStats        |
|   RunStats: model_calls, cache_hits, errors                         |
| OutputCache(path?)  JSONL, successful outputs only                  |
+---------------------------------------------------------------------+
| score(outputs, dataset, scorer) -> {example_id: score}              |
| bootstrap_ci(values) -> (low, high)                                 |
| gate(baseline, candidate, dataset, max_slice_drop, min_slice_n)     |
|   -> GateResult(passed, overall_delta, delta_ci, reasons, notes)    |
| should_sample(trace_id, rate, flagged) -> bool                      |
+---------------------------------------------------------------------+
```

---

## The Demo's Candidate, Two Gates

| | `evaluate(new) >= evaluate(old)` | Paired gate (this lesson) |
|---|---|---|
| Overall | 0.875 ≥ 0.850, so ship | delta +0.025, CI (-0.1, 0.15): no significant change either way |
| Shipping slice | not looked at | 0.92 → 0.67 with n=12: **fail** |
| Cost of the comparison | 80 generations every time | 40 for the new prompt; baseline from the cache |
| A timeout during the run | crash, or a cached empty answer | one error recorded and retried next run |
| Reproducible later? | no record of settings | dataset version + config in every key |
| Decision | ships a broken shipping experience | blocked, with the reason printed |
