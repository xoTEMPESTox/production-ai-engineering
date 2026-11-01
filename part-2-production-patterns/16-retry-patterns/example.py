"""
Lesson 16: Retry Patterns.

Retrying well is mostly about when not to. Only transient errors are
retried; waits grow exponentially with full jitter, so clients that failed
together don't retry together; a server's Retry-After is honoured; no wait
is started that would overrun the caller's deadline; a retry budget caps
retries as a share of normal traffic, so an outage isn't multiplied by the
retry count; and every attempt of one logical operation carries the same
idempotency key.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class TransientError(Exception):
    """Timeouts, 429, 5xx: may succeed later. retry_after comes from the server, if it sent one."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentError(Exception):
    """400, 401, 404, policy refusals: the same request will fail the same way."""


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, (TransientError, TimeoutError, ConnectionError))


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_s: float = 0.5
    cap_s: float = 20.0
    deadline_s: float = 30.0  # the caller's total time, including every wait

    def backoff(self, attempt: int, rng: random.Random) -> float:
        """Full jitter: anywhere between 0 and the exponential ceiling."""
        return rng.uniform(0, min(self.cap_s, self.base_s * 2 ** (attempt - 1)))


@dataclass
class RetryBudget:
    """Retries are allowed in proportion to requests: ratio 0.1 means about one
    retry per ten requests once the initial allowance is used up."""
    ratio: float = 0.1
    max_tokens: float = 10.0
    tokens: float = 10.0

    def on_request(self) -> None:
        self.tokens = min(self.max_tokens, self.tokens + self.ratio)

    def try_spend(self) -> bool:
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


@dataclass(frozen=True)
class Attempt:
    number: int
    error: str
    waited_s: float = 0.0


@dataclass
class RetryOutcome:
    ok: bool
    value: Any = None
    attempts: list[Attempt] = field(default_factory=list)
    stop_reason: str = ""


def call_with_retry(fn: Callable[[str], Any], idempotency_key: str, policy: RetryPolicy = RetryPolicy(),
                    budget: Optional[RetryBudget] = None, sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic, rng: Optional[random.Random] = None) -> RetryOutcome:
    """fn receives the same idempotency key on every attempt, so a retried write
    that already succeeded downstream isn't applied twice."""
    rng = rng or random.Random()
    budget = budget if budget is not None else RetryBudget()
    budget.on_request()
    started, outcome = clock(), RetryOutcome(ok=False)
    for attempt in range(1, policy.max_attempts + 1):
        try:
            outcome.ok, outcome.value = True, fn(idempotency_key)
            return outcome
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if not is_retryable(exc):
                outcome.attempts.append(Attempt(attempt, error))
                outcome.stop_reason = "not retryable"
                return outcome
            if attempt == policy.max_attempts:
                outcome.attempts.append(Attempt(attempt, error))
                outcome.stop_reason = "attempts exhausted"
                return outcome
            wait = policy.backoff(attempt, rng)
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is not None:
                wait = max(wait, retry_after)  # the server knows when it can take us again
            if clock() - started + wait > policy.deadline_s:
                outcome.attempts.append(Attempt(attempt, error))
                outcome.stop_reason = f"waiting {wait:.1f}s would pass the {policy.deadline_s:.0f}s deadline"
                return outcome
            if not budget.try_spend():
                outcome.attempts.append(Attempt(attempt, error))
                outcome.stop_reason = "retry budget exhausted"
                return outcome
            outcome.attempts.append(Attempt(attempt, error, round(wait, 3)))
            sleep(wait)
    outcome.stop_reason = "max_attempts is below 1"
    return outcome


class FakeProvider:
    """Raises the scripted errors in order, then succeeds."""

    def __init__(self, errors: list[Exception]) -> None:
        self._errors, self.calls = list(errors), 0

    def __call__(self, key: str) -> str:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return "ok"


class FakeBilling:
    """Like real payment APIs: the first request with a key is applied, repeats return its result."""

    def __init__(self, time_out_after_applying: int = 0) -> None:
        self.applied: dict[str, str] = {}
        self._timeouts = time_out_after_applying

    def refund(self, key: str) -> str:
        self.applied.setdefault(key, f"refund for key {key}")
        if self._timeouts:
            self._timeouts -= 1
            raise TimeoutError("response lost after the refund was applied")
        return self.applied[key]


if __name__ == "__main__":
    now = [0.0]  # a fake clock: the demo moves time by setting now[0] (a list, so lambdas see the change)

    def fake_sleep(seconds: float) -> None:
        now[0] += seconds

    def run(errors: list[Exception], policy: RetryPolicy = RetryPolicy()) -> RetryOutcome:
        return call_with_retry(FakeProvider(errors), "op-1", policy, RetryBudget(), fake_sleep,
                               lambda: now[0], random.Random(7))

    cases = {
        "503, 503, then ok": [TransientError("503"), TransientError("503")],
        "429 with Retry-After 7s": [TransientError("429", retry_after=7.0)],
        "400 bad request": [PermanentError("400 prompt too long")],
        "429 with Retry-After 60s": [TransientError("429", retry_after=60.0)],
    }
    for title, errors in cases.items():
        result = run(errors)
        waits = [a.waited_s for a in result.attempts]
        print(f"{title:<26} ok={result.ok!s:<5} attempts={len(result.attempts) + result.ok} waits={waits} "
              f"{result.stop_reason}")

    for label, budget in (("no budget (4 attempts each)", RetryBudget(max_tokens=1e9, tokens=1e9)),
                          ("retry budget, ratio 0.1", RetryBudget())):
        down = FakeProvider([TransientError("503")] * 10_000)
        for i in range(100):
            call_with_retry(down, f"req-{i}", RetryPolicy(), budget, fake_sleep, lambda: now[0], random.Random(i))
        print(f"outage, 100 requests, {label}: {down.calls} calls to the provider")

    billing = FakeBilling(time_out_after_applying=1)
    result = call_with_retry(billing.refund, "refund-acct17-ch2", sleep=fake_sleep, clock=lambda: now[0])
    print(f"refund retried after a lost response: ok={result.ok}, refunds applied={len(billing.applied)}")

    rng = random.Random(1)
    delays = [RetryPolicy().backoff(3, rng) for _ in range(1_000)]
    print(f"1,000 clients at attempt 3 wait between {min(delays):.2f}s and {max(delays):.2f}s "
          f"(without jitter they would all retry at exactly 2.00s)")
