"""Configuration. The API token is held as a SecretStr and is never printed or logged."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

# Published Runta rates (https://runta.com/pricing/, read 2026-10-10). Used for *estimates* only.
USD_PER_VCPU_HOUR = 0.0504
USD_PER_GIB_HOUR = 0.0162
USD_PER_DISK_GIB_HOUR = 0.000108
DEFAULT_DISK_GIB = 32  # observed root disk on a default runtime (evidence/02-smoke.json)

# Every runtime/checkpoint we create is named fb-<run_id>-..., so cleanup can't touch anything else.
RESOURCE_PREFIX = "fb"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    token: SecretStr
    endpoint: str | None = None
    vcpus: int = 2
    memory_mib: int = 4096
    budget_usd: float = 10.0
    step_timeout_s: float = 900.0

    def usd_per_second(self) -> float:
        per_hour = (
            self.vcpus * USD_PER_VCPU_HOUR
            + (self.memory_mib / 1024) * USD_PER_GIB_HOUR
            + DEFAULT_DISK_GIB * USD_PER_DISK_GIB_HOUR
        )
        return per_hour / 3600


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings(env_file: Path | None = Path(".env"), **overrides: object) -> Settings:
    """Environment variables win over the .env file; explicit overrides win over both."""
    file_values = _read_dotenv(env_file) if env_file else {}
    token = os.environ.get("RUNTA_TOKEN") or file_values.get("RUNTA_TOKEN", "")
    if not token:
        raise ConfigError("RUNTA_TOKEN is not set (export it or put it in .env).")
    endpoint = os.environ.get("RUNTA_ENDPOINT") or file_values.get("RUNTA_ENDPOINT") or None
    clean = {k: v for k, v in overrides.items() if v is not None}
    return Settings(token=SecretStr(token), endpoint=endpoint, **clean)  # type: ignore[arg-type]
