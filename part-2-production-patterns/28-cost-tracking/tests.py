import unittest

from example import (
    PRICE_TABLE_VERSION, Budget, BudgetExceeded, CostAwareClient, FakeModel, Ledger, call_cost, has_citation,
)


def make_client(limit: float = 1.0, small_reply: str = "ok [doc-1]", alerts: list | None = None):
    small = FakeModel(lambda p: small_reply)
    large = FakeModel(lambda p: "thorough [doc-2]", output_tokens=120)
    on_alert = (lambda t, s: alerts.append((t, round(s, 2)))) if alerts is not None else (lambda t, s: None)
    client = CostAwareClient({"small-model": small, "large-model": large}, Ledger(),
                             Budget({"acme": limit, "globex": limit}, on_alert=on_alert))
    return client, small, large


class TestPricingAndLedger(unittest.TestCase):
    def test_cost_is_tokens_times_price(self) -> None:
        self.assertAlmostEqual(call_cost("large-model", 1_000_000, 1_000_000), 18.0)
        self.assertAlmostEqual(call_cost("small-model", 2_000, 500), (2_000 * 0.15 + 500 * 0.60) / 1e6)

    def test_batch_mode_is_discounted(self) -> None:
        self.assertAlmostEqual(call_cost("small-model", 800, 60, batch=True), call_cost("small-model", 800, 60) / 2)

    def test_every_call_is_recorded_with_its_price_version(self) -> None:
        client, _, _ = make_client()
        client.complete("acme", "chat", "r1", "small-model", "hello")
        entry = client.ledger.entries[0]
        self.assertEqual((entry.tenant, entry.feature, entry.mode, entry.price_version),
                         ("acme", "chat", "live", PRICE_TABLE_VERSION))
        self.assertGreater(entry.cost_usd, 0)

    def test_breakdowns_by_any_field(self) -> None:
        client, _, _ = make_client()
        client.complete("acme", "chat", "r1", "small-model", "a")
        client.complete("globex", "report", "r2", "large-model", "b")
        self.assertEqual(set(client.ledger.by("tenant")), {"acme", "globex"})
        self.assertEqual(client.ledger.total(feature="report"), client.ledger.total(tenant="globex"))


class TestBudgets(unittest.TestCase):
    def test_hard_stop_happens_before_the_call(self) -> None:
        client, _, large = make_client(limit=0.004)
        with self.assertRaises(BudgetExceeded):
            client.complete("acme", "report", "r1", "large-model", "x " * 400, max_tokens=300)
        self.assertEqual(large.calls, 0)  # nothing was spent
        self.assertEqual(client.ledger.entries, [])

    def test_reservation_is_replaced_by_the_actual_cost(self) -> None:
        client, _, _ = make_client()
        client.complete("acme", "chat", "r1", "large-model", "hello", max_tokens=300)
        self.assertEqual(client.budget.reserved("acme"), 0)
        self.assertAlmostEqual(client.budget.spent["acme"], client.ledger.total(tenant="acme"))
        self.assertLess(client.budget.spent["acme"], call_cost("large-model", 2, 300))  # less than worst case

    def test_reservations_stop_concurrent_calls_overspending(self) -> None:
        budget = Budget({"acme": 0.010})
        budget.reserve("acme", 0.006)  # first call in flight
        with self.assertRaises(BudgetExceeded):
            budget.reserve("acme", 0.006)  # second would overspend if the first costs its worst case

    def test_alert_fires_once_when_crossing_the_threshold(self) -> None:
        alerts: list = []
        budget = Budget({"acme": 1.0}, alert_at=0.8, on_alert=lambda t, s: alerts.append(t))
        for amount in (0.5, 0.35, 0.1):
            budget.settle(budget.reserve("acme", amount), amount)
        self.assertEqual(alerts, ["acme"])

    def test_failed_call_releases_its_reservation(self) -> None:
        class Broken:
            def complete(self, prompt: str, max_tokens: int):
                raise TimeoutError("slow")

        client, _, _ = make_client()
        client.models["small-model"] = Broken()
        with self.assertRaises(TimeoutError):
            client.complete("acme", "chat", "r1", "small-model", "hi")
        self.assertEqual((client.budget.reserved("acme"), client.budget.spent["acme"]), (0, 0))

    def test_tenants_have_separate_budgets(self) -> None:
        client, _, _ = make_client(limit=0.004)
        with self.assertRaises(BudgetExceeded):
            client.complete("acme", "r", "r1", "large-model", "x " * 400)
        self.assertEqual(client.complete("globex", "r", "r2", "small-model", "hi"), "ok [doc-1]")


class TestSavings(unittest.TestCase):
    def test_repeat_is_free_and_recorded_as_a_cache_hit(self) -> None:
        client, small, _ = make_client()
        client.complete("acme", "chat", "r1", "small-model", "same question")
        client.complete("acme", "chat", "r2", "small-model", "same question")
        self.assertEqual(small.calls, 1)
        self.assertEqual([(e.mode, e.cost_usd) for e in client.ledger.entries][1], ("cache", 0.0))

    def test_cache_does_not_cross_models(self) -> None:
        client, small, large = make_client()
        client.complete("acme", "chat", "r1", "small-model", "q")
        client.complete("acme", "chat", "r2", "large-model", "q")
        self.assertEqual((small.calls, large.calls), (1, 1))

    def test_cascade_stops_at_the_cheap_model_when_it_passes(self) -> None:
        client, _, large = make_client(small_reply="fine [doc-1]")
        _, model = client.cascade("acme", "chat", "r1", "q", has_citation)
        self.assertEqual((model, large.calls), ("small-model", 0))

    def test_cascade_escalates_and_pays_for_both_when_rejected(self) -> None:
        client, _, large = make_client(small_reply="not sure")
        answer, model = client.cascade("acme", "chat", "r1", "q", has_citation)
        self.assertEqual((model, answer, large.calls), ("large-model", "thorough [doc-2]", 1))
        self.assertEqual([e.model for e in client.ledger.entries], ["small-model", "large-model"])


if __name__ == "__main__":
    unittest.main()
