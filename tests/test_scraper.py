"""Unit tests for crous_monitor.scraper (pure parsing logic, no network)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crous_monitor.scraper import (  # noqa: E402
    _detect_total_pages,
    _extract_available_date,
    _extract_city,
    _extract_price,
    _extract_surface,
    _set_query_param,
    parse_search_page,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_search_page.html"


def _load_fixture() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def test_parse_search_page_extracts_all_listings():
    html = _load_fixture()
    listings = parse_search_page(html, base_url="https://trouverunlogement.lescrous.fr/tools/47/search")

    assert len(listings) == 3
    ids = {listing.listing_id for listing in listings}
    assert ids == {"47:245", "47:257", "47:103"}


def test_parse_search_page_fields_for_single_listing():
    html = _load_fixture()
    listings = parse_search_page(html, base_url="https://trouverunlogement.lescrous.fr/tools/47/search")

    rinck = next(l for l in listings if l.listing_id == "47:245")
    assert rinck.title == "RESIDENCE GEORGES RINCK INTERGENERATIONNEL"
    assert rinck.rent == "415,1 €"
    assert rinck.surface == "de 16,5 à 19,89 m²"
    assert rinck.city == "69002 Lyon"
    assert rinck.url == "https://trouverunlogement.lescrous.fr/tools/47/accommodations/245"


def test_parse_search_page_detects_explicit_available_date():
    html = _load_fixture()
    listings = parse_search_page(html, base_url="https://trouverunlogement.lescrous.fr/tools/47/search")

    paul_bert = next(l for l in listings if l.listing_id == "47:103")
    assert "01/09/2026" in paul_bert.available_date


def test_parse_search_page_falls_back_when_no_date_hint():
    html = _load_fixture()
    listings = parse_search_page(html, base_url="https://trouverunlogement.lescrous.fr/tools/47/search")

    archimede = next(l for l in listings if l.listing_id == "47:257")
    assert archimede.available_date  # never empty
    assert isinstance(archimede.available_date, str)


def test_detect_total_pages():
    html = _load_fixture()
    assert _detect_total_pages(html) == 2


def test_detect_total_pages_defaults_to_one_when_absent():
    assert _detect_total_pages("<html><title>no pagination info here</title></html>") == 1


def test_set_query_param_adds_new_param():
    url = "https://example.com/search?locationName=Lyon"
    result = _set_query_param(url, "page", "3")
    assert "page=3" in result
    assert "locationName=Lyon" in result


def test_set_query_param_overwrites_existing_param():
    url = "https://example.com/search?page=1&locationName=Lyon"
    result = _set_query_param(url, "page", "5")
    assert "page=5" in result
    assert "page=1" not in result


def test_extract_price_variants():
    assert _extract_price("Loyer 415,1 €") == "415,1 €"
    assert _extract_price("no price here") == ""


def test_extract_surface_range_and_single():
    assert _extract_surface("de 16,5 à 19,89 m²") == "de 16,5 à 19,89 m²"
    assert _extract_surface("11 m²") == "11 m²"
    assert _extract_surface("no surface") == ""


def test_extract_city_from_postal_code_pattern():
    assert _extract_city("21 Rue Delandine 69002 Lyon") == "69002 Lyon"
    assert _extract_city("no city info") == ""


def test_extract_available_date_hint_and_fallback():
    assert "01/09/2026" in _extract_available_date("Disponible à partir du 01/09/2026")
    assert _extract_available_date("nothing relevant") == "Non précisée (voir l'annonce)"


def test_parse_search_page_deduplicates_within_page():
    html = """
    <li>
      <a href="/tools/47/accommodations/999">Photo</a>
      <div>250 &euro;</div>
      <h3><a href="/tools/47/accommodations/999">RESIDENCE TEST</a></h3>
      <p>10 m² 69000 Lyon</p>
    </li>
    """
    listings = parse_search_page(html, base_url="https://trouverunlogement.lescrous.fr/tools/47/search")
    assert len(listings) == 1
    assert listings[0].listing_id == "47:999"
