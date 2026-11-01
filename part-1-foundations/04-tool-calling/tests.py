import time
import unittest

from pydantic import BaseModel

from example import (
    CloseArgs, FakeBilling, Outcome, SearchArgs, Tier, ToolContext, ToolRegistry, ToolSpec,
    build_registry, idempotency_key,
)

REFUND = {"account_id": "acct-17", "amount_cents": 1999, "reason": "duplicate charge"}


class TestValidationAndPermissions(unittest.TestCase):
    def test_schema_the_model_sees_carries_the_constraints(self) -> None:
        schema = build_registry(FakeBilling()).schemas()[1]
        params = schema["parameters"]
        self.assertEqual(schema["name"], "issue_refund")
        self.assertEqual(sorted(params["required"]), ["account_id", "amount_cents", "reason"])
        self.assertEqual(params["properties"]["amount_cents"]["maximum"], 50_000)

    def test_invalid_arguments_never_reach_the_handler(self) -> None:
        billing = FakeBilling()
        result = build_registry(billing).call("issue_refund", {**REFUND, "amount_cents": -5}, "r1")

        self.assertEqual(result.outcome, Outcome.INVALID_ARGS)
        self.assertIn("amount_cents", result.content)
        self.assertEqual(billing.refunds, {})

    def test_unexpected_argument_is_rejected(self) -> None:
        result = build_registry(FakeBilling()).call("issue_refund", {**REFUND, "force": True}, "r1")
        self.assertEqual(result.outcome, Outcome.INVALID_ARGS)

    def test_unknown_tool_lists_the_real_ones(self) -> None:
        result = build_registry(FakeBilling()).call("drop_database", {}, "r1")
        self.assertEqual(result.outcome, Outcome.UNKNOWN_TOOL)
        self.assertIn("close_account, issue_refund, search_docs", result.content)

    def test_destructive_tool_is_denied_without_approval(self) -> None:
        billing = FakeBilling()
        result = build_registry(billing).call("close_account", {"account_id": "acct-17"}, "r1")

        self.assertEqual(result.outcome, Outcome.DENIED)
        self.assertEqual(billing.closed, [])

    def test_approver_sees_validated_arguments(self) -> None:
        seen: list[BaseModel] = []

        def approve(tool: str, args: BaseModel) -> bool:
            seen.append(args)
            return True

        billing = FakeBilling()
        result = build_registry(billing, approver=approve).call("close_account", {"account_id": "acct-17"}, "r1")

        self.assertEqual(result.outcome, Outcome.OK)
        self.assertIsInstance(seen[0], CloseArgs)
        self.assertEqual(billing.closed, ["acct-17"])


class TestSideEffects(unittest.TestCase):
    def test_repeated_write_in_a_run_executes_once(self) -> None:
        billing = FakeBilling()
        tools = build_registry(billing)
        first = tools.call("issue_refund", REFUND, "run-1")
        again = tools.call("issue_refund", dict(reversed(list(REFUND.items()))), "run-1")

        self.assertEqual(first.outcome, Outcome.OK)
        self.assertEqual(again.outcome, Outcome.REPLAYED)
        self.assertEqual(again.content, first.content)
        self.assertEqual(len(billing.refunds), 1)

    def test_reads_are_never_replayed(self) -> None:
        statuses = iter(["pending", "completed"])

        def refund_status(args: BaseModel, ctx: ToolContext) -> str:
            return next(statuses)

        tools = ToolRegistry()
        tools.register(ToolSpec("refund_status", "Status.", SearchArgs, refund_status))
        first = tools.call("refund_status", {"query": "acct-17"}, "run-1")
        second = tools.call("refund_status", {"query": "acct-17"}, "run-1")

        self.assertEqual((first.content, second.content), ("pending", "completed"))
        self.assertEqual(second.outcome, Outcome.OK)

    def test_a_different_run_gets_a_different_key(self) -> None:
        args = SearchArgs(query="refund")
        self.assertNotEqual(idempotency_key("run-1", "t", args), idempotency_key("run-2", "t", args))

    def test_failed_write_is_not_cached_as_done(self) -> None:
        attempts: list[int] = []

        def flaky(args: BaseModel, ctx: ToolContext) -> str:
            attempts.append(1)
            if len(attempts) == 1:
                raise ConnectionError("billing API unavailable")
            return "refund queued"

        tools = ToolRegistry()
        tools.register(ToolSpec("issue_refund", "Refund.", SearchArgs, flaky, tier=Tier.WRITE))
        first = tools.call("issue_refund", {"query": "acct-17"}, "run-1")
        second = tools.call("issue_refund", {"query": "acct-17"}, "run-1")

        self.assertEqual(first.outcome, Outcome.ERROR)
        self.assertIn("ConnectionError", first.content)
        self.assertEqual(second.outcome, Outcome.OK)
        self.assertEqual(len(attempts), 2)

    def test_timeout_returns_quickly_and_warns_for_writes(self) -> None:
        def slow(args: BaseModel, ctx: ToolContext) -> str:
            time.sleep(0.5)
            return "late"

        tools = ToolRegistry()
        tools.register(ToolSpec("slow_write", "Slow.", SearchArgs, slow, tier=Tier.WRITE, timeout_s=0.05))
        started = time.monotonic()
        result = tools.call("slow_write", {"query": "xx"}, "run-1")

        self.assertLess(time.monotonic() - started, 0.4)
        self.assertEqual(result.outcome, Outcome.TIMEOUT)
        self.assertIn("may still complete", result.content)

    def test_large_result_is_truncated_with_a_hint(self) -> None:
        result = build_registry(FakeBilling()).call("search_docs", {"query": "refund"}, "r1")

        self.assertTrue(result.truncated)
        self.assertLess(len(result.content), 450)
        self.assertIn("Narrow the query", result.content)

    def test_duplicate_tool_names_are_refused(self) -> None:
        tools = build_registry(FakeBilling())
        with self.assertRaises(ValueError):
            tools.register(ToolSpec("search_docs", "Again.", SearchArgs, lambda a, c: ""))


if __name__ == "__main__":
    unittest.main()
