"""Scraper for CROUS ("Trouver un logement") search result pages.

Why HTML scraping instead of a JSON API
----------------------------------------
The search results on ``trouverunlogement.lescrous.fr`` are rendered
server-side (there is no public, documented JSON API backing the search
tool), so the listings are already present in the initial HTML response.
That means a plain ``requests.get`` is enough - no browser automation
(Playwright/Selenium) is required, which makes the monitor faster, lighter,
and far more reliable to run every 10 minutes on GitHub Actions.

Parsing strategy
-----------------
Rather than hard-coding brittle CSS selectors tied to exact class names
(which CROUS could rename at any time), each listing "card" is located by
looking for its accommodation link (``/tools/<id>/accommodations/<id>``)
and then walking up to the smallest enclosing container that also contains
a price. Every other field (surface, address/city, availability note) is
then extracted from that container's text using tolerant regular
expressions. This is more resilient to markup/CSS changes than a rigid
selector chain, at the cost of being a little more heuristic - see the
tests in ``tests/test_scraper.py`` for the guarantees this makes.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

import requests
from bs4 import BeautifulSoup, Tag

from .models import Listing

logger = logging.getLogger(__name__)

ACCOMMODATION_URL_RE = re.compile(r"/tools/(?P<tool_id>\d+)/accommodations/(?P<acc_id>\d+)")
PRICE_RE = re.compile(r"(\d[\d\s]*(?:[.,]\d+)?)\s*€")
SURFACE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)(?:\s*(?:à|-)\s*(\d+(?:[.,]\d+)?))?\s*m²", re.IGNORECASE
)
POSTAL_CITY_RE = re.compile(
    r"\b(\d{5})\b\s+([A-Za-zÀ-ÖØ-öø-ÿ'’\-.]+(?:\s[A-Za-zÀ-ÖØ-öø-ÿ'’\-.]+){0,3})"
)
# Words marking the start of "badge" text (availability notes, demand
# level, ...) rather than part of a city name - used to trim over-greedy
# matches of POSTAL_CITY_RE, since that regex has no way to know where the
# address ends and free-form text begins.
_CITY_STOPWORDS = {
    "dernieres", "dernières", "derniere", "dernière",
    "logement", "logements",
    "tres", "très",
    "demande", "demandé", "demandee", "demandée",
    "disponible", "disponibles",
    "place", "places",
    "voir", "depuis", "cedex",
}
TOTAL_PAGES_RE = re.compile(r"page\s+\d+\s+sur\s+(\d+)", re.IGNORECASE)
AVAILABILITY_HINT_RE = re.compile(
    r"(disponible[^.]{0,60}|à partir du\s+\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})",
    re.IGNORECASE,
)


class ScraperError(RuntimeError):
    """Raised when a search page cannot be fetched/parsed after retries."""


@dataclass
class ScraperSettings:
    timeout_seconds: float = 20.0
    max_retries: int = 5
    retry_backoff_base_seconds: float = 2.0
    request_delay_seconds: float = 1.0
    max_pages: int = 20
    user_agent: str = "CrousHousingMonitor/1.0"


def _set_query_param(url: str, key: str, value: str) -> str:
    """Return ``url`` with query parameter ``key`` set to ``value``."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query[key] = [value]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def fetch_with_retries(
    url: str,
    settings: ScraperSettings,
    session: requests.Session | None = None,
) -> str:
    """Fetch ``url`` returning the response text, retrying with exponential backoff.

    Raises :class:`ScraperError` if every attempt fails.
    """
    session = session or requests.Session()
    headers = {"User-Agent": settings.user_agent, "Accept-Language": "fr-FR,fr;q=0.9"}

    last_error: Exception | None = None
    for attempt in range(1, settings.max_retries + 1):
        try:
            response = session.get(url, headers=headers, timeout=settings.timeout_seconds)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:  # network error, timeout, 4xx/5xx
            last_error = exc
            wait = settings.retry_backoff_base_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Request to %s failed (attempt %d/%d): %s. Retrying in %.1fs",
                url,
                attempt,
                settings.max_retries,
                exc,
                wait,
            )
            if attempt < settings.max_retries:
                time.sleep(wait)

    raise ScraperError(
        f"Failed to fetch {url} after {settings.max_retries} attempts: {last_error}"
    ) from last_error


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _find_card_container(link: Tag) -> Tag:
    """Walk up from an accommodation ``<a>`` tag to its listing card container.

    We climb until we find an ancestor whose text contains a price (``€``),
    which reliably marks the boundary of a single listing card. Falls back
    to a fixed number of parent levels if no price is found nearby (this
    still lets us extract at least the title/URL).
    """
    node = link
    for _ in range(6):
        if node.parent is None:
            break
        node = node.parent
        if "€" in node.get_text():
            return node
    return link.parent or link


def _extract_surface(text: str) -> str:
    match = SURFACE_RE.search(text)
    if not match:
        return ""
    low, high = match.group(1), match.group(2)
    if high:
        return f"de {low} à {high} m²"
    return f"{low} m²"


def _extract_price(text: str) -> str:
    match = PRICE_RE.search(text)
    if not match:
        return ""
    return f"{match.group(1).strip()} €"


def _extract_city(text: str) -> str:
    match = POSTAL_CITY_RE.search(text)
    if not match:
        return ""

    postal_code = match.group(1)
    words = match.group(2).strip().split()
    kept_words: list[str] = []
    for word in words:
        normalized = word.lower().strip(".,'’-")
        if normalized in _CITY_STOPWORDS:
            break
        kept_words.append(word)

    if not kept_words:
        return postal_code
    return f"{postal_code} {' '.join(kept_words)}"


def _extract_available_date(text: str) -> str:
    match = AVAILABILITY_HINT_RE.search(text)
    if match:
        return _clean(match.group(0))
    return "Non précisée (voir l'annonce)"


def parse_search_page(html: str, base_url: str) -> list[Listing]:
    """Parse a single search results page into a list of :class:`Listing`."""
    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []
    seen_on_page: set[str] = set()

    for link in soup.find_all("a", href=ACCOMMODATION_URL_RE):
        href = link["href"]
        match = ACCOMMODATION_URL_RE.search(href)
        if not match:
            continue

        tool_id, acc_id = match.group("tool_id"), match.group("acc_id")
        listing_id = f"{tool_id}:{acc_id}"
        if listing_id in seen_on_page:
            # The accommodation photo and the title are often both wrapped
            # in separate <a> tags pointing at the same URL - keep the first.
            continue

        title = _clean(link.get_text())
        if not title:
            # This <a> is likely the photo link (no text) rather than the
            # title link; skip it, the title link for the same id will
            # still be found elsewhere in the loop.
            continue

        container = _find_card_container(link)
        container_text = _clean(container.get_text(" "))

        full_url = href if href.startswith("http") else f"https://trouverunlogement.lescrous.fr{href}"

        listings.append(
            Listing(
                listing_id=listing_id,
                title=title,
                residence=title,
                city=_extract_city(container_text),
                rent=_extract_price(container_text),
                surface=_extract_surface(container_text),
                available_date=_extract_available_date(container_text),
                url=full_url,
            )
        )
        seen_on_page.add(listing_id)

    return listings


def _detect_total_pages(html: str) -> int:
    match = TOTAL_PAGES_RE.search(html)
    if match:
        try:
            return max(1, int(match.group(1)))
        except ValueError:
            return 1
    return 1


def scrape_search(
    url: str,
    settings: ScraperSettings | None = None,
    session: requests.Session | None = None,
) -> list[Listing]:
    """Scrape every page of a CROUS search URL and return all listings found."""
    settings = settings or ScraperSettings()
    session = session or requests.Session()

    first_page_html = fetch_with_retries(url, settings, session=session)
    all_listings = parse_search_page(first_page_html, url)

    total_pages = min(_detect_total_pages(first_page_html), settings.max_pages)
    logger.info("Search page reports %d total page(s)", total_pages)

    for page in range(2, total_pages + 1):
        time.sleep(settings.request_delay_seconds)
        page_url = _set_query_param(url, "page", str(page))
        html = fetch_with_retries(page_url, settings, session=session)
        all_listings.extend(parse_search_page(html, page_url))

    # De-duplicate in case pagination overlaps.
    dedup: dict[str, Listing] = {listing.listing_id: listing for listing in all_listings}
    return list(dedup.values())
