# Lesson 09: Evaluation & Self-Reflection

Learn how to measure an AI system per component: deterministic scorers first, an LLM judge only where nothing exact exists (calibrated before it's trusted), and completeness checked outside the model.

> **Pillars:** Harness · RAG · Agent  |  **Harness layer:** evaluation, which is how you know the rest works
>
> *Count what you can, judge only what you can't, and never let the model grade its own homework.*

## Why does this exist?
Every lesson so far has changed behaviour: prompts, retrieval, tools, plans. Without measurement you can't tell whether a change helped. "Looks good on five examples" is how a regression reaches users on the sixth.

Evaluation turns quality into numbers you can compare, broken down by component: did retrieval find the evidence, did the answer use it, is anything missing? This lesson builds the scorers and the judgement about which ones to trust. Lesson 27 turns them into a pipeline with versioned datasets, confidence intervals and a CI gate.

---

## What problem does it solve?
1. **Knowing which component failed.** A wrong RAG answer can be a retrieval failure (the right chunk was never found) or a generation failure (it was found and ignored). The fixes are completely different.
2. **Measuring what has no exact reference**: helpfulness, clarity, whether the conditions that matter were stated.
3. **Trusting the measurement.** A judge model has biases, and a self-review shares the blind spots of the answer it reviews.
4. **Silent omission.** Asked to list everything that matches, models drop items, and their output gives no sign that anything is missing.

---

## How would beginners implement this?
```python
def evaluate(examples):
    scores = []
    for ex in examples:
        answer = system(ex.question)
        verdict = llm(f"Is this a good answer to '{ex.question}'? {answer} Reply 1-10.")
        scores.append(int(verdict))
    return sum(scores) / len(scores)  # "8.1 out of 10, ship it"
```

### Why does that fail?
1. **One number for the whole system.** Retrieval and generation failures blend together, so you don't know what to fix.
2. **An unanchored 1–10 scale.** The judge's "8" has no stable meaning, and it drifts with phrasing.
3. **Judge biases.** Judge models tend to prefer longer answers, the answer they read first, and answers in their own style, and they miss confident-sounding factual errors.
4. **No comparison with people.** Nothing shows that the judge agrees with a human reviewer.
5. **A judge where an exact check exists.** "Does the answer say 30 days?" is a string check: faster, free and repeatable.
6. **Completeness is never checked.** A list missing one item scores the same as a complete one. (And `int("8/10")` crashes.)

---

## How is this solved in production?
**1. A golden set with labels per component.** Each `GoldenCase` holds the question, the chunk IDs that answer it (retrieval labels), the facts a correct answer must contain, and, for "list every…" questions, the expected items. Build it from real traffic (Lesson 22 traces) and include the cases that have failed before. A small, specific set beats a large, vague one.

**2. Deterministic scorers first.**

| Scorer | Measures | Example |
|---|---|---|
| Recall@k | share of the relevant chunks in the top k | 10 relevant, 4 in the top 5 → 0.4 |
| Precision@k | share of the top k that are relevant | 4 of the top 5 → 0.8 |
| Reciprocal rank (MRR, averaged) | 1 / rank of the first relevant chunk | first relevant at rank 2 → 0.5 |
| Required facts | normalised phrase present in the answer | "30 days" in "within 30 Days!" |
| Grounded | cites at least one source, and only retrieved ones | Lesson 03's check, reused |
| Missing items | expected set minus returned set, in code | `['inv-1044']` |

**3. A diagnosis for every failing case.** `ScoreCard.diagnosis` names the component to fix, checking retrieval first because the model can't use what it never saw:
```
case  R@3   RR    facts  grounded  diagnosis
g1    1.0   1.0   1.0    True      pass
g2    0.0   0.0   0.0    True      retrieval_miss
g3    1.0   1.0   1.0    True      incomplete ['inv-1044']
```
Note g2. It is "grounded", because it cites a chunk that was retrieved, yet it is wrong, because the right chunk never was. Grounded is necessary, not sufficient.

**4. Completeness outside the model.** In g3 the system lists four of five overdue invoices. Asked to review itself, the model says `{"complete": true, "count": 4}`. Its count matches its own partial list, so self-consistency reveals nothing. Long-input aggregation tasks often fail exactly like this: everything returned is correct, a stretch of the input is missing, and nothing in the output hints at it. Only a check against an independent source of truth (the expected set, a database count) finds the gap.

**5. An LLM judge only where nothing exact exists, and used carefully:**
* **An anchored rubric**: each score from 1 to 5 is described ("4: correct, but misses a condition the customer needs"), and the reply is validated (`score` must be 1–5).
* **Pairwise comparisons run in both orders.** `judge_pairwise` asks "A or B?" and then "B or A?". If the verdicts disagree, the comparison is `inconsistent` and discarded, and the share of inconsistent comparisons measures the judge's position bias. The demo's judge always picks whichever answer it read first, and is caught.

**6. Calibrate the judge before trusting it.** Score a sample that people have also labelled, and compute Cohen's kappa, which is agreement beyond chance. A judge that says "pass" to everything agrees with humans 90% of the time when 90% of answers pass, yet its kappa is 0. `calibrate` trusts a judge only above a threshold (0.6 here, a common convention), and the check is repeated whenever the judge model or the rubric changes.

**7. Self-reflection as a signal, not a verdict.** Asking the model to check its own work is a cheap way to trigger a retry for formatting slips or obvious mistakes. It never decides pass or fail, because the reviewer shares the author's blind spots.

The same checks can serve as an agent's **goal gate**: a run isn't "done" (Lesson 06) until the deterministic checks on its output pass.

---

## Design Tradeoffs

### Deterministic scorers vs. an LLM judge
* **Deterministic**: free, instant, repeatable and explainable. They only measure what can be specified exactly: IDs, phrases, sets, schemas.
* **LLM judge**: covers qualities with no exact reference, but costs a call per item, is noisy and biased, and needs calibration and re-calibration. Use it for the remainder, not as the default.

### Pointwise vs. pairwise judging
* **Pointwise** (score one answer on a rubric): gives absolute numbers you can track over time, but scales drift and judges cluster scores in the middle.
* **Pairwise** (A vs. B, both orders): more sensitive to small differences, and the right tool for "is the new prompt better?". It needs two calls per comparison and gives only relative answers.

### Self-critique vs. external verification
* **Self-critique**: cheap, with no extra infrastructure. It catches some slips but shares the author's blind spots, especially omissions.
* **External verification** (code checks, a different model, a person): independent, but each needs a source of truth or a reviewer. Spend it on the failure modes that matter most.

---

## Failure Cases to Watch For
1. **One blended score**: an average that hides whether retrieval or generation regressed.
2. **An uncalibrated judge**: scores nobody has compared with human labels.
3. **Raw agreement on imbalanced labels**: 90% agreement that is really a judge that always says yes. Report kappa.
4. **Position and length bias**: single-order pairwise judging, and rubrics that reward length.
5. **Self-grading**: the model that wrote the answer decides whether it passed.
6. **Unmeasured omission**: list and aggregation tasks scored only on what was returned, never on what is missing.
7. **Overfitting the golden set**: prompts tuned on the same cases you report. Keep a held-out split.
8. **Stale labels**: re-chunking changes chunk IDs, so relevance labels must be versioned with the index (Lesson 05).
9. **Too few cases**: with 20 cases, one flip moves the score five points. Confidence intervals are in Lesson 27.

---

## Try It
```sh
cd part-1-foundations/09-evaluation-self-reflection
python example.py            # scripted judge; no API key needed
python -m pytest tests.py -q
```
What to watch for: the diagnosis column, which names a different component for each failing case. g3's self-review claims completeness while the external check names `inv-1044`. The position-biased judge comes out `inconsistent`. And the always-pass judge shows 0.9 agreement with kappa 0.0, so it is not trusted.

Experiment: in `judge_pairwise`, ask only once (`winners = {ask(a, b)}`) and run the tests. The biased judge now "prefers" whichever answer you list first, and `test_position_biased_judge_is_caught_by_the_swap` fails.

**Next:** Lesson 10 makes work outlive a single request: scheduled jobs, leases and heartbeats, and steps that resume from a checkpoint.
