import json
import unittest

from example import (
    STATIC_FALLBACK, AnswerService, CircuitBreaker, ErrorKind, FormatError, PolicyError, ProviderError,
    ScriptedProvider, classify,
)

DOWN = ProviderError(503, "unavailable")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestClassification(unittest.TestCase):
    def test_taxonomy(self) -> None:
        cases = {
            TimeoutError("read timed out"): ErrorKind.TRANSIENT,
            ConnectionError("reset"): ErrorKind.TRANSIENT,
            ProviderError(429, "rate limited"): ErrorKind.TRANSIENT,
            ProviderError(503, "unavailable"): ErrorKind.TRANSIENT,
            ProviderError(400, "prompt too long"): ErrorKind.PERMANENT,
            ProviderError(401, "bad key"): ErrorKind.PERMANENT,
            FormatError("not JSON"): ErrorKind.FORMAT,
            json.JSONDecodeError("x", "", 0): ErrorKind.PERMANENT,  # unknown types are not retried
            PolicyError("refused"): ErrorKind.POLICY,
        }
        for exc, kind in cases.items():
            self.assertEqual(classify(exc), kind, repr(exc))


class TestFallbacks(unittest.TestCase):
    def test_transient_error_falls_back_and_is_not_cached(self) -> None:
        primary = ScriptedProvider([DOWN, "the real answer"])
        service = AnswerService(primary, ScriptedProvider(["a degraded answer"]))

        first = service.answer("q")
        self.assertEqual((first.source, first.error_kind), ("fallback_model", ErrorKind.TRANSIENT))
        self.assertEqual(service.cache, {})  # never cache a fallback under the real key

        second = service.answer("q")  # the primary has recovered
        self.assertEqual((second.source, second.text), ("primary", "the real answer"))
        self.assertEqual(service.answer("q").source, "cache")

    def test_format_error_tries_the_next_model(self) -> None:
        service = AnswerService(ScriptedProvider(["   "]), ScriptedProvider(["fine"]))
        result = service.answer("q")
        self.assertEqual((result.source, result.error_kind), ("fallback_model", ErrorKind.FORMAT))

    def test_permanent_error_stops_without_fallback(self) -> None:
        primary, fallback = ScriptedProvider([ProviderError(400, "bad request")]), ScriptedProvider(["x"])
        result = AnswerService(primary, fallback).answer("q")
        self.assertEqual((result.error_kind, result.needs_human), (ErrorKind.PERMANENT, True))
        self.assertEqual((primary.calls, fallback.calls), (1, 0))

    def test_policy_refusal_is_not_shopped_around(self) -> None:
        fallback = ScriptedProvider(["sure, here you go"])
        result = AnswerService(ScriptedProvider([PolicyError("blocked")]), fallback).answer("q")
        self.assertEqual((result.error_kind, result.needs_human), (ErrorKind.POLICY, True))
        self.assertEqual(fallback.calls, 0)

    def test_everything_down_gives_the_static_answer_uncached(self) -> None:
        service = AnswerService(ScriptedProvider([DOWN]), ScriptedProvider([TimeoutError()]))
        result = service.answer("q")
        self.assertEqual((result.source, result.text), ("static", STATIC_FALLBACK))
        self.assertEqual(service.cache, {})


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_threshold_and_fails_fast(self) -> None:
        clock = Clock()
        primary = ScriptedProvider([DOWN])
        service = AnswerService(primary, ScriptedProvider(["fallback"]), clock=clock)
        for _ in range(5):
            service.answer("q")
        self.assertEqual(service.breakers["primary"].state, "open")
        self.assertEqual(primary.calls, 3)  # calls 4 and 5 never reached the provider

    def test_half_open_trial_closes_on_success(self) -> None:
        clock = Clock()
        breaker = CircuitBreaker(threshold=2, cooldown_s=30, clock=clock)
        breaker.record_failure()
        breaker.record_failure()
        self.assertFalse(breaker.allow())
        clock.now = 30
        self.assertTrue(breaker.allow())   # the single trial
        self.assertFalse(breaker.allow())  # nobody else while it's in flight
        breaker.record_success()
        self.assertEqual(breaker.state, "closed")

    def test_failed_trial_reopens_for_another_cooldown(self) -> None:
        clock = Clock()
        breaker = CircuitBreaker(threshold=3, cooldown_s=30, clock=clock)
        for _ in range(3):
            breaker.record_failure()
        clock.now = 30
        breaker.allow()
        breaker.record_failure()
        self.assertEqual(breaker.state, "open")
        clock.now = 45
        self.assertFalse(breaker.allow())

    def test_permanent_errors_do_not_trip_the_breaker(self) -> None:
        primary = ScriptedProvider([ProviderError(400, "bad request")])
        service = AnswerService(primary, ScriptedProvider(["x"]))
        for _ in range(5):
            service.answer(f"q{_}")
        self.assertEqual(service.breakers["primary"].state, "closed")
        self.assertEqual(primary.calls, 5)


if __name__ == "__main__":
    unittest.main()
