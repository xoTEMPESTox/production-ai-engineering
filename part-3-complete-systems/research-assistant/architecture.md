# System: Research Assistant - Architecture

This document shows the full run, where each safeguard sits, the components, and how the demo's two runs differ.

## One Run, End to End

```mermaid
flowchart TD
    Q([question + tenant]) --> P[planner call: Plan of sub-questions, validated, max 4]
    P -- invalid --> P1[degrade: one sub-question = the question]
    P --> F{fan out: semaphore 2, timeout per sub-agent}
    P1 --> F
    F --> S1[sub-agent 1: refund]
    F --> S2[sub-agent 2: export]
    subgraph SA["each sub-agent (isolated context)"]
        R[search: this tenant only] --> QZ{injection phrases?}
        QZ -- yes --> QL[quarantine: logged, removed]
        QZ -- no --> W[wrap as untrusted source blocks]
        W --> M[researcher call: Finding JSON]
        M --> V{schema ok and citations within own sources?}
        V -- no, first time --> RP[repair with the exact error] --> M
        V -- no, again --> X[None: this sub-question is missing]
        V -- yes --> OK[verified Finding]
    end
    S1 --> R
    S2 --> R
    OK --> J[collect verified findings]
    X --> J
    J --> MS[missing = planned minus verified, computed in code]
    MS --> WR[writer call: findings only, plus Missing list]
    WR --> NV[append Not verified lines]
    NV --> REP[Report: text, citations, missing, quarantined]
    REP --> EV[score against the golden case]
```

Every model call passes through `Harness.charge` (budget) and `Harness.span` (trace).

---

## Where Each Safeguard Sits

| Threat | Safeguard | Location |
|---|---|---|
| Globex documents in an Acme answer | tenant filter before ranking | `search` |
| "Ignore previous instructions…" in a wiki page | quarantine + untrusted markers | `research` |
| A cited source that wasn't provided | citation check, repair once, fail closed | `research` |
| A sub-agent that hangs | `asyncio.wait_for` per sub-agent | `run.bounded` |
| A sub-question with no verified answer | `missing` computed in code, appended to the report | `run` |
| Runaway calls | `max_calls` budget | `Harness.charge` |
| "Why did it say that?" | span per step | `Harness.trace` |
| Regressions | golden facts and citations | `score` |

---

## Component Layout

```
+------------------------------------------------------------------------+
| ResearchAssistant(llm, harness, max_parallel, timeout_s)               |
|   run(question, tenant) -> Report(text, citations, missing,            |
|                                   quarantined)                         |
|   plan() -> Plan  research() -> Finding | None  _call() budget + trace |
+------------------------------------------------------------------------+
| search(query, tenant, k)   BM25 over CORPUS, tenant-filtered first     |
| Plan / Finding (Pydantic, extra=forbid)                                |
| Harness(max_calls, price): charge(role), span(name, ...), cost_usd     |
| score(report, required_facts, expected_citations)                      |
| LLMClient (Protocol)   RuleBasedLLM(fabricate_once, slow_on)           |
+------------------------------------------------------------------------+
```

---

## The Demo's Two Runs

| | Run 1: fabricated citation once | Run 2: slow export sub-agent |
|---|---|---|
| Plan | refund + export sub-questions | same |
| Quarantined | `wiki-export` | `wiki-export` |
| Refund finding | rejected (`pol-made-up`), repaired → `pol-refund` | `pol-refund` |
| Export finding | `pol-export` | timed out → missing |
| Report | both answers with citations | refund answer + "Not verified: …export…" |
| `complete` | True | False |
| Eval | passed (facts 1.0, citations ok) | failed (facts 0.5) |
