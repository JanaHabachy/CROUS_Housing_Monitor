"""Configuration loading for the CROUS monitor.

All *secrets* (SMTP credentials, recipient address, ...) come exclusively
from environment variables so they can be injected via GitHub Secrets and
never need to be hardcoded or committed to the repository.

Non-secret, purely operational settings (polling interval, heartbeat
interval, HTTP timeouts, ...) have sensible defaults but can be overridden
with environment variables too, which makes the monitor configurable
without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import SearchTarget

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEARCHES_FILE = REPO_ROOT / "config" / "searches.yaml"
DEFAULT_STATE_FILE = REPO_ROOT / "state" / "seen_listings.json"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise ConfigError(
            f"Missing required environment variable/secret: {name}. "
            "Set it as a GitHub Secret (Settings > Secrets and variables > "
            "Actions) or export it locally before running the monitor."
        )
    return value


@dataclass
class EmailConfig:
    smtp_server: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    email_from: str
    email_to: str
    use_tls: bool = True

    @staticmethod
    def from_env() -> "EmailConfig":
        return EmailConfig(
            smtp_server=_env("SMTP_SERVER", required=True),
            smtp_port=int(_env("SMTP_PORT", required=True)),
            smtp_username=_env("SMTP_USERNAME", required=True),
            smtp_password=_env("SMTP_PASSWORD", required=True),
            email_from=_env("EMAIL_FROM", required=True),
            email_to=_env("EMAIL_TO", required=True),
            use_tls=_env("SMTP_USE_TLS", "true").lower() not in ("0", "false", "no"),
        )


@dataclass
class MonitorConfig:
    searches: list[SearchTarget]
    email: EmailConfig
    state_file: Path = DEFAULT_STATE_FILE
    heartbeat_interval_hours: float = 48.0
    request_timeout_seconds: float = 20.0
    max_retries: int = 5
    retry_backoff_base_seconds: float = 2.0
    request_delay_seconds: float = 1.0
    max_pages_per_search: int = 20
    user_agent: str = (
        "Mozilla/5.0 (compatible; CrousHousingMonitor/1.0; "
        "+https://github.com/) personal-use housing alert bot"
    )

    @staticmethod
    def load(searches_file: Path | None = None) -> "MonitorConfig":
        searches_file = searches_file or Path(
            _env("CROUS_SEARCHES_FILE", str(DEFAULT_SEARCHES_FILE))
        )
        searches = load_searches(searches_file)

        state_file = Path(_env("CROUS_STATE_FILE", str(DEFAULT_STATE_FILE)))

        return MonitorConfig(
            searches=searches,
            email=EmailConfig.from_env(),
            state_file=state_file,
            heartbeat_interval_hours=float(
                _env("CROUS_HEARTBEAT_INTERVAL_HOURS", "48")
            ),
            request_timeout_seconds=float(_env("CROUS_REQUEST_TIMEOUT", "20")),
            max_retries=int(_env("CROUS_MAX_RETRIES", "5")),
            retry_backoff_base_seconds=float(_env("CROUS_RETRY_BACKOFF_BASE", "2")),
            request_delay_seconds=float(_env("CROUS_REQUEST_DELAY", "1")),
            max_pages_per_search=int(_env("CROUS_MAX_PAGES", "20")),
        )


def load_searches(path: Path) -> list[SearchTarget]:
    if not path.exists():
        raise ConfigError(f"Searches config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    raw_searches = data.get("searches") or []
    if not raw_searches:
        raise ConfigError(
            f"No searches defined in {path}. Add at least one entry under "
            "the 'searches:' key."
        )

    targets: list[SearchTarget] = []
    seen_names: set[str] = set()
    for entry in raw_searches:
        name = entry.get("name")
        url = entry.get("url")
        if not name or not url:
            raise ConfigError(
                f"Each search entry needs a 'name' and a 'url'. Offending entry: {entry}"
            )
        if name in seen_names:
            raise ConfigError(f"Duplicate search name '{name}' in {path}")
        seen_names.add(name)
        targets.append(SearchTarget(name=name, url=url))

    return targets
