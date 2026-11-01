# Lesson 25: Configuration - Architecture

This document shows how settings are built at start-up, how a flag is resolved per request, the components, and a typical misconfiguration handled naively and properly.

## Start-up: From Environment to Settings

```mermaid
flowchart TD
    Env([process environment + mounted secret files]) --> Scan[take PAIE_ variables only]
    Scan --> Unknown{any PAIE_ name that isn't a field?}
    Unknown -- yes --> Fail1[ConfigError: unknown settings, process exits]
    Unknown -- no --> Files[NAME_FILE: read the mounted secret]
    Files --> Validate[Settings.model_validate: types, ranges, extra forbidden]
    Validate -- errors --> Fail2[ConfigError: every field problem, no values echoed]
    Validate --> Prod{environment is prod?}
    Prod -- yes --> Rules[debug off, no star origins, keys present]
    Rules -- violated --> Fail3[ConfigError: every rule broken]
    Rules -- ok --> Frozen[frozen Settings]
    Prod -- no --> Frozen
    Frozen --> Log[log public_summary: values, secrets_set, fingerprint]
```

---

## Per Request: Resolving a Flag

```mermaid
flowchart LR
    Req([user, tenant]) --> K{flag killed?}
    K -- yes --> D1[default: kill switch]
    K -- no --> O{tenant override?}
    O -- yes --> V1[override variant]
    O -- no --> B[bucket = sha256 of flag name and user, mod 100]
    B --> R{inside a rollout range?}
    R -- yes --> V2[rollout variant]
    R -- no --> D2[default]
    D1 --> T[Resolution on the trace: variant + reason]
    V1 --> T
    V2 --> T
    D2 --> T
```

---

## Component Layout

```
+---------------------------------------------------------------------+
| Settings (Pydantic, frozen, extra=forbid)                           |
|   environment, default_model, request_timeout_s, max_steps,         |
|   daily_budget_usd, allowed_origins, debug                          |
|   llm_api_key: SecretStr?, database_url: SecretStr?                 |
|   _production_rules (model validator)   public_summary()            |
| load_settings(env) -> Settings | ConfigError                        |
|   PAIE_<NAME>, PAIE_<NAME>_FILE, unknown names rejected             |
+---------------------------------------------------------------------+
| Flag(name, variants, default, rollout, tenant_overrides, killed)    |
| FlagStore: set(flag, actor) -> validated + audited                  |
|            resolve(name, user_id, tenant) -> Resolution             |
+---------------------------------------------------------------------+
```

---

## A Misconfigured Deploy, Two Ways

The production deploy has `DEBUG=true`, `MAX_STEP=12` (a typo) and no API key.

| | `os.getenv` scattered through the code | `load_settings` + flags (this lesson) |
|---|---|---|
| Typo `MAX_STEP` | ignored; runs with 8 steps | `ConfigError: unknown settings` at start-up |
| `DEBUG=true` in production | runs, with debug pages exposed | refused: "debug must be off in production" |
| Missing API key | found by the first user request | refused at start-up, alongside the other problems |
| What's logged | possibly the whole environment, key included | the public summary: no secret values |
| The new prompt misbehaves | edit a constant and redeploy | kill switch: everyone back on v2 in seconds |
| "Who changed the model?" | `git blame`, if it was a commit | the flag audit: actor, flag, change |
