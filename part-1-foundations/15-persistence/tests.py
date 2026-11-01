import json
import os
import tempfile
import unittest

from example import (
    SCHEMA_VERSION, CheckpointConflict, CheckpointStore, Crash, CountingLLM, Message, RunState, SchemaTooNew,
    migrate, run_agent,
)


def insert_raw(store: CheckpointStore, run_id: str, step: int, version: int, data: dict) -> None:
    with store.db:
        store.db.execute("INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?)", (run_id, step, version, json.dumps(data), 0.0))


class TestCheckpointing(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)  # registered first, so it runs after every store is closed
        self.path = os.path.join(folder.name, "runs.db")

    def open(self) -> CheckpointStore:
        store = CheckpointStore(self.path)
        self.addCleanup(store.close)  # closing twice is harmless
        return store

    def test_state_survives_reopening_the_database(self) -> None:
        store = self.open()
        store.save(RunState(run_id="r", task="t", step=1, messages=[Message(role="user", content="hi")]))
        store.close()

        reopened = self.open()
        self.assertEqual(reopened.load("r").messages[0].content, "hi")

    def test_resume_after_crash_does_not_repeat_model_calls(self) -> None:
        llm = CountingLLM()
        first = self.open()
        with self.assertRaises(Crash):
            run_agent(llm, first, "r", "task", total_steps=5, crash_after_step=3)
        first.close()

        second = self.open()
        final = run_agent(llm, second, "r", "task", total_steps=5)
        self.assertEqual((final.step, llm.calls), (5, 5))
        self.assertEqual([m.content for m in final.messages][-1], "result of step 5")
        self.assertEqual(final.tokens_used, 600)

    def test_any_earlier_step_can_be_reloaded(self) -> None:
        store = self.open()
        run_agent(CountingLLM(), store, "r", "task", total_steps=4)
        self.assertEqual(store.steps("r"), [1, 2, 3, 4])
        self.assertEqual(len(store.load("r", step=2).messages), 2)

    def test_a_step_can_only_be_written_once(self) -> None:
        store = self.open()
        store.save(RunState(run_id="r", task="t", step=1))
        with self.assertRaises(CheckpointConflict):
            store.save(RunState(run_id="r", task="other writer", step=1))
        self.assertEqual(store.load("r").task, "t")

    def test_oversized_state_is_refused(self) -> None:
        store = self.open()
        huge = RunState(run_id="r", task="t", messages=[Message(role="tool", content="x" * 100_000)])
        with self.assertRaises(ValueError):
            store.save(huge)
        self.assertEqual(store.steps("r"), [])

    def test_unknown_run_loads_as_none(self) -> None:
        store = self.open()
        self.assertIsNone(store.load("nope"))


class TestMigrations(unittest.TestCase):
    V1 = {"run_id": "r", "task": "t", "step": 2, "history": ["hi", "hello", "thanks"], "todo": ["reply"]}

    def test_v1_checkpoint_loads_as_current_schema(self) -> None:
        store = CheckpointStore()
        insert_raw(store, "r", 2, 1, self.V1)
        state = store.load("r")
        self.assertEqual([(m.role, m.content) for m in state.messages],
                         [("user", "hi"), ("assistant", "hello"), ("user", "thanks")])
        self.assertEqual((state.plan, state.tokens_used), (["reply"], 0))

    def test_only_the_missing_steps_are_applied(self) -> None:
        v2 = {"run_id": "r", "task": "t", "messages": [], "todo": ["a"], "tokens_used": 7}
        migrated = migrate(v2, 2)
        self.assertEqual((migrated["plan"], migrated["tokens_used"]), (["a"], 7))
        self.assertNotIn("todo", migrated)

    def test_migration_does_not_mutate_its_input(self) -> None:
        original = json.loads(json.dumps(self.V1))
        migrate(original, 1)
        self.assertEqual(original, self.V1)

    def test_checkpoint_from_newer_code_is_refused(self) -> None:
        store = CheckpointStore()
        insert_raw(store, "r", 1, SCHEMA_VERSION + 1, {"run_id": "r", "task": "t", "new_field": 1})
        with self.assertRaises(SchemaTooNew):
            store.load("r")

    def test_current_schema_rejects_unknown_fields(self) -> None:
        store = CheckpointStore()
        insert_raw(store, "r", 1, SCHEMA_VERSION, {"run_id": "r", "task": "t", "surprise": True})
        with self.assertRaises(ValueError):
            store.load("r")


if __name__ == "__main__":
    unittest.main()
