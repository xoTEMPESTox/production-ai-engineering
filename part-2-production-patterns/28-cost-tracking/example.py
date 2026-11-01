"""
Lesson 28: Cost Tracking.

Every model call is priced and written to an append-only ledger (tokens x
a versioned price table), tagged with tenant, feature and run, so cost can
be broken down any way you need. Budgets are enforced before money is
spent: each call reserves its worst-case cost, a call that would cross the
limit is refused without being made, and the reservation is settled with
the real cost afterwards. An exact-match cache, a small-then-large
cascade, and a discounted batch mode for work that can wait cut the bill.
"""
from __future__ import annotations

import hashlib
import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional, Protocol

PRICE_TABLE_VERSION = "2026-11-01"
# Illustrative USD prices per million tokens (input, output). Real prices change: version them.
PRICES = {"small-model": (0.15, 0.60), "large-model": (3.00, 15.00)}
BATCH_DISCOUNT = 0.5  # many providers discount asynchronous batch work; check yours


def call_cost(model: str, input_tokens: int, output_tokens: int, batch: bool = False) -> float:
    price_in, price_out = PRICES[model]
    cost = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
    return round(cost * (BATCH_DISCOUNT if batch else 1.0), 8)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)  # rough; use the provider's token counter where it matters


@dataclass(frozen=True)
class LedgerEntry:
    run_id: str
    tenant: str
    feature: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    mode: Literal["live", "batch", "cache"]
    price_version: str = PRICE_TABLE_VERSION


class Ledger:
    """Append-only. Totals are computed from entries, never stored separately."""

    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []

    def record(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    def total(self, **filters: str) -> float:
        return round(sum(e.cost_usd for e in self.entries
                         if all(getattr(e, k) == v for k, v in filters.items())), 8)

    def by(self, field_name: str) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for e in self.entries:
            out[getattr(e, field_name)] += e.cost_usd
        return {k: round(v, 8) for k, v in out.items()}


class BudgetExceeded(Exception):
    pass


@dataclass
class Reservation:
    id: int
    tenant: str
    amount: float


class Budget:
    """Hard stop with reservations: spent + reserved + this call's worst case must fit."""

    def __init__(self, limits: dict[str, float], alert_at: float = 0.8,
                 on_alert: Callable[[str, float], None] = lambda tenant, share: None) -> None:
        self.limits, self.alert_at, self._on_alert = limits, alert_at, on_alert
        self.spent: dict[str, float] = defaultdict(float)
        self._held: dict[int, Reservation] = {}
        self._ids = itertools.count(1)
        self._alerted: set[str] = set()

    def reserved(self, tenant: str) -> float:
        return sum(r.amount for r in self._held.values() if r.tenant == tenant)

    def reserve(self, tenant: str, worst_case: float) -> Reservation:
        limit = self.limits[tenant]
        if self.spent[tenant] + self.reserved(tenant) + worst_case > limit:
            raise BudgetExceeded(f"{tenant}: spent {self.spent[tenant]:.4f} + reserved "
                                 f"{self.reserved(tenant):.4f} + this call {worst_case:.4f} > limit {limit:.4f}")
        reservation = Reservation(next(self._ids), tenant, worst_case)
        self._held[reservation.id] = reservation
        return reservation

    def settle(self, reservation: Reservation, actual: float) -> None:
        del self._held[reservation.id]  # release the hold, record what was really spent
        self.spent[reservation.tenant] += actual
        share = self.spent[reservation.tenant] / self.limits[reservation.tenant]
        if share >= self.alert_at and reservation.tenant not in self._alerted:
            self._alerted.add(reservation.tenant)  # alert once, not on every call after
            self._on_alert(reservation.tenant, share)


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    def complete(self, prompt: str, max_tokens: int) -> Completion: ...


class FakeModel:
    def __init__(self, reply: Callable[[str], str], output_tokens: int = 60) -> None:
        self._reply, self._output_tokens, self.calls = reply, output_tokens, 0

    def complete(self, prompt: str, max_tokens: int) -> Completion:
        self.calls += 1
        return Completion(self._reply(prompt), estimate_tokens(prompt), min(self._output_tokens, max_tokens))


@dataclass
class CostAwareClient:
    models: dict[str, LLMClient]
    ledger: Ledger
    budget: Budget
    cache: dict[str, str] = field(default_factory=dict)

    def complete(self, tenant: str, feature: str, run_id: str, model: str, prompt: str,
                 max_tokens: int = 300, batch: bool = False) -> str:
        key = hashlib.sha256(f"{model}|{max_tokens}|{prompt}".encode()).hexdigest()
        if key in self.cache:  # a repeat costs nothing, and the ledger says so
            self.ledger.record(LedgerEntry(run_id, tenant, feature, model, 0, 0, 0.0, "cache"))
            return self.cache[key]
        worst_case = call_cost(model, estimate_tokens(prompt), max_tokens, batch)
        reservation = self.budget.reserve(tenant, worst_case)  # raises before any money is spent
        try:
            result = self.models[model].complete(prompt, max_tokens)
        except Exception:
            self.budget.settle(reservation, 0.0)  # nothing billed (record partial usage if your provider bills it)
            raise
        actual = call_cost(model, result.input_tokens, result.output_tokens, batch)
        self.budget.settle(reservation, actual)
        self.ledger.record(LedgerEntry(run_id, tenant, feature, model, result.input_tokens,
                                       result.output_tokens, actual, "batch" if batch else "live"))
        self.cache[key] = result.text
        return result.text

    def cascade(self, tenant: str, feature: str, run_id: str, prompt: str,
                accept: Callable[[str], bool]) -> tuple[str, str]:
        """Cheap model first; the expensive one only when a check rejects the cheap answer."""
        answer = self.complete(tenant, feature, run_id, "small-model", prompt)
        if accept(answer):
            return answer, "small-model"
        return self.complete(tenant, feature, run_id, "large-model", prompt), "large-model"


def has_citation(answer: str) -> bool:
    return "[" in answer and "]" in answer


if __name__ == "__main__":
    small = FakeModel(lambda p: "Refunds within 30 days [billing-04]." if "refund" in p else "Not sure.")
    large = FakeModel(lambda p: "Exports are under Settings > Export [privacy-02].", output_tokens=120)
    alerts: list[str] = []
    client = CostAwareClient({"small-model": small, "large-model": large}, Ledger(),
                             Budget({"acme": 0.02}, alert_at=0.5, on_alert=lambda t, s: alerts.append(f"{t} at {s:.0%}")))

    questions = ["How do refunds work for annual plans?", "How do I export my data?",
                 "How do refunds work for annual plans?"]
    for i, question in enumerate(questions):
        answer, model = client.cascade("acme", "support_chat", f"run-{i}", question * 20, has_citation)
        print(f"{question:<40} -> {model:<12} {answer}")
    ledger = client.ledger
    print("\nledger:", [(e.model, e.mode, e.cost_usd) for e in ledger.entries])
    always_large = sum(call_cost("large-model", estimate_tokens(q * 20), 120) for q in dict.fromkeys(questions))
    print(f"spent ${ledger.total(tenant='acme'):.5f} vs ${always_large:.5f} sending each distinct question "
          "to the large model")

    try:
        for i in range(100):
            client.complete("acme", "report", f"bulk-{i}", "large-model", f"Summarise ticket {i} " * 50)
    except BudgetExceeded as exc:
        print(f"\nhard stop after {i} report calls: {exc}")
    print("alerts:", alerts, "| by feature:", ledger.by("feature"))
    print(f"\n100 eval items live: ${100 * call_cost('small-model', 800, 60):.4f}, "
          f"batch: ${100 * call_cost('small-model', 800, 60, batch=True):.4f}")
