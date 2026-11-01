import json
import unittest

from example import (
    AgentLoop, Budget, EventKind, ModelTurn, ScriptedLLM, StopReason, ToolCall,
)


class FakeClock:
    """Advances by a fixed amount every time it is read."""

    def __init__(self, tick: float) -> None:
        self.now = 0.0
        self.tick = tick

    def __call__(self) -> float:
        self.now += self.tick
        return self.now


def lookup(key: str) -> str:
    return f"value-of-{key}"


def explode() -> str:
    raise ConnectionError("upstream timed out")


TOOLS = {"lookup": lookup, "explode": explode}


class TestAgentLoop(unittest.TestCase):
    def test_observation_reaches_model_and_answer_is_returned(self) -> None:
        llm = ScriptedLLM([
            ModelTurn("look it up", ToolCall("lookup", {"key": "a"})),
            ModelTurn("done", final_answer="a is value-of-a"),
        ])
        result = AgentLoop(llm, TOOLS).run("what is a?")

        self.assertEqual(result.stop_reason, StopReason.FINAL_ANSWER)
        self.assertEqual(result.answer, "a is value-of-a")
        self.assertEqual(result.steps, 2)
        # The second model call saw the tool's output as an observation.
        self.assertEqual(llm.seen[1][-1].role, "tool")
        self.assertEqual(llm.seen[1][-1].content, "value-of-a")

    def test_tool_exception_becomes_observation_and_run_continues(self) -> None:
        llm = ScriptedLLM([
            ModelTurn("try it", ToolCall("explode")),
            ModelTurn("fall back", ToolCall("lookup", {"key": "b"})),
            ModelTurn("done", final_answer="b"),
        ])
        result = AgentLoop(llm, TOOLS).run("task")

        self.assertEqual(result.stop_reason, StopReason.FINAL_ANSWER)
        self.assertIn("ConnectionError: upstream timed out", llm.seen[1][-1].content)
        kinds = [e.kind for e in result.events]
        self.assertIn(EventKind.TOOL_ERROR, kinds)
        self.assertIn(EventKind.TOOL_RESULT, kinds)

    def test_unknown_tool_and_bad_arguments_are_observations(self) -> None:
        llm = ScriptedLLM([
            ModelTurn("guess", ToolCall("delete_everything")),
            ModelTurn("wrong args", ToolCall("lookup", {"id": "a"})),
            ModelTurn("done", final_answer="gave up politely"),
        ])
        result = AgentLoop(llm, TOOLS).run("task")

        self.assertEqual(result.stop_reason, StopReason.FINAL_ANSWER)
        self.assertIn("unknown tool 'delete_everything'", llm.seen[1][-1].content)
        self.assertIn("Available tools: explode, lookup", llm.seen[1][-1].content)
        self.assertIn("TypeError", llm.seen[2][-1].content)

    def test_identical_repeated_call_stops_the_run(self) -> None:
        calls: list[str] = []

        def counting_lookup(key: str) -> str:
            calls.append(key)
            return "nothing new"

        same = ToolCall("lookup", {"key": "x"})
        llm = ScriptedLLM([ModelTurn("again", same) for _ in range(6)])
        result = AgentLoop(llm, {"lookup": counting_lookup}).run("task")

        self.assertEqual(result.stop_reason, StopReason.LOOP_DETECTED)
        self.assertEqual(result.steps, 3)
        self.assertEqual(len(calls), 2)  # the third identical call never executed

    def test_argument_order_does_not_hide_a_repeat(self) -> None:
        self.assertEqual(
            ToolCall("t", {"a": 1, "b": 2}).fingerprint(),
            ToolCall("t", {"b": 2, "a": 1}).fingerprint(),
        )

    def test_step_budget_bounds_a_run_that_never_finishes(self) -> None:
        llm = ScriptedLLM([ModelTurn("more", ToolCall("lookup", {"key": str(i)})) for i in range(50)])
        result = AgentLoop(llm, TOOLS, Budget(max_steps=5)).run("task")

        self.assertEqual(result.stop_reason, StopReason.MAX_STEPS)
        self.assertEqual(len(llm.seen), 5)

    def test_token_budget_stops_before_more_tools_run(self) -> None:
        llm = ScriptedLLM([
            ModelTurn("big", ToolCall("lookup", {"key": "1"}), input_tokens=600, output_tokens=100),
            ModelTurn("bigger", ToolCall("lookup", {"key": "2"}), input_tokens=900, output_tokens=100),
            ModelTurn("never reached", final_answer="x"),
        ])
        result = AgentLoop(llm, TOOLS, Budget(max_tokens=1_000)).run("task")

        self.assertEqual(result.stop_reason, StopReason.TOKEN_BUDGET)
        self.assertEqual(result.tokens_used, 1_700)
        self.assertEqual(len(llm.seen), 2)

    def test_time_budget_uses_the_injected_clock(self) -> None:
        llm = ScriptedLLM([ModelTurn("slow", ToolCall("lookup", {"key": str(i)})) for i in range(50)])
        result = AgentLoop(llm, TOOLS, Budget(max_seconds=10), clock=FakeClock(tick=2.0)).run("task")

        self.assertEqual(result.stop_reason, StopReason.TIME_BUDGET)
        self.assertLess(len(llm.seen), 50)

    def test_empty_turn_gets_a_correction_not_a_crash(self) -> None:
        llm = ScriptedLLM([ModelTurn("hmm"), ModelTurn("ok", final_answer="done")])
        result = AgentLoop(llm, TOOLS).run("task")

        self.assertEqual(result.stop_reason, StopReason.FINAL_ANSWER)
        self.assertIn(EventKind.CORRECTION, [e.kind for e in result.events])
        self.assertIn("no tool call and no final answer", llm.seen[1][-1].content)

    def test_model_failure_ends_the_run_with_a_reason(self) -> None:
        result = AgentLoop(ScriptedLLM([]), TOOLS).run("task")

        self.assertEqual(result.stop_reason, StopReason.MODEL_ERROR)
        self.assertIsNone(result.answer)
        self.assertEqual(result.events[0].kind, EventKind.MODEL_ERROR)

    def test_event_log_is_serialisable_and_ends_with_stop(self) -> None:
        llm = ScriptedLLM([ModelTurn("x", ToolCall("lookup", {"key": "k"})), ModelTurn("y", final_answer="k")])
        result = AgentLoop(llm, TOOLS).run("task")

        rows = [json.loads(e.to_json()) for e in result.events]
        self.assertEqual(rows[-1]["kind"], "stopped")
        self.assertEqual(rows[-1]["detail"], "final_answer")
        self.assertEqual([r["step"] for r in rows], sorted(r["step"] for r in rows))


if __name__ == "__main__":
    unittest.main()
