"""Orchestration: scrape every configured search, diff against state, notify."""

from __future__ import annotations

import logging
import time
import traceback
from datetime import timedelta

from . import notifier
from .config import MonitorConfig
from .models import SearchResult, SearchTarget
from .notifier import NewListingsBySearch
from .scraper import ScraperError, ScraperSettings, scrape_search
from .state import MonitorState, SearchState, load_state, save_state, utcnow

logger = logging.getLogger(__name__)


def _scraper_settings_from_config(config: MonitorConfig) -> ScraperSettings:
    return ScraperSettings(
        timeout_seconds=config.request_timeout_seconds,
        max_retries=config.max_retries,
        retry_backoff_base_seconds=config.retry_backoff_base_seconds,
        request_delay_seconds=config.request_delay_seconds,
        max_pages=config.max_pages_per_search,
        user_agent=config.user_agent,
    )


def run_search(target: SearchTarget, settings: ScraperSettings) -> SearchResult:
    """Scrape one search target, capturing any error instead of raising."""
    started = time.monotonic()
    try:
        listings = scrape_search(target.url, settings=settings)
        elapsed = time.monotonic() - started
        logger.info(
            "Search '%s': found %d listing(s) in %.1fs", target.name, len(listings), elapsed
        )
        return SearchResult(target=target, listings=listings)
    except ScraperError as exc:
        elapsed = time.monotonic() - started
        logger.error("Search '%s' failed after %.1fs: %s", target.name, elapsed, exc)
        return SearchResult(target=target, listings=[], error=str(exc))


def run_once(config: MonitorConfig) -> int:
    """Run a single monitoring pass. Returns a process exit code (0 = success)."""
    run_start = time.monotonic()
    now = utcnow()
    logger.info("=== CROUS monitor run starting at %s ===", now.isoformat())

    state = load_state(config.state_file)
    settings = _scraper_settings_from_config(config)

    results: list[SearchResult] = []
    for target in config.searches:
        results.append(run_search(target, settings))

    failed_results = [r for r in results if not r.succeeded]
    succeeded_results = [r for r in results if r.succeeded]

    new_listings_groups: list[NewListingsBySearch] = []
    counts_by_search: dict[str, int] = {}

    for result in succeeded_results:
        search_state = state.get_search(result.target.name)
        current_ids = {listing.listing_id for listing in result.listings}
        previously_seen_ids = search_state.seen_ids

        new_ids = current_ids - previously_seen_ids
        removed_ids = previously_seen_ids - current_ids

        new_listings = [l for l in result.listings if l.listing_id in new_ids]
        if new_listings:
            new_listings_groups.append(
                NewListingsBySearch(target=result.target, listings=new_listings)
            )

        logger.info(
            "Search '%s': %d total, %d new, %d removed since last check",
            result.target.name,
            len(current_ids),
            len(new_ids),
            len(removed_ids),
        )

        # Update state for this search regardless of whether new listings
        # were found, so removed listings are also reflected.
        search_state.seen_ids = current_ids
        search_state.last_listings = result.listings
        search_state.last_count = len(result.listings)
        counts_by_search[result.target.name] = len(result.listings)

    # For searches that failed this run, keep reporting their last known
    # count in the heartbeat rather than silently dropping them.
    for result in failed_results:
        existing = state.searches.get(result.target.name)
        counts_by_search[result.target.name] = existing.last_count if existing else 0

    had_new_listings = bool(new_listings_groups)

    if had_new_listings:
        total_new = sum(len(g.listings) for g in new_listings_groups)
        logger.info("Sending new-listings email for %d new listing(s)", total_new)
        try:
            notifier.send_new_listings_email(config.email, new_listings_groups)
        except Exception:  # noqa: BLE001 - we still want to persist state below
            logger.exception("Failed to send new-listings email")
    else:
        logger.info("No new listings found - no notification sent.")

    # Heartbeat: every `heartbeat_interval_hours`, regardless of whether new
    # listings were found this run (the email content reflects both cases).
    heartbeat_due = state.last_heartbeat is None or (
        now - state.last_heartbeat >= timedelta(hours=config.heartbeat_interval_hours)
    )
    if heartbeat_due:
        logger.info("Heartbeat interval elapsed - sending heartbeat email")
        try:
            notifier.send_heartbeat_email(
                config.email,
                checked_at=now,
                counts_by_search=counts_by_search,
                had_new_listings_this_run=had_new_listings,
            )
            state.last_heartbeat = now
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send heartbeat email")

    # Error email(s) for searches that ultimately failed after retries.
    for result in failed_results:
        logger.info("Sending error email for failed search '%s'", result.target.name)
        try:
            notifier.send_error_email(
                config.email,
                context=f"Scraping search '{result.target.name}' ({result.target.url})",
                error_details=result.error or "Unknown error",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send error email for '%s'", result.target.name)

    state.last_check = now
    save_state(config.state_file, state)

    elapsed_total = time.monotonic() - run_start
    logger.info("=== CROUS monitor run finished in %.1fs ===", elapsed_total)

    # Exit non-zero only if every single search failed, so the GitHub Actions
    # run is flagged red when the monitor is completely broken, while a
    # partial failure (one search down, another fine) still exits 0 since we
    # already alerted by email and successfully persisted state.
    if failed_results and len(failed_results) == len(results):
        return 1
    return 0


def run_with_top_level_error_handling(config: MonitorConfig) -> int:
    """Wrap :func:`run_once` so unexpected exceptions never fail silently."""
    try:
        return run_once(config)
    except Exception as exc:  # noqa: BLE001 - last line of defense
        logger.exception("Unhandled error during monitor run")
        try:
            notifier.send_error_email(
                config.email,
                context="Unhandled exception in monitor run",
                error_details="".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Additionally failed to send the error email")
        return 1
