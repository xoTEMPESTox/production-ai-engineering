# Lesson 25: Configuration

Learn how to configure an AI service without surprises: typed settings validated in full at startup, stricter rules for production, secrets that never reach logs or git, and feature flags for prompt and model versions that change without a deploy.

> **Pillars:** Harness · Agent · RAG  |  **Harness layer:** configuration, meaning which model, prompt, limits and keys this process runs with, and who changed them
>
> *Parse it once, refuse it loudly, never print a secret, and flag what you'll want to change at 2 a.m.*

## Why does this exist?
An AI service has a lot of knobs: which model, which prompt version, timeouts, step and token budgets (Lessons 02 and 28), the allowed origins, the database URL, API keys. Some differ per environment, some must never appear in a log, and some (prompt and model versions above all) you'll want to change in minutes, without a deploy, when quality drops.

Read from the environment in scattered places, as strings, with silent defaults, configuration becomes a source of quiet failures. A typo gets the default, a production box runs with debug on, and an API key shows up in a stack trace.

---

## What problem does it solve?
1. **Wrong types and silent defaults**: `"0"` steps, `"abc"` seconds, a misspelled variable name.
2. **Environment mistakes**: debug mode or `*` origins in production, missing keys found at the first request.
3. **Secret leaks**: keys in git, in logs, in error messages, in config dumps.
4. **Changing behaviour without a deploy**: roll a new prompt to 10%, give a beta customer the new model, kill a bad variant instantly.
5. **Traceability**: knowing exactly which settings and which variant produced a given answer.

---

## How would beginners implement this?
```python
# settings.py
MODEL = os.getenv("MODEL", "large-model")
MAX_STEPS = os.getenv("MAX_STEPS", 8)            # a str when set, an int when not
TIMEOUT = float(os.getenv("TIMEOUT", "30"))      # crashes deep inside a request if malformed
OPENAI_API_KEY = "sk-live-..."                   # "temporary", committed
PROMPT_VERSION = "v3"                            # a constant: changing it is a deploy
print("starting with", vars())                   # logs the key
```

### Why does that fail?
1. **Types depend on whether the variable is set.** `MAX_STEPS` is a string from the environment and an int by default, so comparisons misbehave.
2. **Failures surface late.** A malformed value crashes some request an hour later, not the process at start-up.
3. **Typos are silent.** `MAX_STEP=12` is ignored, and the service runs with 8.
4. **The key is in git**, so it's in every clone and fork forever, and printing `vars()` puts it in the logs too.
5. **No environment rules.** Nothing stops `DEBUG=true` in production.
6. **Prompt versions are constants**, so a rollback is a redeploy, and there's no way to give 10% of users the new one.

---

## How is this solved in production?
**1. One typed, frozen `Settings` object, built once.** A Pydantic model declares every setting with its type, default and limits (`max_steps: int = Field(8, ge=1, le=50)`). `load_settings(os.environ)` reads `PAIE_<NAME>` variables, converts and validates them, and returns an object that can't be modified afterwards. The app gets its configuration from that object, never from `os.environ` directly.

**2. Fail at start-up, with every problem listed.** A `ValidationError` is turned into one `ConfigError` naming each bad field: `request_timeout_s: Input should be a valid number; max_steps: Input should be greater than or equal to 1`. The process doesn't start. The messages carry field names and rules, never the rejected values, so a key pasted into the wrong variable isn't echoed into the logs.

**3. Unknown variables are errors.** Any `PAIE_` variable that isn't a field (`PAIE_MAX_STEP`) is rejected, so a typo can't silently fall back to a default.

**4. Production is stricter.** A model validator runs only when `environment == prod`: debug off, no `*` origins, the API key and database URL required. Everything wrong is listed together.

**5. Secrets are `SecretStr`, and come from the environment or a file.** Their `repr`, `str` and JSON dumps show `**********`. The value is read only where it's used (`settings.llm_api_key.get_secret_value()`, inside the client that calls the provider). `PAIE_LLM_API_KEY_FILE=/run/secrets/llm_api_key` reads a secret mounted by the platform, which is the common convention for container secrets. In production, secrets come from a secret manager into the environment or a mounted file, and never from the repository. Keep `.env` in `.gitignore`, commit only a `.env.example` with names and no values, and run a secret scanner in a pre-commit hook and in continuous integration (CI). If a key is ever committed, **rotate it**: rewriting history doesn't un-leak it.

**6. Log a safe summary.** `public_summary()` returns every non-secret value, which secrets are set (not their values), and a short fingerprint. Logged at start-up and attached to traces (Lesson 22), it answers "what configuration produced this answer?".

**7. Prompt and model versions are feature flags:**
```python
flags.set(Flag("prompt.grounded_answer", variants=("v2", "v3"), default="v2",
               rollout={"v3": 10}, tenant_overrides={"acme-beta": "v3"}), actor="release-bot")
flags.resolve("prompt.grounded_answer", user_id, tenant)  # -> Resolution(variant, reason)
```
Resolution order: the **kill switch** (everyone gets the default), then **tenant overrides**, then a **sticky percentage rollout** (a hash of the flag name and the user, so different flags pick different users), then the default. Variants must be registered, so a typo can't route traffic to a prompt that doesn't exist, and rollouts can't add up to more than 100%. Every change is **audited** with its actor. The `Resolution` (variant and reason) goes on the trace, so evals and incident reviews know which variant answered. Lesson 24's canary is this mechanism driven by a controller.

---

## Design Tradeoffs

### Environment variables vs. config files vs. a config service
* **Environment variables** (this lesson): universal and simple, and the standard way platforms inject secrets. Flat, and awkward for nested or large configuration.
* **Config files** (YAML or JSON, per environment, in the repo): reviewable and structured, but they need a deploy to change and must never contain secrets.
* **A config or flag service**: changes without deploys, with targeting and audit. It's another dependency, so cache its values and define behaviour when it's unreachable (fall back to safe defaults).

### Static settings vs. dynamic flags
* **Static** (read at start-up, frozen): predictable, the same for every request, easy to reason about. Changing one is a restart.
* **Dynamic flags**: change in seconds, per tenant or percentage. There are more states to test, and stale flags pile up, so give each flag an owner and an expiry, and delete it after full rollout.

### Failing fast vs. degrading
* **Fail fast** (this lesson): bad config never serves a request. A typo in a non-critical setting also blocks the deploy, which is usually what you want.
* **Degrade with a warning**: the service starts anyway. That's appropriate only for optional features, and an ignored warning becomes silent misbehaviour.

---

## Failure Cases to Watch For
1. **Secrets in git**, including "temporary" ones and test keys that turn out to be live.
2. **Secrets in logs and errors**: config dumps, `repr` of settings objects, validation errors that echo input values.
3. **Silent defaults** for misspelled or malformed settings.
4. **Late validation**: a bad value found by the first request that needs it, hours after the deploy.
5. **Production running with development settings** (debug, permissive origins, test keys).
6. **Prompt and model versions as code constants**: no gradual rollout and no instant rollback.
7. **Unaudited flag changes**: behaviour changed and nobody knows who changed it, or when.
8. **Flag sprawl**: dozens of stale flags, each an untested combination.

---

## Try It
```sh
cd part-2-production-patterns/25-configuration
python example.py            # loads settings from in-code dicts, no real environment needed
python -m pytest tests.py -q
```
What to watch for: the dev summary lists `secrets_set: ['llm_api_key']` and a fingerprint, but never the key, and the `repr` shows `SecretStr('**********')`. The production config lists all four problems at once. `PAIE_MAX_STEP` is refused as unknown. About 10% of globex users get v3, acme-beta gets it by override, and after the kill switch everyone is back on v2. The flag naming an unregistered model is refused, and the audit lists both changes.

Experiment: in `load_settings`, stop collecting unknown variables (replace `unknown.append(key)` with `pass`) and run the tests. `test_misspelled_setting_is_an_error_not_a_silent_default` fails: `PAIE_MAX_STEP=12` is quietly ignored, and the service runs with 8 steps.

**Next:** Lesson 26 watches the system run: SLOs for latency and errors, quality signals like schema-failure, fallback and human-edit rates, alerts, and drift.
