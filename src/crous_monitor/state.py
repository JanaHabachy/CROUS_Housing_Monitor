"""Persistence of "already seen" listings between GitHub Actions runs.

GitHub Actions runners are stateless: each run starts from a clean checkout
of the repository. To remember which listings were seen on the previous
run, this module reads/writes a small JSON file (``state/seen_listings.json``
by default) that lives *in the repository itself*. The GitHub Actions
workflow commits and pushes this file back to the repo at the end of every
run (see ``.github/workflows/monitor.yml``), which is the most transparent
and dependency-free persistence option for a low-volume, single-branch
monitor like this one (no external database, and the history of the state
file doubles as an audit log of what changed and when).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import Listing

logger = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = 1


@dataclass
class SearchState:
    seen_ids: set[str] = field(default_factory=set)
    last_listings: list[Listing] = field(default_factory=list)
    last_count: int = 0

    def to_dict(self) -> dict:
        return {
            "seen_ids": sorted(self.seen_ids),
            "last_listings": [listing.as_dict() for listing in self.last_listings],
            "last_count": self.last_count,
        }

    @staticmethod
    def from_dict(data: dict) -> "SearchState":
        return SearchState(
            seen_ids=set(data.get("seen_ids", [])),
            last_listings=[Listing.from_dict(item) for item in data.get("last_listings", [])],
            last_count=int(data.get("last_count", 0)),
        )


@dataclass
class MonitorState:
    last_check: datetime | None = None
    last_heartbeat: datetime | None = None
    searches: dict[str, SearchState] = field(default_factory=dict)

    def get_search(self, name: str) -> SearchState:
        if name not in self.searches:
            self.searches[name] = SearchState()
        return self.searches[name]

    def to_dict(self) -> dict:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "searches": {name: state.to_dict() for name, state in self.searches.items()},
        }

    @staticmethod
    def from_dict(data: dict) -> "MonitorState":
        def _parse_dt(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None

        return MonitorState(
            last_check=_parse_dt(data.get("last_check")),
            last_heartbeat=_parse_dt(data.get("last_heartbeat")),
            searches={
                name: SearchState.from_dict(value)
                for name, value in (data.get("searches") or {}).items()
            },
        )


def load_state(path: Path) -> MonitorState:
    """Load monitor state from disk, returning a fresh empty state if absent."""
    if not path.exists():
        logger.info("No existing state file at %s, starting fresh", path)
        return MonitorState()

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return MonitorState.from_dict(data)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(
            "State file %s could not be read (%s). Starting from an empty "
            "state - this may cause already-seen listings to be re-notified "
            "once.",
            path,
            exc,
        )
        return MonitorState()


def save_state(path: Path, state: MonitorState) -> None:
    """Persist ``state`` to ``path`` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(state.to_dict(), fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    tmp_path.replace(path)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
