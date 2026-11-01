import os
import sqlite3
import threading
import unittest
import uuid

from example import PostgresStateStore, SqliteStateStore, StaleWrite, update_with_retry


class StoreBehaviour:
    """Run against SQLite always, and against PostgreSQL when PAIE_POSTGRES_URL is set."""

    def make(self):
        raise NotImplementedError

    def test_round_trip_and_creation_event(self) -> None:
        store = self.make()
        store.create_run("r1", "acme", {"customer_id": "cus-1", "notes": []})
        run = store.get_run("r1")
        self.assertEqual((run.tenant, run.status, run.version), ("acme", "running", 1))
        self.assertEqual(run.state["customer_id"], "cus-1")
        self.assertEqual([t for t, _ in store.events("r1")], ["run.created"])

    def test_stale_write_is_refused_and_leaves_no_trace(self) -> None:
        store = self.make()
        store.create_run("r1", "acme", {"notes": []})
        first, second = store.get_run("r1"), store.get_run("r1")
        store.update_run("r1", first.version, {"notes": ["a"]}, "running")
        with self.assertRaises(StaleWrite):
            store.update_run("r1", second.version, {"notes": ["b"]}, "running")
        self.assertEqual(store.get_run("r1").state, {"notes": ["a"]})
        self.assertEqual([t for t, _ in store.events("r1")], ["run.created", "run.updated"])

    def test_retry_loop_reapplies_the_change_on_fresh_state(self) -> None:
        store = self.make()
        store.create_run("r1", "acme", {"notes": []})
        raced: list[int] = []

        def add_mine(state: dict) -> dict:
            if not raced:  # another worker commits between our read and our write
                raced.append(1)
                other = store.get_run("r1")
                store.update_run("r1", other.version, {"notes": other.state["notes"] + ["other"]}, "running")
            return {**state, "notes": state["notes"] + ["mine"]}

        final = update_with_retry(store, "r1", add_mine)
        self.assertEqual(final.state["notes"], ["other", "mine"])
        self.assertEqual(final.version, 3)

    def test_json_lookup_is_scoped_by_tenant(self) -> None:
        store = self.make()
        store.create_run("r1", "acme", {"customer_id": "cus-1"})
        store.create_run("r2", "globex", {"customer_id": "cus-1"})
        self.assertEqual(store.find_runs("acme", "customer_id", "cus-1"), ["r1"])

    def test_jobs_are_claimed_once_in_order(self) -> None:
        store = self.make()
        first, second = store.enqueue("r1"), store.enqueue("r2")
        self.assertEqual(store.claim_job("w1"), (first, "r1"))
        self.assertEqual(store.claim_job("w2"), (second, "r2"))
        self.assertIsNone(store.claim_job("w3"))


class TestSqliteStore(StoreBehaviour, unittest.TestCase):
    def make(self) -> SqliteStateStore:
        return SqliteStateStore()

    def test_a_step_is_written_once(self) -> None:
        store = self.make()
        store.create_run("r1", "acme", {})
        store.add_step("r1", 1, "tool_call", {"tool": "search"})
        with self.assertRaises(sqlite3.IntegrityError):
            store.add_step("r1", 1, "tool_call", {"tool": "search"})

    def test_concurrent_claimers_never_share_a_job(self) -> None:
        store, claimed, lock = self.make(), [], threading.Lock()
        for i in range(40):
            store.enqueue(f"r{i}")

        def worker(name: str) -> None:
            for _ in range(100):  # bounded, so a claim bug fails the test instead of hanging it
                job = store.claim_job(name)
                if job is None:
                    return
                with lock:
                    claimed.append(job[0])

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(claimed), list(range(1, 41)))  # every job once, none twice

    def test_concurrent_updates_lose_nothing_with_the_retry_loop(self) -> None:
        store = self.make()
        store.create_run("r1", "acme", {"notes": []})

        def add_note(i: int) -> None:
            update_with_retry(store, "r1", lambda s: {**s, "notes": s["notes"] + [i]}, attempts=50)

        threads = [threading.Thread(target=add_note, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(store.get_run("r1").state["notes"]), list(range(10)))


@unittest.skipUnless(os.environ.get("PAIE_POSTGRES_URL"), "set PAIE_POSTGRES_URL to run against PostgreSQL")
class TestPostgresStore(StoreBehaviour, unittest.TestCase):
    def make(self) -> PostgresStateStore:
        return PostgresStateStore(os.environ["PAIE_POSTGRES_URL"], schema=f"paie_test_{uuid.uuid4().hex[:8]}")


if __name__ == "__main__":
    unittest.main()
