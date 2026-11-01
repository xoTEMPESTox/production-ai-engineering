import os
import threading
import unittest
import uuid

from example import CheckpointStore, FakeRedis, Keys, RedisAdapter, RunLock


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class LockBehaviour:
    """Shared tests, run against the fake always and against real Redis when configured."""

    def make(self):  # -> (store, keys, advance_clock or None)
        raise NotImplementedError

    def test_only_one_holder_at_a_time(self) -> None:
        store, keys, _ = self.make()
        locks = RunLock(store, keys)
        self.assertIsNotNone(locks.acquire("run-1", 30_000))
        self.assertIsNone(locks.acquire("run-1", 30_000))
        self.assertIsNotNone(locks.acquire("run-2", 30_000))  # other runs are independent

    def test_release_with_a_stale_token_does_nothing(self) -> None:
        store, keys, _ = self.make()
        locks = RunLock(store, keys)
        token = locks.acquire("run-1", 30_000)
        self.assertFalse(locks.release("run-1", "not-my-token"))
        self.assertEqual(store.get(keys.lock("run-1")), token)
        self.assertTrue(locks.release("run-1", token))
        self.assertIsNotNone(locks.acquire("run-1", 30_000))


class TestFakeRedisLocks(LockBehaviour, unittest.TestCase):
    def make(self):
        self.clock = Clock()
        return FakeRedis(clock=self.clock), Keys(env="test"), self.clock

    def test_expired_lock_can_be_taken_and_the_old_owner_cannot_touch_it(self) -> None:
        store, keys, clock = self.make()
        locks = RunLock(store, keys)
        old = locks.acquire("run-1", 30_000)
        clock.now = 31
        new = locks.acquire("run-1", 30_000)
        self.assertIsNotNone(new)
        self.assertFalse(locks.release("run-1", old))
        self.assertFalse(locks.refresh("run-1", old, 30_000))
        self.assertEqual(store.get(keys.lock("run-1")), new)

    def test_refresh_extends_only_for_the_owner(self) -> None:
        store, keys, clock = self.make()
        locks = RunLock(store, keys)
        token = locks.acquire("run-1", 30_000)
        clock.now = 25
        self.assertTrue(locks.refresh("run-1", token, 30_000))
        clock.now = 50  # past the original expiry, inside the refreshed one
        self.assertIsNone(locks.acquire("run-1", 30_000))

    def test_concurrent_acquires_have_one_winner(self) -> None:
        store, keys, _ = self.make()
        locks, winners = RunLock(store, keys), []

        def contend() -> None:
            if locks.acquire("run-1", 30_000):
                winners.append(1)

        threads = [threading.Thread(target=contend) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(winners), 1)


class TestCheckpointsAndKeys(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.store, self.keys = FakeRedis(clock=self.clock), Keys(env="test")
        self.checkpoints = CheckpointStore(self.store, self.keys, ttl_ms=60_000)

    def test_latest_and_specific_steps(self) -> None:
        for step in (1, 2, 3):
            self.checkpoints.save("run-1", step, {"n": step})
        self.assertEqual(self.checkpoints.load("run-1")["step"], 3)
        self.assertEqual(self.checkpoints.load("run-1", step=2)["state"], {"n": 2})
        self.assertIsNone(self.checkpoints.load("run-9"))

    def test_checkpoints_expire(self) -> None:
        self.checkpoints.save("run-1", 1, {"n": 1})
        self.assertGreater(self.store.pttl(self.keys.latest("run-1")), 0)
        self.clock.now = 61
        self.assertIsNone(self.checkpoints.load("run-1"))
        self.assertEqual(self.store.pttl(self.keys.latest("run-1")), -2)

    def test_keys_are_namespaced_versioned_and_hash_tagged(self) -> None:
        self.assertEqual(Keys(env="prod").lock("run-7"), "paie:prod:v1:run:{run-7}:lock")
        self.assertTrue(self.keys.latest("run-7").startswith("paie:test:v1:run:{run-7}:"))

    def test_keys_reject_unsafe_ids(self) -> None:
        for bad in ("run 7", "run-7}:lock", "*", "", "RUN-7"):
            with self.assertRaises(ValueError, msg=bad):
                self.keys.lock(bad)


@unittest.skipUnless(os.environ.get("PAIE_REDIS_URL"), "set PAIE_REDIS_URL to run against a real Redis")
class TestRealRedisLocks(LockBehaviour, unittest.TestCase):
    def make(self):
        return RedisAdapter(os.environ["PAIE_REDIS_URL"]), Keys(env=f"test-{uuid.uuid4().hex[:8]}"), None


if __name__ == "__main__":
    unittest.main()
