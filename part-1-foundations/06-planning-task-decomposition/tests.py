import json
import unittest

from example import (
    Plan, PlanError, PlanExecutor, PlanSpec, ScriptedLLM, Status, Step, StepSpec, validate_plan,
)


def step(sid: str, tool: str, args: dict | None = None, deps: list[str] | None = None) -> dict:
    return {"id": sid, "description": f"do {sid}", "tool": tool, "args": args or {}, "depends_on": deps or []}


def plan_json(*steps: dict) -> str:
    return json.dumps({"steps": list(steps)})


class Recorder:
    """Tools that record their calls; 'broken' always fails."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def tools(self) -> dict:
        def make(name: str):
            def tool(**kwargs) -> str:
                self.calls.append((name, kwargs))
                if name == "broken":
                    raise LookupError("no email on file")
                return f"{name}-result"
            return tool
        return {name: make(name) for name in ["lookup", "refund", "broken", "callback"]}


class TestExecution(unittest.TestCase):
    def test_steps_run_in_order_and_pass_results_forward(self) -> None:
        rec = Recorder()
        llm = ScriptedLLM([plan_json(step("s1", "lookup"), step("s2", "refund", {"charge": "{s1}"}, ["s1"]))])
        report = PlanExecutor(llm, rec.tools()).run("goal")

        self.assertEqual(report.status, "done")
        self.assertEqual(rec.calls, [("lookup", {}), ("refund", {"charge": "lookup-result"})])
        self.assertEqual(report.plan.progress()["done"], 2)

    def test_replan_keeps_finished_work_and_drops_the_rest(self) -> None:
        rec = Recorder()
        llm = ScriptedLLM([
            plan_json(step("s1", "refund"), step("s2", "broken", deps=["s1"]), step("s3", "lookup", deps=["s2"])),
            plan_json(step("s4", "callback", {"note": "{s1}"}, ["s1"])),
        ])
        report = PlanExecutor(llm, rec.tools()).run("goal")

        self.assertEqual(report.status, "done")
        self.assertEqual(report.replans, 1)
        self.assertEqual([name for name, _ in rec.calls], ["refund", "broken", "callback"])  # refund ran once
        statuses = {s.spec.id: s.status for s in report.plan.steps}
        self.assertEqual(statuses, {"s1": Status.DONE, "s2": Status.FAILED,
                                    "s3": Status.SUPERSEDED, "s4": Status.DONE})
        self.assertEqual(report.plan.version, 2)

    def test_replan_prompt_shows_the_checklist_and_the_error(self) -> None:
        llm = ScriptedLLM([plan_json(step("s1", "refund"), step("s2", "broken", deps=["s1"])),
                           plan_json(step("s3", "callback"))])
        PlanExecutor(llm, Recorder().tools()).run("goal")

        replan_prompt = llm.prompts[1]
        self.assertIn("[x] s1 refund", replan_prompt)
        self.assertIn("[!] s2 broken", replan_prompt)
        self.assertIn("LookupError: no email on file", replan_prompt)

    def test_replan_budget_is_enforced(self) -> None:
        failing = [plan_json(step(f"s{i}", "broken")) for i in range(1, 10)]
        llm = ScriptedLLM(failing)
        report = PlanExecutor(llm, Recorder().tools(), max_replans=2).run("goal")

        self.assertEqual(report.status, "failed")
        self.assertEqual(report.replans, 2)
        self.assertEqual(len(llm.prompts), 3)  # first plan + two re-plans, no more
        self.assertIn("re-plan budget spent", report.reason)

    def test_invalid_plan_fails_the_run_without_running_anything(self) -> None:
        rec = Recorder()
        report = PlanExecutor(ScriptedLLM(["Step 1: look things up"]), rec.tools()).run("goal")
        self.assertEqual(report.status, "failed")
        self.assertIn("invalid plan", report.reason)
        self.assertEqual(rec.calls, [])


class TestPlanValidation(unittest.TestCase):
    def check(self, *steps: dict, plan: Plan | None = None) -> str:
        with self.assertRaises(PlanError) as ctx:
            validate_plan(PlanSpec.model_validate({"steps": list(steps)}), Recorder().tools(), plan or Plan("g"))
        return str(ctx.exception)

    def test_unknown_tool(self) -> None:
        self.assertIn("unknown tool 'delete_all'", self.check(step("s1", "delete_all")))

    def test_dependency_must_come_first(self) -> None:
        self.assertIn("depends on 's2'", self.check(step("s1", "lookup", deps=["s2"]), step("s2", "lookup")))

    def test_placeholder_needs_a_declared_dependency(self) -> None:
        message = self.check(step("s1", "lookup"), step("s2", "refund", {"charge": "{s1}"}))
        self.assertIn("uses ['s1'] without depending on it", message)

    def test_replan_cannot_reuse_a_finished_steps_id(self) -> None:
        finished = Step(StepSpec(**step("s1", "lookup")), version=1, status=Status.DONE, result="x")
        message = self.check(step("s1", "refund"), plan=Plan("g", steps=[finished]))
        self.assertIn("s1: id already used", message)

    def test_plan_size_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            PlanSpec.model_validate({"steps": [step(f"s{i}", "lookup") for i in range(1, 10)]})


if __name__ == "__main__":
    unittest.main()
