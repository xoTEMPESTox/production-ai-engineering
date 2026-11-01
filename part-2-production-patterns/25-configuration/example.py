"""
Lesson 25: Configuration.

Settings are a typed, frozen object built once at startup from environment
variables (or mounted secret files), validated in full so every mistake is
reported at once, and stricter in production. Secrets are SecretStr: they
never appear in logs, reprs or dumps, and they never live in git. Prompt and
model versions are feature flags, not constants: typed variants, per-tenant
overrides, a sticky percentage rollout and a kill switch, changeable
without a deploy and audited when changed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

PREFIX = "PAIE_"


class Environment(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: Environment = Environment.DEV
    default_model: str = "small-model"
    request_timeout_s: float = Field(30.0, gt=0, le=120)
    max_steps: int = Field(8, ge=1, le=50)
    daily_budget_usd: float = Field(50.0, gt=0)
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    debug: bool = False
    llm_api_key: Optional[SecretStr] = None
    database_url: Optional[SecretStr] = None

    @model_validator(mode="after")
    def _production_rules(self) -> "Settings":
        if self.environment is not Environment.PROD:
            return self
        problems = []
        if self.debug:
            problems.append("debug must be off in production")
        if "*" in self.allowed_origins:
            problems.append("allowed_origins may not contain '*' in production")
        for name in ("llm_api_key", "database_url"):
            if getattr(self, name) is None:
                problems.append(f"{name} is required in production")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def public_summary(self) -> dict:
        """Safe to log at startup: every non-secret value, which secrets are set, and a fingerprint."""
        values = self.model_dump(mode="json", exclude={"llm_api_key", "database_url"})
        values["secrets_set"] = sorted(n for n in ("llm_api_key", "database_url") if getattr(self, n))
        values["fingerprint"] = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:12]
        return values


class ConfigError(Exception):
    pass


def load_settings(env: Mapping[str, str]) -> Settings:
    """PAIE_<NAME> or PAIE_<NAME>_FILE (a mounted secret). Unknown PAIE_ variables are errors:
    a typo like PAIE_MAX_STEP must not silently fall back to the default."""
    fields = set(Settings.model_fields)
    raw: dict[str, object] = {}
    unknown = []
    for key, value in env.items():
        if not key.startswith(PREFIX):
            continue
        name = key[len(PREFIX):].lower()
        if name.endswith("_file") and name[:-5] in fields:
            raw[name[:-5]] = Path(value).read_text(encoding="utf-8").strip()
        elif name in fields:
            raw[name] = value.split(",") if name == "allowed_origins" else value
        else:
            unknown.append(key)
    if unknown:
        raise ConfigError(f"unknown settings: {sorted(unknown)}")
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:  # every problem at once, never the secret values themselves
        problems = [f"{'.'.join(map(str, e['loc'])) or 'settings'}: {e['msg']}" for e in exc.errors()]
        raise ConfigError("; ".join(problems)) from None


# --- Feature flags for prompt and model versions ---

@dataclass(frozen=True)
class Flag:
    name: str
    variants: tuple[str, ...]
    default: str
    rollout: dict[str, int] = field(default_factory=dict)          # variant -> percent of users
    tenant_overrides: dict[str, str] = field(default_factory=dict)  # e.g. a beta customer
    killed: bool = False                                             # everyone gets the default


@dataclass(frozen=True)
class Resolution:
    flag: str
    variant: str
    reason: str  # recorded on the trace (Lesson 22), so every answer says which variant produced it


class FlagStore:
    """In memory here; a flag service or a config table in production. Changes need no deploy."""

    def __init__(self) -> None:
        self._flags: dict[str, Flag] = {}
        self.audit: list[tuple[str, str, str]] = []  # (actor, flag, change)

    def set(self, flag: Flag, actor: str) -> None:
        named = {flag.default, *flag.rollout, *flag.tenant_overrides.values()}
        if not named <= set(flag.variants):
            raise ValueError(f"{flag.name}: unknown variants {sorted(named - set(flag.variants))}")
        if sum(flag.rollout.values()) > 100:
            raise ValueError(f"{flag.name}: rollout adds up to more than 100%")
        self._flags[flag.name] = flag
        self.audit.append((actor, flag.name, f"default={flag.default} rollout={flag.rollout} "
                                             f"overrides={flag.tenant_overrides} killed={flag.killed}"))

    def resolve(self, name: str, user_id: str, tenant: str) -> Resolution:
        flag = self._flags[name]
        if flag.killed:
            return Resolution(name, flag.default, "kill switch")
        if tenant in flag.tenant_overrides:
            return Resolution(name, flag.tenant_overrides[tenant], f"override for {tenant}")
        # Hash the flag name with the user, so different flags don't pick the same users.
        bucket = int(hashlib.sha256(f"{name}:{user_id}".encode()).hexdigest()[:8], 16) % 100
        start = 0
        for variant, percent in flag.rollout.items():
            if start <= bucket < start + percent:
                return Resolution(name, variant, f"rollout {percent}%")
            start += percent
        return Resolution(name, flag.default, "default")


if __name__ == "__main__":
    dev = load_settings({"PAIE_MAX_STEPS": "12", "PAIE_LLM_API_KEY": "sk-dev-0123456789", "HOME": "/home/x"})
    print("dev settings:", dev.public_summary())
    print("repr never shows the key:", repr(dev.llm_api_key))

    for env in ({"PAIE_ENVIRONMENT": "prod", "PAIE_DEBUG": "true", "PAIE_ALLOWED_ORIGINS": "*"},
                {"PAIE_MAX_STEP": "12"},
                {"PAIE_REQUEST_TIMEOUT_S": "abc", "PAIE_MAX_STEPS": "0"}):
        try:
            load_settings(env)
        except ConfigError as exc:
            print(f"refused {sorted(env)}:\n   {exc}")

    flags = FlagStore()
    flags.set(Flag("prompt.grounded_answer", ("v2", "v3"), default="v2", rollout={"v3": 10},
                   tenant_overrides={"acme-beta": "v3"}), actor="release-bot")
    picks = [flags.resolve("prompt.grounded_answer", f"user-{i}", "globex").variant for i in range(10_000)]
    print(f"\nv3 for {picks.count('v3') / 100:.1f}% of globex users; acme-beta gets",
          flags.resolve("prompt.grounded_answer", "user-1", "acme-beta"))
    flags.set(Flag("prompt.grounded_answer", ("v2", "v3"), default="v2", rollout={"v3": 10}, killed=True),
              actor="on-call")
    print("after the kill switch:", flags.resolve("prompt.grounded_answer", "user-1", "acme-beta"))
    try:
        flags.set(Flag("model.default", ("small-model",), default="huge-model"), actor="someone")
    except ValueError as exc:
        print("refused flag:", exc)
    print("audit:", [(actor, flag) for actor, flag, _ in flags.audit])
