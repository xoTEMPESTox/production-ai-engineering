"""
Lesson 27: Evaluation Pipelines.

An offline eval harness that can gate a release. Datasets and run configs
are versioned; model outputs are cached under a key built from everything
that affects them, so re-running is free and a new metric re-scores without
re-generating; failed calls are never cached. Results are reported per
slice with bootstrap confidence intervals, and the regression gate compares a
candidate with the baseline on the same examples: it fails on a drop larger
than the noise, or on a slice that broke while the average looked fine.
Production traffic is sampled into the review queue deterministically.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol


@dataclass(frozen=True)
class Example:
    id: str
    question: str
    expected: str
    slice: str  # topic, language, customer tier: whatever a regression could hide in


@dataclass(frozen=True)
class Dataset:
    name: str
    version: str  # scores are only comparable on the same version
    examples: tuple[Example, ...]


@dataclass(frozen=True)
class RunConfig:
    model: str
    prompt_version: str
    prompt_template: str
    temperature: float = 0.0
    seed: int = 0

    def cache_key(self, dataset: Dataset, example: Example) -> str:
        """Everything that can change the output is in the key, and nothing else."""
        parts = [self.model, self.prompt_version, self.prompt_template, self.temperature, self.seed,
                 dataset.name, dataset.version, example.id, example.question]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:24]


class ModelAdapter(Protocol):
    def complete(self, prompt: str, temperature: float, seed: int) -> str: ...


class OutputCache:
    """Raw outputs keyed by RunConfig.cache_key, appended to a JSONL file if a path is given."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path
        self._rows: dict[str, str] = {}
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        row = json.loads(line)
                        self._rows[row["key"]] = row["output"]
            except FileNotFoundError:
                pass

    def get(self, key: str) -> Optional[str]:
        return self._rows.get(key)

    def put(self, key: str, output: str) -> None:
        self._rows[key] = output
        if self._path:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "output": output}, ensure_ascii=False) + "\n")


@dataclass
class RunStats:
    model_calls: int = 0
    cache_hits: int = 0
    errors: list[str] = field(default_factory=list)


def generate(model: ModelAdapter, config: RunConfig, dataset: Dataset,
             cache: OutputCache) -> tuple[dict[str, str], RunStats]:
    outputs, stats = {}, RunStats()
    for ex in dataset.examples:
        key = config.cache_key(dataset, ex)
        cached = cache.get(key)
        if cached is not None:
            outputs[ex.id], stats.cache_hits = cached, stats.cache_hits + 1
            continue
        stats.model_calls += 1
        try:
            output = model.complete(config.prompt_template.format(question=ex.question),
                                    config.temperature, config.seed)
        except Exception as exc:  # one bad call must not end a long run
            stats.errors.append(f"{ex.id}: {type(exc).__name__}: {exc}")
            outputs[ex.id] = ""   # scored as wrong this time, and retried next time
            continue
        cache.put(key, output)    # only successful outputs are cached
        outputs[ex.id] = output
    return outputs, stats


# --- Scoring is separate from generation ---

Scorer = Callable[[str, Example], float]


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def exact_match(output: str, ex: Example) -> float:
    return float(_norm(output) == _norm(ex.expected))


def score(outputs: dict[str, str], dataset: Dataset, scorer: Scorer) -> dict[str, float]:
    return {ex.id: scorer(outputs[ex.id], ex) for ex in dataset.examples}


def bootstrap_ci(values: list[float], resamples: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% interval for the mean, by resampling the examples with replacement."""
    rng, n = random.Random(seed), len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return round(means[int(0.025 * resamples)], 3), round(means[int(0.975 * resamples) - 1], 3)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    overall_delta: float
    delta_ci: tuple[float, float]
    reasons: tuple[str, ...]
    notes: tuple[str, ...] = ()


def gate(baseline: dict[str, float], candidate: dict[str, float], dataset: Dataset,
         max_slice_drop: float = 0.10, min_slice_n: int = 8) -> GateResult:
    """Paired comparison on the same examples. Fail on a drop larger than the
    noise overall, or on any big enough slice that dropped by more than max_slice_drop."""
    ids = [ex.id for ex in dataset.examples]
    deltas = [candidate[i] - baseline[i] for i in ids]
    low, high = bootstrap_ci(deltas)
    reasons, notes = [], []
    if high < 0:
        reasons.append(f"overall drop {sum(deltas) / len(deltas):+.3f} is beyond the noise (95% interval {low}, {high})")
    for name in sorted({ex.slice for ex in dataset.examples}):
        members = [ex.id for ex in dataset.examples if ex.slice == name]
        drop = sum(baseline[i] - candidate[i] for i in members) / len(members)
        if len(members) < min_slice_n:
            notes.append(f"slice {name!r} has {len(members)} examples: too few to gate on")
        elif drop > max_slice_drop:
            reasons.append(f"slice {name!r} dropped {drop:.2f} (n={len(members)})")
    return GateResult(not reasons, round(sum(deltas) / len(deltas), 3), (low, high), tuple(reasons), tuple(notes))


def should_sample(trace_id: str, rate: float, flagged: bool = False) -> bool:
    """Online sampling into the review queue: deterministic per trace (re-runs
    pick the same traces), plus every trace already flagged (low score, user complaint)."""
    bucket = int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return flagged or bucket < rate


# --- Demo data and a fake model ---

def build_dataset() -> Dataset:
    sizes = {"billing": 16, "account": 12, "shipping": 12}
    examples = tuple(Example(f"{s}-{i}", f"{s} question {i}", f"{s} answer {i}", s)
                     for s, n in sizes.items() for i in range(n))
    return Dataset("support-golden", "2026-11-01", examples)


class FakeModel:
    """Answers correctly except for the questions it's configured to miss."""

    def __init__(self, misses: set[str], flaky: Optional[set[str]] = None) -> None:
        self._misses, self._flaky, self.calls = misses, set(flaky or ()), 0

    def complete(self, prompt: str, temperature: float, seed: int) -> str:
        self.calls += 1
        question = prompt.split("Q: ")[1].split("\n")[0]
        if question in self._flaky:
            self._flaky.discard(question)  # fails once, then works
            raise TimeoutError("provider timed out")
        return "not sure" if question in self._misses else question.replace("question", "answer")


V1 = RunConfig("support-model", "v1", "Answer the customer.\nQ: {question}\nA:")
V2 = RunConfig("support-model", "v2", "Answer briefly and cite the policy.\nQ: {question}\nA:")
BASELINE_MISSES = {"billing question 3", "billing question 7", "account question 2", "account question 5",
                   "account question 9", "shipping question 4"}
CANDIDATE_MISSES = {"billing question 3", "shipping question 1", "shipping question 4", "shipping question 6",
                    "shipping question 8"}

if __name__ == "__main__":
    data, cache = build_dataset(), OutputCache()
    base_model = FakeModel(BASELINE_MISSES, flaky={"billing question 0"})
    base_out, stats = generate(base_model, V1, data, cache)
    print(f"baseline v1: {stats.model_calls} calls, {stats.cache_hits} cached, errors {stats.errors}")
    base_out, stats = generate(base_model, V1, data, cache)
    print(f"baseline v1 again: {stats.model_calls} call(s) (only the failed one), {stats.cache_hits} cached")

    cand_out, _ = generate(FakeModel(CANDIDATE_MISSES), V2, data, cache)
    base, cand = score(base_out, data, exact_match), score(cand_out, data, exact_match)
    for name, scores in (("baseline", base), ("candidate", cand)):
        mean = sum(scores.values()) / len(scores)
        by_slice = {s: round(sum(scores[e.id] for e in data.examples if e.slice == s) /
                             sum(e.slice == s for e in data.examples), 2) for s in ("billing", "account", "shipping")}
        print(f"{name:<9} exact_match {mean:.3f} 95% interval {bootstrap_ci(list(scores.values()))}  {by_slice}")

    result = gate(base, cand, data)
    print(f"\ngate passed={result.passed}  overall delta {result.overall_delta:+.3f} 95% interval {result.delta_ci}")
    for reason in result.reasons:
        print("  FAIL:", reason)
    print("  (in continuous integration: raise SystemExit(0 if result.passed else 1))")

    sampled = sum(should_sample(f"trace-{i}", rate=0.05) for i in range(10_000))
    print(f"\nonline sampling at 5%: {sampled} of 10,000 traces; flagged traces always included")
