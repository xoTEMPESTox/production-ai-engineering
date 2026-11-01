"""
Lesson 26: Monitoring.

Knowing the system is healthy without reading every trace. Metrics are
counted per minute with a small, fixed set of labels (no user ids: that
would explode cardinality). An SLO turns "reliable enough" into an error
budget, and alerts fire on how fast the budget is burning, checked over a
long and a short window so they're both sensitive and quick to clear.
AI-specific quality signals (schema failures, fallbacks, empty retrievals,
and the human edit rate) get their own alerts with a minimum volume, each incident alerts once, and
input drift is measured with the population stability index (PSI).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Literal

ALLOWED_LABELS = {"route", "model", "prompt_version"}  # bounded sets of values only


class Metrics:
    """Per-minute counters and latency samples. The same shape as a time-series store, in memory."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        # (name, minute) -> {label combination: count}. Each combination is its own series.
        self._counts: dict[tuple[str, int], dict[tuple[tuple[str, str], ...], float]] = defaultdict(
            lambda: defaultdict(float))
        self._latencies: dict[int, list[float]] = defaultdict(list)

    @staticmethod
    def _check(labels: dict[str, str]) -> None:
        bad = set(labels) - ALLOWED_LABELS
        if bad:  # a label per user or per request id creates a new time series for each one
            raise ValueError(f"label(s) {sorted(bad)} not allowed: high-cardinality data belongs on traces")

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        self._check(labels)
        series = tuple(sorted(labels.items()))
        self._counts[(name, int(self._clock() // 60))][series] += value

    def observe_latency(self, ms: float, **labels: str) -> None:
        self._check(labels)
        self._latencies[int(self._clock() // 60)].append(ms)

    def total(self, name: str, window_s: float, **labels: str) -> float:
        """Sum over the window, across every series whose labels include `labels`."""
        now = int(self._clock() // 60)
        wanted = set(labels.items())
        total = 0.0
        for minute in range(now - int(window_s // 60) + 1, now + 1):
            for series, value in self._counts.get((name, minute), {}).items():
                if wanted <= set(series):
                    total += value
        return total

    def p95(self, window_s: float) -> float:
        now = int(self._clock() // 60)
        samples = sorted(x for m in range(now - int(window_s // 60) + 1, now + 1) for x in self._latencies.get(m, []))
        return samples[max(0, math.ceil(0.95 * len(samples)) - 1)] if samples else 0.0


@dataclass(frozen=True)
class SLO:
    objective: float = 0.99  # 99% of requests succeed, over the SLO period

    def burn_rate(self, bad: float, total: float) -> float:
        """1.0 = spending the error budget exactly as fast as the period allows."""
        return (bad / total) / (1 - self.objective) if total else 0.0


@dataclass(frozen=True)
class Alert:
    name: str
    severity: Literal["page", "ticket"]
    message: str


HOUR = 3600.0


def evaluate_rules(m: Metrics, slo: SLO, min_requests: int = 200) -> list[Alert]:
    alerts: list[Alert] = []
    # Burn-rate alerts: the long window says it's real, the short window says it's still happening.
    for severity, long_s, short_s, threshold in (("page", HOUR, 300, 14.4), ("ticket", 6 * HOUR, 1800, 6.0)):
        long_rate = slo.burn_rate(m.total("errors", long_s), m.total("requests", long_s))
        short_rate = slo.burn_rate(m.total("errors", short_s), m.total("requests", short_s))
        if long_rate > threshold and short_rate > threshold:
            alerts.append(Alert(f"error_budget_burn_{severity}", severity,
                                f"burning the error budget {long_rate:.1f}x too fast over {long_s / HOUR:.0f}h"))
    requests = m.total("requests", HOUR)
    if requests >= min_requests:  # no verdicts on a handful of requests
        for name, limit, severity in (("schema_failures", 0.02, "ticket"), ("fallbacks", 0.10, "page"),
                                      ("empty_retrievals", 0.15, "ticket")):
            rate = m.total(name, HOUR) / requests
            if rate > limit:
                alerts.append(Alert(f"{name}_rate", severity, f"{name} at {rate:.1%} over the last hour (limit {limit:.0%})"))
        reviewed = m.total("drafts_reviewed", HOUR)
        if reviewed >= 50:  # share of reviewed drafts a person changed (Lesson 08)
            edit_rate = m.total("drafts_edited", HOUR) / reviewed
            if edit_rate > 0.30:
                alerts.append(Alert("human_edit_rate", "ticket",
                                    f"reviewers changed {edit_rate:.0%} of drafts over the last hour (limit 30%)"))
        if m.p95(HOUR) > 3000:
            alerts.append(Alert("latency_p95", "ticket", f"p95 {m.p95(HOUR):.0f} ms over the last hour (SLO 3000 ms)"))
    return alerts


class AlertManager:
    """One notification per incident: fire on the transition to firing, resolve when it clears."""

    def __init__(self) -> None:
        self.active: dict[str, Alert] = {}
        self.notifications: list[str] = []

    def update(self, alerts: list[Alert], at: str) -> None:
        current = {a.name: a for a in alerts}
        for name, alert in current.items():
            if name not in self.active:
                self.notifications.append(f"{at} FIRING [{alert.severity}] {alert.message}")
        for name in set(self.active) - set(current):
            self.notifications.append(f"{at} resolved {name}")
        self.active = current


def psi(reference: Counter, current: Counter, floor: float = 1e-4) -> float:
    """Population stability index between two category distributions.
    Rule of thumb: < 0.1 stable, 0.1-0.25 shifting, > 0.25 a real shift."""
    ref_total, cur_total = sum(reference.values()), sum(current.values())
    score = 0.0
    for category in set(reference) | set(current):
        r = max(reference[category] / ref_total, floor)
        c = max(current[category] / cur_total, floor)
        score += (c - r) * math.log(c / r)
    return round(score, 4)


if __name__ == "__main__":
    now = [0.0]  # a fake clock: the demo moves time by setting now[0] (a list, so lambdas see the change)
    metrics, slo, manager = Metrics(clock=lambda: now[0]), SLO(0.99), AlertManager()
    try:
        metrics.inc("requests", user_id="u-123")
    except ValueError as exc:
        print("refused:", exc)

    for minute in range(48 * 60):  # two days, 20 requests a minute
        now[0] = minute * 60
        hour = minute / 60
        outage = 30 <= hour < 32         # provider degraded
        bad_prompt = 40 <= hour < 44     # a prompt change that breaks the JSON
        worse_model = 45 <= hour < 47    # valid JSON, no errors, but weaker answers
        for i in range(20):
            metrics.inc("requests")
            metrics.observe_latency(1200 + (i * 37) % 900 + (2500 if outage else 0))
            if outage and i % 10 < 3:  # 30% of requests fail
                metrics.inc("errors")
            if outage and i % 4 == 0:
                metrics.inc("fallbacks")
            if bad_prompt and i % 12 == 0:
                metrics.inc("schema_failures")
            if i % 4 == 1:  # a person reviews 1 draft in 4
                metrics.inc("drafts_reviewed")
                if i == 1 or (worse_model and i < 10):  # usually 1 review in 5 changes the draft; 3 in 5 here
                    metrics.inc("drafts_edited")
        if minute % 5 == 4:
            manager.update(evaluate_rules(metrics, slo), at=f"h{hour:05.2f}")
    for note in manager.notifications:
        print(note)

    last_week = Counter({"billing": 500, "account": 300, "shipping": 200})
    this_week = Counter({"billing": 380, "account": 260, "shipping": 160, "data_export": 200})  # new feature launched
    print(f"\ntopic drift PSI: {psi(last_week, last_week)} (same week) vs {psi(last_week, this_week)} "
          "(after the launch): retrieval and evals need the new topic")
