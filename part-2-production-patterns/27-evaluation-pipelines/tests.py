import os
import tempfile
import unittest
from dataclasses import replace

from example import (
    BASELINE_MISSES, CANDIDATE_MISSES, V1, V2, Dataset, Example, FakeModel, OutputCache, bootstrap_ci,
    build_dataset, exact_match, gate, generate, score, should_sample,
)

DATA = build_dataset()


def scores_for(misses: set[str], config=V1) -> dict[str, float]:
    outputs, _ = generate(FakeModel(misses), config, DATA, OutputCache())
    return score(outputs, DATA, exact_match)


class TestCaching(unittest.TestCase):
    def test_second_run_is_served_from_cache(self) -> None:
        cache, model = OutputCache(), FakeModel(BASELINE_MISSES)
        generate(model, V1, DATA, cache)
        _, stats = generate(model, V1, DATA, cache)
        self.assertEqual((stats.model_calls, stats.cache_hits), (0, 40))
        self.assertEqual(model.calls, 40)

    def test_anything_that_changes_the_output_changes_the_key(self) -> None:
        ex = DATA.examples[0]
        base = V1.cache_key(DATA, ex)
        variants = [replace(V1, prompt_version="v2"), replace(V1, prompt_template="other {question}"),
                    replace(V1, temperature=0.7), replace(V1, model="bigger-model")]
        for config in variants:
            self.assertNotEqual(config.cache_key(DATA, ex), base, config)
        self.assertNotEqual(V1.cache_key(replace(DATA, version="2026-12-01"), ex), base)

    def test_failed_calls_are_retried_not_cached(self) -> None:
        cache = OutputCache()
        model = FakeModel(set(), flaky={"billing question 0"})
        first, stats = generate(model, V1, DATA, cache)
        self.assertEqual(len(stats.errors), 1)
        self.assertEqual(exact_match(first["billing-0"], DATA.examples[0]), 0.0)

        second, stats = generate(model, V1, DATA, cache)
        self.assertEqual(stats.model_calls, 1)  # only the failed example
        self.assertEqual(exact_match(second["billing-0"], DATA.examples[0]), 1.0)

    def test_cache_file_survives_a_new_process(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "outputs.jsonl")
            generate(FakeModel(set()), V1, DATA, OutputCache(path))
            _, stats = generate(FakeModel(set()), V1, DATA, OutputCache(path))
            self.assertEqual(stats.model_calls, 0)

    def test_new_metric_rescored_without_generating(self) -> None:
        cache, model = OutputCache(), FakeModel(BASELINE_MISSES)
        outputs, _ = generate(model, V1, DATA, cache)
        calls_before = model.calls
        short = score(outputs, DATA, lambda out, ex: float(len(out) < 40))
        self.assertEqual(len(short), 40)
        self.assertEqual(model.calls, calls_before)


class TestStatistics(unittest.TestCase):
    def test_interval_contains_the_mean_and_is_reproducible(self) -> None:
        values = [1.0] * 30 + [0.0] * 10
        low, high = bootstrap_ci(values)
        self.assertLessEqual(low, 0.75)
        self.assertGreaterEqual(high, 0.75)
        self.assertEqual(bootstrap_ci(values), (low, high))

    def test_more_examples_give_a_narrower_interval(self) -> None:
        small = bootstrap_ci([1.0] * 15 + [0.0] * 5)
        large = bootstrap_ci([1.0] * 300 + [0.0] * 100)
        self.assertLess(large[1] - large[0], small[1] - small[0])


class TestGate(unittest.TestCase):
    def test_broken_slice_fails_even_when_the_average_improves(self) -> None:
        base, cand = scores_for(BASELINE_MISSES), scores_for(CANDIDATE_MISSES, V2)
        result = gate(base, cand, DATA)
        self.assertGreater(result.overall_delta, 0)
        self.assertFalse(result.passed)
        self.assertIn("slice 'shipping' dropped 0.25", result.reasons[0])

    def test_significant_overall_drop_fails(self) -> None:
        base = scores_for(set())
        worse = scores_for({ex.question for ex in DATA.examples[::3]})  # a third of everything wrong
        result = gate(base, worse, DATA, max_slice_drop=1.0)  # isolate the overall check
        self.assertFalse(result.passed)
        self.assertLess(result.delta_ci[1], 0)
        self.assertIn("beyond the noise", result.reasons[0])

    def test_noise_sized_change_passes(self) -> None:
        base = scores_for({"billing question 1"})
        cand = scores_for({"billing question 2"})  # one item swapped for another
        self.assertTrue(gate(base, cand, DATA).passed)

    def test_tiny_slice_is_reported_not_gated(self) -> None:
        data = Dataset("d", "1", tuple(DATA.examples) + (Example("vip-0", "vip question 0", "vip answer 0", "vip"),))
        base = {**scores_for(set()), "vip-0": 1.0}
        cand = {**scores_for(set()), "vip-0": 0.0}
        result = gate(base, cand, data)
        self.assertTrue(result.passed)
        self.assertIn("slice 'vip' has 1 examples: too few to gate on", result.notes)


class TestOnlineSampling(unittest.TestCase):
    def test_sampling_is_deterministic_and_near_the_rate(self) -> None:
        picks = [should_sample(f"t-{i}", 0.05) for i in range(10_000)]
        self.assertEqual(picks, [should_sample(f"t-{i}", 0.05) for i in range(10_000)])
        self.assertTrue(400 < sum(picks) < 600)

    def test_flagged_traces_are_always_sampled(self) -> None:
        self.assertTrue(all(should_sample(f"t-{i}", 0.0, flagged=True) for i in range(100)))


if __name__ == "__main__":
    unittest.main()
