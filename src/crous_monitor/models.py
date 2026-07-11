"""Data models used across the CROUS monitor."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Listing:
    """A single accommodation listing scraped from a CROUS search page.

    ``listing_id`` is the stable identifier used for new/removed detection.
    It is derived from the accommodation URL, e.g. for
    ``https://trouverunlogement.lescrous.fr/tools/47/accommodations/245``
    the listing id is ``"47:245"`` (tool id + accommodation id), which keeps
    ids unique even if two different CROUS "tools" (search campaigns) reuse
    the same accommodation number.
    """

    listing_id: str
    title: str
    residence: str
    city: str
    rent: str
    surface: str
    available_date: str
    url: str

    def as_dict(self) -> dict:
        """Return a JSON-serialisable representation of the listing."""
        return {
            "listing_id": self.listing_id,
            "title": self.title,
            "residence": self.residence,
            "city": self.city,
            "rent": self.rent,
            "surface": self.surface,
            "available_date": self.available_date,
            "url": self.url,
        }

    @staticmethod
    def from_dict(data: dict) -> "Listing":
        return Listing(
            listing_id=data["listing_id"],
            title=data.get("title", ""),
            residence=data.get("residence", ""),
            city=data.get("city", ""),
            rent=data.get("rent", ""),
            surface=data.get("surface", ""),
            available_date=data.get("available_date", ""),
            url=data.get("url", ""),
        )


@dataclass
class SearchTarget:
    """A named CROUS search page to monitor."""

    name: str
    url: str


@dataclass
class SearchResult:
    """Result of scraping a single :class:`SearchTarget`."""

    target: SearchTarget
    listings: list[Listing] = field(default_factory=list)
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None
