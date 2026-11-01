"""
Lesson 14: Error Handling.

Errors are sorted by what to do about them. Transient errors (timeouts,
429, 5xx) move on to a fallback model. Permanent errors (400, 401, 404)
stop and alert: retrying or switching models can't fix a bad request.
Format errors (unparseable output) try the next model. Policy errors
(a refusal, a blocked request) stop and go to a person, because shopping
the request around to another model is a way to get around the policy.
Only a primary answer is cached under the real key, so a degraded answer
can't outlive the outage, and a circuit breaker stops calling a provider
that keeps failing.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Literal, Optional, Protocol


class ErrorKind(str, Enum):
    TRANSIENT = "transient"  # may work if tried again or elsewhere
    PERMANENT = "permanent"  # the request itself is wrong; nothing will fix it automatically
    FORMAT = "format"        # the model answered, but not in the required shape
    POLICY = "policy"        # refused or blocked on purpose


class ProviderError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status} {message}")
        self.status = status


class FormatError(ValueError):
    pass


class PolicyError(Exception):
    pass


def classify(exc: Exception) -> ErrorKind:
    if isinstance(exc, PolicyError):
        return ErrorKind.POLICY
    if isinstance(exc, FormatError):
        return ErrorKind.FORMAT
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return ErrorKind.TRANSIENT
    if isinstance(exc, ProviderError):
        return ErrorKind.TRANSIENT if exc.status in (408, 429) or exc.status >= 500 else ErrorKind.PERMANENT
    return ErrorKind.PERMANENT  # unknown: don't hammer anything with retries


class CircuitBreaker:
    """closed -> (threshold failures in a row) -> open: fail fast for cooldown_s
    -> half_open: let one call through -> closed on success, open on failure."""

    def __init__(self, threshold: int = 3, cooldown_s: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.threshold, self.cooldown_s, self._clock = threshold, cooldown_s, clock
        self.failures, self.opened_at, self._trial_in_flight = 0, 0.0, False
        self.state: Literal["closed", "open", "half_open"] = "closed"

    def allow(self) -> bool:
        if self.state == "open":
            if self._clock() - self.opened_at < self.cooldown_s:
                return False  # fail fast: don't wait on a provider that is down
            self.state = "half_open"
        if self.state == "half_open":
            if self._trial_in_flight:
                return False  # one trial at a time
            self._trial_in_flight = True
        return True

    def record_success(self) -> None:
        self.state, self.failures, self._trial_in_flight = "closed", 0, False

    def record_failure(self) -> None:
        self.failures += 1  # only a success resets it, so a failed half-open trial reopens at once
        if self.failures >= self.threshold:
            self.state, self.opened_at = "open", self._clock()
        self._trial_in_flight = False


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class Answer:
    text: str
    source: Literal["cache", "primary", "fallback_model", "static"]
    error_kind: Optional[ErrorKind] = None
    needs_human: bool = False


STATIC_FALLBACK = "I can't answer that right now. Here is the help centre: https://help.example.com"
POLICY_REPLY = "I can't help with that request here. A member of the team will follow up."


class AnswerService:
    def __init__(self, primary: LLMClient, fallback: LLMClient, clock: Callable[[], float] = time.monotonic) -> None:
        self._chain = [("primary", primary, CircuitBreaker(clock=clock)),
                       ("fallback_model", fallback, CircuitBreaker(clock=clock))]
        self.cache: dict[str, str] = {}
        self.events: list[str] = []

    @property
    def breakers(self) -> dict[str, CircuitBreaker]:
        return {name: breaker for name, _, breaker in self._chain}

    def answer(self, question: str) -> Answer:
        key = hashlib.sha256(question.encode()).hexdigest()[:16]
        if key in self.cache:
            return Answer(self.cache[key], "cache")
        last_kind = None
        for name, client, breaker in self._chain:
            if not breaker.allow():
                self.events.append(f"{name}: circuit open, skipped")
                continue
            try:
                text = client.complete(question)
                if not text.strip():
                    raise FormatError("empty answer")
            except Exception as exc:
                last_kind = classify(exc)
                self.events.append(f"{name}: {last_kind.value} ({exc})")
                if last_kind in (ErrorKind.POLICY, ErrorKind.PERMANENT):
                    breaker.record_success()  # the provider answered; the request was the problem
                    reply = POLICY_REPLY if last_kind is ErrorKind.POLICY else STATIC_FALLBACK
                    return Answer(reply, "static", last_kind, needs_human=True)  # stop: no fallback
                breaker.record_failure()  # transient or format: try the next option
                continue
            breaker.record_success()
            if name == "primary":
                self.cache[key] = text  # never cache a fallback under the real key
            return Answer(text, name, last_kind)
        return Answer(STATIC_FALLBACK, "static", last_kind)


class ScriptedProvider:
    """Replies or raises per call, in order (the last item repeats)."""

    def __init__(self, script: list[object]) -> None:
        self._script, self.calls = list(script), 0

    def complete(self, prompt: str) -> str:
        item = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return str(item)


if __name__ == "__main__":
    now = [0.0]  # a fake clock: the demo moves time by setting now[0] (a list, so lambdas see the change)
    outage = ProviderError(503, "service unavailable")
    primary = ScriptedProvider([outage, outage, outage, "Refunds take 5 business days. [billing-02]"])
    fallback = ScriptedProvider(["Refunds usually take about a week."])
    service = AnswerService(primary, fallback, clock=lambda: now[0])

    question = "How long does a refund take?"
    for step in range(5):
        result = service.answer(question)
        print(f"t={now[0]:>4.0f}s  {result.source:<15} primary breaker={service.breakers['primary'].state:<9} "
              f"cached={bool(service.cache)}  {result.text}")
        now[0] += 20
    print("\nprimary was called", primary.calls, "times (not on every request during the outage)")

    for error in (PolicyError("request blocked by content policy"), ProviderError(400, "prompt too long")):
        spare = ScriptedProvider(["a fallback answer"])
        result = AnswerService(ScriptedProvider([error]), spare).answer("...")
        print(f"{result.error_kind.value}: needs_human={result.needs_human}, fallback model calls={spare.calls}, "
              f"reply={result.text!r}")
