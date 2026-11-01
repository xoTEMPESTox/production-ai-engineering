import unittest
import threading
from example import StateStore, AgentState, Message


class TestAgentState(unittest.TestCase):
    def test_initial_state(self) -> None:
        store = StateStore()
        state = store.get_state()
        self.assertEqual(state.version, 0)
        self.assertEqual(len(state.messages), 0)
        self.assertIsNone(state.current_task)
        self.assertEqual(len(state.execution_plan), 0)
        self.assertEqual(len(state.metadata), 0)

    def test_state_immutability_and_versioning(self) -> None:
        store = StateStore()
        initial_state = store.get_state()

        # Update and verify new object returned
        new_state = store.update(
            current_task="Ingest medical documents",
            execution_plan=["step1", "step2"],
            metadata={"source": "api"}
        )
        self.assertNotEqual(initial_state, new_state)
        self.assertEqual(new_state.version, 1)
        self.assertEqual(new_state.current_task, "Ingest medical documents")
        self.assertEqual(new_state.execution_plan, ["step1", "step2"])
        
        # Verify the initial state object did not mutate in place
        self.assertEqual(initial_state.version, 0)
        self.assertIsNone(initial_state.current_task)
        self.assertEqual(initial_state.execution_plan, [])

    def test_copy_on_write_leak_prevention(self) -> None:
        store = StateStore()
        plan = ["step1"]
        meta = {"user": "bob"}
        
        # Update state with variables
        store.update(execution_plan=plan, metadata=meta)
        
        # Mutate the original variables externally
        plan.append("step2")
        meta["user"] = "alice"
        
        # Retrieve state and check that it was protected against mutations
        state = store.get_state()
        self.assertEqual(state.execution_plan, ["step1"])
        self.assertEqual(state.metadata, {"user": "bob"})

    def test_returned_states_cannot_rewrite_history(self) -> None:
        store = StateStore()
        store.update(execution_plan=["step1"])

        # Mutate every object the store hands out
        store.get_state().execution_plan.append("INJECTED")
        store.update(current_task="next").execution_plan.append("INJECTED")
        store.get_history()[1].execution_plan.append("INJECTED")

        history = store.get_history()
        self.assertEqual(history[1].execution_plan, ["step1"])
        self.assertEqual(history[2].execution_plan, ["step1"])

    def test_rollback(self) -> None:
        store = StateStore()
        store.update(current_task="Task A")
        store.update(current_task="Task B")
        
        # Current version is 2
        self.assertEqual(store.get_state().version, 2)
        self.assertEqual(store.get_state().current_task, "Task B")
        
        # Roll back to version 1 (Task A)
        rolled_state = store.rollback(1)
        
        # Verify rollback transition version is incremented
        self.assertEqual(rolled_state.version, 3)
        self.assertEqual(rolled_state.current_task, "Task A")
        
        # Verify history has 4 entries (V0, V1, V2, V3)
        history = store.get_history()
        self.assertEqual(len(history), 4)
        self.assertEqual(history[1].current_task, "Task A")
        self.assertEqual(history[2].current_task, "Task B")
        self.assertEqual(history[3].current_task, "Task A")

    def test_thread_safety_concurrency(self) -> None:
        store = StateStore()
        num_threads = 50
        updates_per_thread = 10
        
        def worker() -> None:
            for _ in range(updates_per_thread):
                store.update(metadata={"last_thread": threading.get_ident()})

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
            
        final_state = store.get_state()
        # Initial is V0. Total updates = 50 * 10 = 500. Final should be V500
        self.assertEqual(final_state.version, num_threads * updates_per_thread)


if __name__ == "__main__":
    unittest.main()
