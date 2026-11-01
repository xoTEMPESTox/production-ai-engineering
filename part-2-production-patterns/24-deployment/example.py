"""
Lesson 24: Deployment.

Shipping a new prompt or model is a release, not an edit. A candidate must
pass the offline eval gate first, then runs in shadow on copies of real
requests (users never see its output), then takes a small, sticky share of
users in a canary. A controller compares the canary with the stable
release on guardrail metrics (errors, schema failures, p95 latency) and
promotes it stage by stage, holds until there's enough data, or rolls back
at once by moving traffic to the stable release that's still running.
Cold starts are handled by warming new instances before they take traffic.
"""
from __future__ import annotations

import hashlib
import math
import zlib
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional


@dataclass(frozen=True)
class Release:
    name: str
    prompt_version: str
    model: str


@dataclass(frozen=True)
class Outcome:
    ok: bool            # the call succeeded
    schema_ok: bool     # the output passed validation (Lesson 03)
    latency_ms: float
    text: str = ""


def bucket(user_id: str) -> int:
    """0-99, stable per user: the same user stays on the same release for the whole canary."""
    return zlib.crc32(user_id.encode()) % 100


@dataclass
class Router:
    stable: Release
    candidate: Optional[Release] = None
    candidate_percent: int = 0

    def assign(self, user_id: str) -> Release:
        if self.candidate is not None and bucket(user_id) < self.candidate_percent:
            return self.candidate
        return self.stable


@dataclass
class ArmStats:
    requests: int = 0
    errors: int = 0
    schema_failures: int = 0
    latencies: list[float] = field(default_factory=list)

    def record(self, outcome: Outcome) -> None:
        self.requests += 1
        self.errors += not outcome.ok
        self.schema_failures += outcome.ok and not outcome.schema_ok
        self.latencies.append(outcome.latency_ms)

    def rate(self, count: int) -> float:
        return count / self.requests if self.requests else 0.0

    @property
    def p95(self) -> float:
        ordered = sorted(self.latencies)
        return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else 0.0


@dataclass(frozen=True)
class Decision:
    action: Literal["promote", "hold", "rollback"]
    reasons: tuple[str, ...]


@dataclass
class CanaryController:
    router: Router
    stages: tuple[int, ...] = (1, 10, 50)  # passing the last stage promotes to 100%
    min_requests: int = 200            # per arm, before any verdict
    max_error_increase: float = 0.02   # absolute, vs stable
    max_schema_failure_rate: float = 0.03
    max_p95_ratio: float = 1.3
    status: Literal["shadow", "canary", "promoted", "rolled_back"] = "shadow"
    log: list[str] = field(default_factory=list)

    def start(self, offline_eval_passed: bool, stable_stats: Optional[ArmStats] = None,
              shadow_stats: Optional[ArmStats] = None) -> bool:
        """Gate 1: offline evals. Gate 2 (if shadow ran): the same guardrails on shadow traffic."""
        if not offline_eval_passed:
            self.log.append("blocked: the candidate failed the offline eval gate (Lesson 27)")
            return False
        if stable_stats is not None and shadow_stats is not None:
            shadow = self.evaluate(stable_stats, shadow_stats)
            if shadow.action != "promote":
                self.log.append(f"blocked in shadow: {'; '.join(shadow.reasons)}")
                return False
        self.status, self.router.candidate_percent = "canary", self.stages[0]
        self.log.append(f"canary started at {self.stages[0]}%")
        return True

    def evaluate(self, stable: ArmStats, candidate: ArmStats) -> Decision:
        if min(stable.requests, candidate.requests) < self.min_requests:
            return Decision("hold", (f"need {self.min_requests} requests per arm; "
                                     f"have {stable.requests} and {candidate.requests}",))
        reasons = []
        if candidate.rate(candidate.errors) - stable.rate(stable.errors) > self.max_error_increase:
            reasons.append(f"error rate {candidate.rate(candidate.errors):.1%} vs stable {stable.rate(stable.errors):.1%}")
        if candidate.rate(candidate.schema_failures) > self.max_schema_failure_rate:
            reasons.append(f"schema failures {candidate.rate(candidate.schema_failures):.1%}")
        if stable.p95 and candidate.p95 / stable.p95 > self.max_p95_ratio:
            reasons.append(f"p95 {candidate.p95:.0f} ms vs stable {stable.p95:.0f} ms")
        return Decision("rollback", tuple(reasons)) if reasons else Decision("promote", ("all guardrails healthy",))

    def step(self, stable: ArmStats, candidate: ArmStats) -> Decision:
        decision = self.evaluate(stable, candidate)
        percent = self.router.candidate_percent
        if decision.action == "rollback":
            self.router.candidate_percent, self.status = 0, "rolled_back"  # instant: stable never left
            self.log.append(f"rolled back at {percent}%: {'; '.join(decision.reasons)}")
        elif decision.action == "promote":
            later = [s for s in self.stages if s > percent]
            if later:
                self.router.candidate_percent = later[0]
                self.log.append(f"promoted {percent}% -> {later[0]}%")
            else:
                self.router.candidate_percent, self.status = 100, "promoted"
                self.log.append(f"promoted {percent}% -> 100%: the candidate is the new stable release")
        return decision


Call = Callable[[Release, str], Outcome]


def serve(router: Router, call: Call, user_id: str, stats: dict[str, ArmStats],
          shadow: Optional[Release] = None, shadow_log: Optional[list] = None) -> str:
    """Serve one request. In shadow mode the candidate also runs, but only for comparison."""
    release = router.assign(user_id)
    outcome = call(release, user_id)
    stats.setdefault(release.name, ArmStats()).record(outcome)
    if shadow is not None and shadow_log is not None:
        shadow_outcome = call(shadow, user_id)  # in production: async, off the request path
        stats.setdefault(f"{shadow.name} (shadow)", ArmStats()).record(shadow_outcome)
        shadow_log.append((outcome.text, shadow_outcome.text))
    return outcome.text  # always the routed release's answer


def p95_with_cold_starts(requests: int, new_instances: int, base_ms: float, load_ms: float, warmed: bool) -> float:
    """Each new instance pays load_ms (model/index load, imports) on its first request, unless warmed first."""
    latencies = [base_ms] * requests
    if not warmed:
        for i in range(min(new_instances, requests)):
            latencies[i] += load_ms
    stats = ArmStats(latencies=latencies, requests=requests)
    return stats.p95


def fake_call(error_every: int = 0, schema_fail_every: int = 0, base_ms: float = 800) -> Call:
    """Deterministic fake model behaviour, varied per user. Uses a different hash from
    bucket(): CRC32 is linear, so reusing it would correlate behaviour with the canary bucket."""
    def call(release: Release, user_id: str) -> Outcome:
        h = int(hashlib.sha256(f"{release.name}:{user_id}".encode()).hexdigest()[:8], 16)
        ok = not (error_every and h % error_every == 0)
        schema_ok = not (schema_fail_every and h % schema_fail_every == 1)
        return Outcome(ok, schema_ok, base_ms + h % 400, f"{release.name} answer")
    return call


STABLE = Release("support-v7", "grounded_answer@v2", "large-model")
CANDIDATE = Release("support-v8", "grounded_answer@v3", "small-model")

if __name__ == "__main__":
    def release_candidate(title: str, candidate_call: Call, use_shadow: bool) -> None:
        router = Router(STABLE, CANDIDATE)
        controller = CanaryController(router)
        calls = {STABLE.name: fake_call(error_every=100), CANDIDATE.name: candidate_call}
        route = lambda release, user: calls[release.name](release, user)
        print(f"\n== {title}")
        stable_stats, shadow_stats = None, None
        if use_shadow:
            stats: dict[str, ArmStats] = {}
            shadow_log: list = []
            for i in range(400):  # copies of real requests; users only ever see stable answers
                serve(router, route, f"user-{i}", stats, shadow=CANDIDATE, shadow_log=shadow_log)
            stable_stats, shadow_stats = stats[STABLE.name], stats[f"{CANDIDATE.name} (shadow)"]
            shown = sum(answer.startswith(CANDIDATE.name) for answer, _ in shadow_log)
            print(f"   shadow: {shadow_stats.requests} requests, candidate answers shown to users: {shown}")
        if controller.start(offline_eval_passed=True, stable_stats=stable_stats, shadow_stats=shadow_stats):
            user, stats = 0, {}
            for _ in range(100):  # each round: 2,000 more requests, then a verdict
                if controller.status != "canary":
                    break
                for _ in range(2000):
                    serve(router, route, f"user-{user}", stats)
                    user += 1
                stage = router.candidate_percent
                decision = controller.step(stats.get(STABLE.name, ArmStats()), stats.get(CANDIDATE.name, ArmStats()))
                if decision.action != "hold" and router.candidate_percent != stage:
                    stats = {}  # a new stage starts with fresh numbers
        for line in controller.log:
            print("  ", line)

    release_candidate("good candidate", fake_call(error_every=100, base_ms=700), use_shadow=True)
    release_candidate("candidate with schema failures", fake_call(error_every=100, schema_fail_every=12), use_shadow=True)
    release_candidate("same candidate, shadow skipped", fake_call(error_every=100, schema_fail_every=12), use_shadow=False)

    print(f"\ncold starts: p95 {p95_with_cold_starts(100, 10, 800, 6000, warmed=False):.0f} ms without warm-up, "
          f"{p95_with_cold_starts(100, 10, 800, 6000, warmed=True):.0f} ms with warm-up")
