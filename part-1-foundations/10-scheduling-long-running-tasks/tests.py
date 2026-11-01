import unittest
from collections import Counter

from example import JobStore, Lease, LeaseLost, run_worker

DAY = 86_400.0


class Env:
    def __init__(self) -> None:
        self.now = 0.0
        self.store = JobStore(clock=lambda: self.now)
        self.done: Counter = Counter()

    def process(self, job_id: str, item: int) -> None:
        self.done[(job_id, item)] += 1


class TestClaiming(unittest.TestCase):
    def test_job_is_not_claimed_before_it_is_due(self) -> None:
        env = Env()
        env.store.schedule("j", total_items=3, run_at=100.0)
        self.assertIsNone(env.store.claim_due("w1", lease_s=30))
        env.now = 100.0
        self.assertIsNotNone(env.store.claim_due("w1", lease_s=30))

    def test_live_lease_excludes_other_workers(self) -> None:
        env = Env()
        env.store.schedule("j", total_items=3, run_at=0.0)
        self.assertIsNotNone(env.store.claim_due("w1", lease_s=30))
        env.now = 29.0
        self.assertIsNone(env.store.claim_due("w2", lease_s=30))

    def test_each_claim_gets_a_new_fencing_token(self) -> None:
        env = Env()
        env.store.schedule("j", total_items=3, run_at=0.0)
        first = env.store.claim_due("w1", lease_s=30)
        env.now = 31.0
        second = env.store.claim_due("w2", lease_s=30)
        self.assertEqual((first.token, second.token), (1, 2))


class TestCrashAndResume(unittest.TestCase):
    def test_crashed_job_resumes_from_the_last_checkpoint(self) -> None:
        env = Env()
        env.store.schedule("j", total_items=23, run_at=0.0)
        crashed = run_worker(env.store, "w1", env.process, batch_size=5, crash_after_items=12)
        self.assertEqual(crashed.outcome, "crashed")
        self.assertEqual(env.store.get("j").cursor, 10)  # two batches checkpointed

        env.now = 31.0  # lease expired
        resumed = run_worker(env.store, "w2", env.process, batch_size=5)
        self.assertEqual((resumed.outcome, resumed.started_at_cursor), ("completed", 10))
        self.assertEqual(len(env.done), 23)
        twice = sorted(item for (_, item), n in env.done.items() if n == 2)
        self.assertEqual(twice, [10, 11])  # only the batch that never reached a checkpoint
        self.assertEqual(env.store.get("j").status, "done")

    def test_zombie_worker_cannot_overwrite_progress(self) -> None:
        env = Env()
        env.store.schedule("j", total_items=10, run_at=0.0)
        old = env.store.claim_due("w1", lease_s=30)
        env.now = 31.0
        new = env.store.claim_due("w2", lease_s=30)
        env.store.checkpoint(new, 5)

        with self.assertRaises(LeaseLost):
            env.store.checkpoint(old, 2)  # w1 wakes up with a stale token
        with self.assertRaises(LeaseLost):
            env.store.complete(old)
        self.assertEqual(env.store.get("j").cursor, 5)

    def test_expired_lease_cannot_checkpoint_even_if_nobody_took_over(self) -> None:
        env = Env()
        env.store.schedule("j", total_items=10, run_at=0.0)
        lease = env.store.claim_due("w1", lease_s=30)
        env.now = 31.0
        with self.assertRaises(LeaseLost):
            env.store.checkpoint(lease, 5)

    def test_heartbeat_keeps_a_long_job_from_being_stolen(self) -> None:
        env = Env()
        env.store.schedule("j", total_items=10, run_at=0.0)
        lease = env.store.claim_due("w1", lease_s=30)
        for _ in range(3):
            env.now += 20
            env.store.heartbeat(lease, lease_s=30)
        self.assertGreater(env.now, 30)  # past the original expiry
        self.assertIsNone(env.store.claim_due("w2", lease_s=30))

    def test_worker_heartbeats_let_a_job_outlive_one_lease(self) -> None:
        env = Env()
        env.store.schedule("j", total_items=20, run_at=0.0)

        def steady(job_id: str, item: int) -> None:
            env.now += 4  # a 5-item batch takes 20s, inside a 30s lease; the job takes 80s

        report = run_worker(env.store, "w1", steady, batch_size=5, lease_s=30)
        self.assertEqual(report.outcome, "completed")
        self.assertEqual(env.now, 80.0)

    def test_worker_stops_when_its_lease_is_gone(self) -> None:
        env = Env()
        env.store.schedule("j", total_items=10, run_at=0.0)

        def slow(job_id: str, item: int) -> None:
            env.now += 10  # each item takes 10s; a 5-item batch outlives a 30s lease

        report = run_worker(env.store, "w1", slow, batch_size=5, lease_s=30)
        self.assertEqual(report.outcome, "lease_lost")
        self.assertEqual(env.store.get("j").cursor, 0)  # the late checkpoint was refused


class TestRecurring(unittest.TestCase):
    def test_next_run_is_scheduled_from_the_slot_not_from_completion(self) -> None:
        env = Env()
        env.store.schedule("nightly", total_items=1, run_at=0.0, every_s=DAY)
        env.now = 600.0  # started ten minutes late
        run_worker(env.store, "w1", env.process)
        self.assertEqual(env.store.get("nightly").next_run_at, DAY)

    def test_missed_runs_are_coalesced(self) -> None:
        env = Env()
        env.store.schedule("nightly", total_items=1, run_at=0.0, every_s=DAY)
        env.now = 3.5 * DAY  # scheduler down for three nights
        run_worker(env.store, "w1", env.process)
        self.assertEqual(run_worker(env.store, "w1", env.process).outcome, "idle")  # no burst of catch-up runs
        job = env.store.get("nightly")
        self.assertEqual(job.runs_completed, 1)
        self.assertEqual(job.next_run_at, 4 * DAY)


if __name__ == "__main__":
    unittest.main()
