import unittest
from collections import Counter

from example import HOUR, SLO, Alert, AlertManager, Metrics, evaluate_rules, psi


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def run(minutes: int, per_minute: int = 20, start: int = 0, errors_per_minute: int = 0,
        clock: Clock | None = None, metrics: Metrics | None = None, **per_minute_counts: int) -> Metrics:
    clock = clock or Clock()
    metrics = metrics or Metrics(clock)
    for minute in range(start, start + minutes):
        clock.now = minute * 60
        metrics.inc("requests", per_minute)
        metrics.inc("errors", errors_per_minute)
        for name, count in per_minute_counts.items():
            metrics.inc(name, count)
    return metrics


def names(alerts: list[Alert]) -> set[str]:
    return {a.name for a in alerts}


class TestMetrics(unittest.TestCase):
    def test_high_cardinality_labels_are_refused(self) -> None:
        metrics = Metrics(Clock())
        metrics.inc("requests", model="small-model")  # bounded: fine
        with self.assertRaises(ValueError):
            metrics.inc("requests", user_id="u-123")

    def test_windows_only_count_their_own_minutes(self) -> None:
        clock = Clock()
        metrics = run(120, per_minute=10, clock=clock)
        self.assertEqual(metrics.total("requests", HOUR), 600)
        self.assertEqual(metrics.total("requests", 300), 50)

    def test_p95_over_a_window(self) -> None:
        clock = Clock()
        metrics = Metrics(clock)
        for ms in range(1, 101):
            metrics.observe_latency(ms * 10)
        self.assertEqual(metrics.p95(HOUR), 950)


class TestBurnRateAlerts(unittest.TestCase):
    def test_burn_rate_is_error_rate_over_budget(self) -> None:
        self.assertAlmostEqual(SLO(0.99).burn_rate(bad=30, total=100), 30.0)
        self.assertEqual(SLO(0.99).burn_rate(bad=0, total=0), 0.0)

    def test_sustained_outage_pages(self) -> None:
        clock = Clock()
        metrics = run(6 * 60, clock=clock)
        run(60, start=6 * 60, errors_per_minute=6, clock=clock, metrics=metrics)  # 30% for an hour
        self.assertIn("error_budget_burn_page", names(evaluate_rules(metrics, SLO(0.99))))

    def test_short_blip_does_not_page(self) -> None:
        clock = Clock()
        metrics = run(6 * 60, clock=clock)
        run(5, start=6 * 60, errors_per_minute=20, clock=clock, metrics=metrics)  # 5 minutes of 100% errors
        self.assertNotIn("error_budget_burn_page", names(evaluate_rules(metrics, SLO(0.99))))

    def test_alert_clears_soon_after_recovery(self) -> None:
        clock = Clock()
        metrics = run(6 * 60, clock=clock)
        run(60, start=6 * 60, errors_per_minute=6, clock=clock, metrics=metrics)
        run(10, start=7 * 60, clock=clock, metrics=metrics)  # healthy again for 10 minutes
        self.assertGreater(SLO(0.99).burn_rate(metrics.total("errors", HOUR), metrics.total("requests", HOUR)), 14.4)
        self.assertNotIn("error_budget_burn_page", names(evaluate_rules(metrics, SLO(0.99))))  # short window says over


class TestQualityAlerts(unittest.TestCase):
    def test_schema_failures_above_the_limit_alert(self) -> None:
        metrics = run(60, schema_failures=1)  # 1 in 20 = 5%
        self.assertIn("schema_failures_rate", names(evaluate_rules(metrics, SLO())))

    def test_too_few_requests_never_alert(self) -> None:
        metrics = run(1, per_minute=10, schema_failures=5, fallbacks=5)
        self.assertEqual(evaluate_rules(metrics, SLO()), [])

    def test_empty_retrievals_are_watched_too(self) -> None:
        metrics = run(60, empty_retrievals=4)  # 20%: the index no longer covers what people ask
        self.assertIn("empty_retrievals_rate", names(evaluate_rules(metrics, SLO())))


class TestAlertManager(unittest.TestCase):
    def test_one_notification_per_incident_and_one_resolve(self) -> None:
        manager = AlertManager()
        alert = Alert("fallbacks_rate", "page", "fallbacks at 25%")
        for t in range(5):
            manager.update([alert], at=f"t{t}")
        manager.update([], at="t5")
        manager.update([], at="t6")
        self.assertEqual(manager.notifications, ["t0 FIRING [page] fallbacks at 25%", "t5 resolved fallbacks_rate"])


class TestDrift(unittest.TestCase):
    def test_psi_matches_a_hand_computed_value(self) -> None:
        # (0.25 - 0.5) * ln(0.25 / 0.5) + (0.75 - 0.5) * ln(0.75 / 0.5) = 0.1733 + 0.1014
        self.assertAlmostEqual(psi(Counter(a=50, b=50), Counter(a=25, b=75)), 0.2747, places=3)

    def test_psi(self) -> None:
        week = Counter({"billing": 500, "account": 300, "shipping": 200})
        self.assertEqual(psi(week, week), 0.0)
        mild = Counter({"billing": 480, "account": 310, "shipping": 210})
        self.assertLess(psi(week, mild), 0.1)
        launched = Counter({"billing": 380, "account": 260, "shipping": 160, "data_export": 200})
        self.assertGreater(psi(week, launched), 0.25)


if __name__ == "__main__":
    unittest.main()
