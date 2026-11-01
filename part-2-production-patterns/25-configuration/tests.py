import os
import tempfile
import unittest

from pydantic import ValidationError

from example import ConfigError, Environment, Flag, FlagStore, load_settings

KEY = "sk-test-0123456789abcdef"
PROD_OK = {"PAIE_ENVIRONMENT": "prod", "PAIE_LLM_API_KEY": KEY, "PAIE_DATABASE_URL": "postgresql://u:pw@db/app",
           "PAIE_ALLOWED_ORIGINS": "https://app.example.com"}


class TestSettings(unittest.TestCase):
    def test_strings_from_the_environment_become_typed_values(self) -> None:
        s = load_settings({"PAIE_MAX_STEPS": "12", "PAIE_DEBUG": "true", "PAIE_ALLOWED_ORIGINS": "https://a,https://b"})
        self.assertEqual((s.max_steps, s.debug, s.allowed_origins), (12, True, ["https://a", "https://b"]))

    def test_every_problem_is_reported_at_once(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            load_settings({"PAIE_REQUEST_TIMEOUT_S": "abc", "PAIE_MAX_STEPS": "0", "PAIE_DAILY_BUDGET_USD": "-5"})
        message = str(ctx.exception)
        for field in ("request_timeout_s", "max_steps", "daily_budget_usd"):
            self.assertIn(field, message)

    def test_misspelled_setting_is_an_error_not_a_silent_default(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            load_settings({"PAIE_MAX_STEP": "12"})
        self.assertIn("PAIE_MAX_STEP", str(ctx.exception))

    def test_unrelated_environment_variables_are_ignored(self) -> None:
        self.assertEqual(load_settings({"HOME": "/home/x", "PATH": "/bin"}).environment, Environment.DEV)

    def test_production_rules(self) -> None:
        self.assertEqual(load_settings(PROD_OK).environment, Environment.PROD)
        with self.assertRaises(ConfigError) as ctx:
            load_settings({**PROD_OK, "PAIE_DEBUG": "true", "PAIE_ALLOWED_ORIGINS": "*"})
        self.assertIn("debug must be off", str(ctx.exception))
        self.assertIn("'*'", str(ctx.exception))
        missing = {k: v for k, v in PROD_OK.items() if k != "PAIE_LLM_API_KEY"}
        with self.assertRaises(ConfigError):
            load_settings(missing)

    def test_settings_cannot_be_changed_after_startup(self) -> None:
        settings = load_settings({})
        with self.assertRaises(ValidationError):
            settings.max_steps = 99


class TestSecrets(unittest.TestCase):
    def test_secret_never_appears_in_repr_dump_or_summary(self) -> None:
        settings = load_settings({"PAIE_LLM_API_KEY": KEY})
        for text in (repr(settings), str(settings), settings.model_dump_json(), str(settings.public_summary())):
            self.assertNotIn(KEY, text)
        self.assertEqual(settings.llm_api_key.get_secret_value(), KEY)
        self.assertEqual(settings.public_summary()["secrets_set"], ["llm_api_key"])

    def test_secret_from_a_mounted_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "llm_api_key")
            with open(path, "w", encoding="utf-8") as f:
                f.write(KEY + "\n")
            settings = load_settings({"PAIE_LLM_API_KEY_FILE": path})
        self.assertEqual(settings.llm_api_key.get_secret_value(), KEY)

    def test_validation_errors_do_not_echo_values(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            load_settings({"PAIE_MAX_STEPS": "sk-pasted-in-the-wrong-variable"})
        self.assertNotIn("sk-pasted", str(ctx.exception))

    def test_fingerprint_changes_with_any_public_setting(self) -> None:
        a = load_settings({}).public_summary()["fingerprint"]
        b = load_settings({"PAIE_MAX_STEPS": "9"}).public_summary()["fingerprint"]
        c = load_settings({"PAIE_LLM_API_KEY": KEY}).public_summary()["fingerprint"]
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)  # which secrets are set is part of the summary; their values aren't


FLAG = Flag("prompt.grounded_answer", ("v2", "v3"), default="v2", rollout={"v3": 10},
            tenant_overrides={"acme-beta": "v3"})


class TestFlags(unittest.TestCase):
    def setUp(self) -> None:
        self.flags = FlagStore()
        self.flags.set(FLAG, actor="release-bot")

    def test_rollout_is_sticky_and_close_to_its_percentage(self) -> None:
        picks = [self.flags.resolve(FLAG.name, f"u{i}", "globex").variant for i in range(10_000)]
        self.assertTrue(900 < picks.count("v3") < 1100)
        self.assertTrue(picks == [self.flags.resolve(FLAG.name, f"u{i}", "globex").variant for i in range(10_000)])

    def test_tenant_override_beats_the_rollout(self) -> None:
        resolution = self.flags.resolve(FLAG.name, "u1", "acme-beta")
        self.assertEqual((resolution.variant, resolution.reason), ("v3", "override for acme-beta"))

    def test_kill_switch_beats_everything(self) -> None:
        self.flags.set(Flag(**{**FLAG.__dict__, "killed": True}), actor="on-call")
        self.assertEqual(self.flags.resolve(FLAG.name, "u1", "acme-beta").variant, "v2")

    def test_unknown_variants_and_over_100_percent_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.flags.set(Flag("model.default", ("small-model",), default="huge-model"), actor="x")
        with self.assertRaises(ValueError):
            self.flags.set(Flag("f", ("a", "b", "c"), default="a", rollout={"b": 60, "c": 50}), actor="x")
        self.assertEqual(len(self.flags.audit), 1)  # refused changes are not applied

    def test_every_change_is_audited(self) -> None:
        self.flags.set(Flag(**{**FLAG.__dict__, "rollout": {"v3": 50}}), actor="release-bot")
        self.assertEqual([(a, f) for a, f, _ in self.flags.audit],
                         [("release-bot", FLAG.name), ("release-bot", FLAG.name)])
        self.assertIn("{'v3': 50}", self.flags.audit[-1][2])

    def test_different_flags_pick_different_users(self) -> None:
        other = Flag("model.default", ("small", "large"), default="small", rollout={"large": 10})
        self.flags.set(other, actor="x")
        a = {i for i in range(5_000) if self.flags.resolve(FLAG.name, f"u{i}", "g").variant == "v3"}
        b = {i for i in range(5_000) if self.flags.resolve(other.name, f"u{i}", "g").variant == "large"}
        self.assertLess(len(a & b), len(a) * 0.3)  # mostly different users, not the same 10%


if __name__ == "__main__":
    unittest.main()
