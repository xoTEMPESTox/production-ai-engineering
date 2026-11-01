import unittest
import zlib

from example import (
    CANDIDATE, STABLE, ArmStats, CanaryController, Outcome, Router, bucket, fake_call, p95_with_cold_starts, serve,
)


def stats(requests: int, errors: int = 0, schema_failures: int = 0, latency_ms: float = 800) -> ArmStats:
    arm = ArmStats()
    for i in range(requests):
        arm.record(Outcome(ok=i >= errors, schema_ok=not (errors <= i < errors + schema_failures),
                           latency_ms=latency_ms))
    return arm


class TestRouting(unittest.TestCase):
    def test_assignment_is_sticky_per_user(self) -> None:
        router = Router(STABLE, CANDIDATE, candidate_percent=10)
        for i in range(200):
            user = f"user-{i}"
            self.assertEqual(router.assign(user), router.assign(user))

    def test_assignment_is_the_same_on_every_replica(self) -> None:
        # Derived from crc32, not Python's hash(), which differs between processes:
        # otherwise a user would flip between releases depending on which replica answered.
        router = Router(STABLE, CANDIDATE, candidate_percent=10)
        for i in range(500):
            user = f"user-{i}"
            expected = CANDIDATE if zlib.crc32(user.encode()) % 100 < 10 else STABLE
            self.assertEqual(router.assign(user), expected)

    def test_share_of_users_matches_the_percentage(self) -> None:
        router = Router(STABLE, CANDIDATE, candidate_percent=10)
        on_candidate = sum(router.assign(f"user-{i}") == CANDIDATE for i in range(10_000))
        self.assertTrue(800 < on_candidate < 1200)

    def test_zero_and_hundred_percent(self) -> None:
        self.assertEqual(Router(STABLE, CANDIDATE, 0).assign("u"), STABLE)
        self.assertEqual(Router(STABLE, CANDIDATE, 100).assign("u"), CANDIDATE)
        self.assertTrue(0 <= bucket("u") < 100)

    def test_shadow_answers_are_never_shown(self) -> None:
        router, arms, log = Router(STABLE, CANDIDATE, 0), {}, []
        route = lambda release, user: Outcome(True, True, 100, f"{release.name} answer")
        answers = [serve(router, route, f"u{i}", arms, shadow=CANDIDATE, shadow_log=log) for i in range(50)]
        self.assertTrue(all(a.startswith(STABLE.name) for a in answers))
        self.assertEqual(arms[f"{CANDIDATE.name} (shadow)"].requests, 50)


class TestGates(unittest.TestCase):
    def test_failed_offline_eval_blocks_the_release(self) -> None:
        controller = CanaryController(Router(STABLE, CANDIDATE))
        self.assertFalse(controller.start(offline_eval_passed=False))
        self.assertEqual(controller.router.candidate_percent, 0)

    def test_bad_shadow_results_block_the_canary(self) -> None:
        controller = CanaryController(Router(STABLE, CANDIDATE))
        started = controller.start(True, stable_stats=stats(400), shadow_stats=stats(400, schema_failures=40))
        self.assertFalse(started)
        self.assertIn("blocked in shadow", controller.log[-1])

    def test_healthy_candidate_is_promoted_stage_by_stage(self) -> None:
        controller = CanaryController(Router(STABLE, CANDIDATE))
        controller.start(True)
        seen = [controller.router.candidate_percent]
        while controller.status == "canary":
            controller.step(stats(500), stats(500))
            seen.append(controller.router.candidate_percent)
        self.assertEqual(seen, [1, 10, 50, 100])
        self.assertEqual(controller.status, "promoted")

    def test_too_little_data_holds(self) -> None:
        controller = CanaryController(Router(STABLE, CANDIDATE))
        controller.start(True)
        decision = controller.step(stats(5_000), stats(40, schema_failures=40))  # terrible, but 40 requests
        self.assertEqual(decision.action, "hold")
        self.assertEqual(controller.router.candidate_percent, 1)

    def test_guardrail_breach_rolls_back_instantly(self) -> None:
        for candidate, reason in [(stats(500, errors=40), "error rate"),
                                  (stats(500, schema_failures=30), "schema failures"),
                                  (stats(500, latency_ms=1500), "p95")]:
            router = Router(STABLE, CANDIDATE)
            controller = CanaryController(router)
            controller.start(True)
            decision = controller.step(stats(500), candidate)
            self.assertEqual((decision.action, router.candidate_percent, controller.status),
                             ("rollback", 0, "rolled_back"))
            self.assertIn(reason, decision.reasons[0])
            self.assertEqual(router.assign("anyone"), STABLE)

    def test_small_error_increase_within_tolerance_passes(self) -> None:
        controller = CanaryController(Router(STABLE, CANDIDATE))
        controller.start(True)
        self.assertEqual(controller.step(stats(1000, errors=10), stats(1000, errors=25)).action, "promote")


class TestColdStarts(unittest.TestCase):
    def test_warm_up_keeps_load_time_out_of_p95(self) -> None:
        self.assertEqual(p95_with_cold_starts(100, 10, 800, 6000, warmed=False), 6800)
        self.assertEqual(p95_with_cold_starts(100, 10, 800, 6000, warmed=True), 800)

    def test_fake_behaviour_is_independent_of_the_canary_bucket(self) -> None:
        call = fake_call(schema_fail_every=12)
        low = [call(CANDIDATE, f"user-{i}") for i in range(20_000) if bucket(f"user-{i}") < 1]
        rate = sum(not o.schema_ok for o in low) / len(low)
        self.assertTrue(0.04 < rate < 0.13)  # about 1 in 12, even inside the 1% bucket


if __name__ == "__main__":
    unittest.main()
