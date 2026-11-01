# Lesson 27: Evaluation Pipelines

Learn how to turn evaluation into a release gate: versioned datasets and configs, cached outputs, per-slice results with confidence intervals, a paired regression check in CI, and production traffic sampled back into the dataset.

> **Pillars:** Harness · RAG · Agent  |  **Harness layer:** the release gate, meaning how a prompt, model or retrieval change earns its way to production
>
> *Same examples, same settings, cached outputs, honest intervals, and a gate that checks every slice.*

## Why does this exist?
Lesson 09 built scorers. Running them once in a notebook isn't a pipeline. Every change (a prompt edit, a model upgrade, new chunking, a new tool) should be compared with what's in production, on the same data, **before** it ships. That comparison has to be automatic, cheap enough to run on every pull request, and statistically honest: a one-point wobble shouldn't block a release, and a broken slice shouldn't slip through behind a healthy average.

The same structure is behind public evaluation harnesses (task and dataset registries, model adapters, scorers, cached outputs, aggregated reports). This lesson builds the core of one, small enough to read in a sitting.

---

## What problem does it solve?
1. **Comparability**: a score only means something when the dataset, prompt, model and decoding settings are pinned.
2. **Cost**: re-generating thousands of outputs to add a metric or re-run a report is wasted money.
3. **Noise**: on 40 examples, 85% vs. 87.5% is a single answer. Is the difference real?
4. **Hidden regressions**: averages hide broken slices, whether that's a language, a topic or a customer tier.
5. **Freshness**: golden sets go stale unless production traffic feeds back into them.

---

## How would beginners implement this?
```python
def evaluate(prompt):
    examples = load_examples()
    correct = sum(llm(prompt.format(q=ex.q)) == ex.expected for ex in examples)
    return correct / len(examples)

if evaluate(NEW_PROMPT) >= evaluate(OLD_PROMPT):
    deploy(NEW_PROMPT)
```

### Why does that fail?
1. **Both prompts are re-generated on every run.** You pay twice, and the baseline itself drifts with provider changes and sampling.
2. **Nothing records what produced the number.** Which dataset? Which model version? What temperature? It can't be reproduced.
3. **`>=` on raw means lets noise decide.** On 40 examples, one lucky answer ships a regression, or one unlucky answer blocks an improvement.
4. **There is only one average.** In this lesson's demo the overall score goes *up* 2.5 points while the shipping slice drops 25.
5. **One timeout ends the run**, or, if it's caught and cached as an empty answer, poisons every later run.
6. **There is no path from production back to the dataset.**

---

## How is this solved in production?
**1. Version everything that affects a result.** `Dataset(name, version, examples)` and `RunConfig(model, prompt_version, prompt_template, temperature, seed)`. Results are only ever compared on the same dataset version.

**2. Cache outputs under a complete key:**
```python
def cache_key(self, dataset, example):
    parts = [self.model, self.prompt_version, self.prompt_template, self.temperature, self.seed,
             dataset.name, dataset.version, example.id, example.question]
    return sha256(json.dumps(parts))
```
Change any part and the key changes, so a cached output is never served for a different configuration. The cache is an append-only JSONL file here; in CI it lives in shared storage so every run reuses it.

**3. Never cache failures.** A timed-out call is recorded in `RunStats.errors`, scored as wrong for this run, and retried on the next. In the demo, the second baseline run makes exactly one call, for the item that failed, and serves 39 from the cache.

**4. Score separately from generating.** `score(outputs, dataset, scorer)` takes stored outputs, so adding a metric costs no model calls.

**5. Report per slice, with intervals.** `bootstrap_ci` resamples the examples with replacement 2,000 times and reports the middle 95% of the means. On 40 examples the baseline's 0.850 comes with an interval of about (0.73, 0.95). That width is itself the lesson: small eval sets can't resolve small differences.

**6. A paired gate.** The candidate and the baseline are compared **on the same examples**: each example contributes `candidate − baseline`, and the mean of those deltas is bootstrapped. The gate fails if:
* the whole delta interval is below zero (a drop larger than the noise), or
* any slice with at least `min_slice_n` examples dropped by more than `max_slice_drop`. Smaller slices are reported as notes, not gated, because a single example would swing them.
```
baseline  exact_match 0.850  {'billing': 0.88, 'account': 0.75, 'shipping': 0.92}
candidate exact_match 0.875  {'billing': 0.94, 'account': 1.0,  'shipping': 0.67}
gate passed=False  overall delta +0.025 CI (-0.1, 0.15)
  FAIL: slice 'shipping' dropped 0.25 (n=12)
```

**7. Run it in CI.** Every pull request that touches prompts, retrieval or model configuration runs the gate and exits non-zero on failure (`raise SystemExit(0 if result.passed else 1)`). A common split is a small stratified smoke set on each PR and the full set nightly.

**8. Sample production back in.** `should_sample(trace_id, rate, flagged)` picks a deterministic share of traces by hashing the ID (the same traces every time, whatever order they arrive in), plus every trace already flagged by a thumbs-down, a low judge score or a fallback. Sampled traces go to human review, get labels, and join the golden set. Lesson 22 produces those traces.

---

## Design Tradeoffs

### A smoke set per pull request vs. the full set
* **Smoke set** (a few hundred stratified examples): fast and cheap enough for every change, but too small to see small effects or rare slices.
* **Full set** (nightly or before release): sensitive and complete, but too slow and expensive for every commit. Use both, and make the full run block releases.

### A statistical gate vs. a fixed threshold
* **Fixed threshold** ("stay above 0.85"): simple to explain. It ignores noise, and it lets slow erosion through as long as the line holds.
* **Paired interval** (this lesson): accounts for noise and catches real drops. With very large sets, tiny irrelevant drops become "significant", so pair it with a minimum effect size.

### A cached baseline vs. re-running the baseline
* **Cached**: cheap and stable. Provider-side model changes go unnoticed.
* **Re-run periodically**: measures drift and non-determinism (temperature 0 isn't fully deterministic). It costs a full run, so schedule it, for example weekly.

---

## Failure Cases to Watch For
1. **Unpinned inputs**: the prompt or the dataset changed between the two runs being compared.
2. **Incomplete cache keys**: a key without temperature serves outputs generated under different settings.
3. **Cached failures**: one timeout stored as an empty answer, wrong forever after.
4. **Gating on raw means**: noise decides releases.
5. **Averages only**: a broken language, topic or tier hides behind an improved overall score.
6. **Tuning on the gate set**: prompts optimised against the same examples that gate them. Keep a held-out set that's never used for tuning.
7. **Ignoring non-determinism**: assuming temperature 0 means repeatable. Measure the variance with repeat runs.
8. **A stale golden set**: no sampling from production, while real traffic drifts.
9. **Contamination**: test examples leaking into few-shot prompts or fine-tuning data.

---

## Try It
```sh
cd part-2-production-patterns/27-evaluation-pipelines
python example.py            # fake model; runs in well under a second
python -m pytest tests.py -q
```
What to watch for: the second baseline run makes one call and serves 39 from the cache. Look at how wide the intervals are on 40 examples. The gate fails on the shipping slice even though the candidate's overall score is higher. And 5% sampling picks close to 500 of 10,000 traces.

Experiment: in `gate`, replace `if high < 0:` with `if sum(deltas) < 0:` (fail on any drop) and run the tests. `test_noise_sized_change_passes` fails: swapping one wrong answer for another now blocks a release.

**Next:** Lesson 28 tracks what all of this costs: a per-call ledger, budgets with hard stops, caching, and routing cheap work to cheap models.
