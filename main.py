#!/usr/bin/env python3
"""Entrypoint for the CROUS housing monitor.

Usage:
    python main.py

Configuration is read from environment variables (secrets - see README) and
from ``config/searches.yaml`` (the list of search pages to watch). See the
README for the full list of supported environment variables.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from crous_monitor.config import ConfigError, MonitorConfig  # noqa: E402
from crous_monitor.monitor import run_with_top_level_error_handling  # noqa: E402


class _SecretMaskingFilter(logging.Filter):
    """Best-effort filter to keep secrets out of logs.

    The monitor never logs credential values directly, but this filter is a
    defensive second layer in case a future change (or a library we depend
    on) accidentally includes one in an exception message.
    """

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        msg = record.getMessage()
        for secret in self._secrets:
            if secret in msg:
                msg = msg.replace(secret, "***MASKED***")
        record.msg = msg
        record.args = ()
        return True


def _configure_logging(secrets: list[str]) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    mask_filter = _SecretMaskingFilter(secrets)
    logging.getLogger().addFilter(mask_filter)


def main() -> int:
    try:
        config = MonitorConfig.load()
    except ConfigError as exc:
        # Logging isn't configured with secret masking yet at this point,
        # but there are no secrets to leak here: ConfigError only reports
        # which *variable name* is missing, never a value.
        logging.basicConfig(level=logging.INFO)
        logging.getLogger(__name__).error("Configuration error: %s", exc)
        return 2

    _configure_logging(
        secrets=[config.email.smtp_password, config.email.smtp_username]
    )

    return run_with_top_level_error_handling(config)


if __name__ == "__main__":
    raise SystemExit(main())
