"""Unit tests for crous_monitor.state (persistence roundtrip, no network)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crous_monitor.models import Listing  # noqa: E402
from crous_monitor.state import MonitorState, load_state, save_state, utcnow  # noqa: E402


def _sample_listing(listing_id: str = "47:1") -> Listing:
    return Listing(
        listing_id=listing_id,
        title="RESIDENCE TEST",
        residence="RESIDENCE TEST",
        city="69000 Lyon",
        rent="300 €",
        surface="12 m²",
        available_date="Non précisée (voir l'annonce)",
        url=f"https://trouverunlogement.lescrous.fr/tools/47/accommodations/{listing_id}",
    )


def test_load_state_returns_empty_state_when_file_missing(tmp_path):
    state = load_state(tmp_path / "does_not_exist.json")
    assert state.last_check is None
    assert state.last_heartbeat is None
    assert state.searches == {}


def test_save_and_load_state_roundtrip(tmp_path):
    state_path = tmp_path / "seen_listings.json"

    state = MonitorState(last_check=utcnow(), last_heartbeat=utcnow())
    search_state = state.get_search("Lyon")
    search_state.seen_ids = {"47:1", "47:2"}
    search_state.last_listings = [_sample_listing("47:1"), _sample_listing("47:2")]
    search_state.last_count = 2

    save_state(state_path, state)
    assert state_path.exists()

    reloaded = load_state(state_path)
    assert reloaded.last_check is not None
    assert reloaded.last_heartbeat is not None
    assert reloaded.get_search("Lyon").seen_ids == {"47:1", "47:2"}
    assert reloaded.get_search("Lyon").last_count == 2


def test_load_state_recovers_from_corrupted_file(tmp_path):
    state_path = tmp_path / "seen_listings.json"
    state_path.write_text("{not valid json", encoding="utf-8")

    state = load_state(state_path)
    assert state.searches == {}


def test_get_search_creates_empty_entry_on_first_access(tmp_path):
    state = MonitorState()
    search_state = state.get_search("Villeurbanne")
    assert search_state.seen_ids == set()
    assert search_state.last_count == 0
    # Accessing again returns the same object.
    assert state.get_search("Villeurbanne") is search_state
