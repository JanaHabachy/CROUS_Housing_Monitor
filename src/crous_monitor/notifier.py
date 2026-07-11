"""Email notifications (new listings, heartbeat, and error alerts).

Credentials are only ever read from :class:`~crous_monitor.config.EmailConfig`
(itself populated from environment variables / GitHub Secrets) and are never
logged. Log statements in this module intentionally avoid printing the SMTP
password, username, or full email body content that could contain secrets.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from .config import EmailConfig
from .models import Listing, SearchTarget

logger = logging.getLogger(__name__)


@dataclass
class NewListingsBySearch:
    target: SearchTarget
    listings: list[Listing]


def _send(email_config: EmailConfig, subject: str, text_body: str, html_body: str) -> None:
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = email_config.email_from
    message["To"] = email_config.email_to

    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    logger.info("Connecting to SMTP server %s:%s", email_config.smtp_server, email_config.smtp_port)
    with smtplib.SMTP(email_config.smtp_server, email_config.smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        if email_config.use_tls:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(email_config.smtp_username, email_config.smtp_password)
        smtp.sendmail(email_config.email_from, [email_config.email_to], message.as_string())

    logger.info("Email sent: %s", subject)


def _html_wrapper(title: str, body_html: str) -> str:
    return f"""\
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, Helvetica, sans-serif; background:#f4f5f7; margin:0; padding:24px;">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e2e2e2;">
    <div style="background:#1e3a5f;color:#ffffff;padding:16px 24px;">
      <h1 style="margin:0;font-size:18px;">{escape(title)}</h1>
    </div>
    <div style="padding:24px;color:#222222;font-size:14px;line-height:1.5;">
      {body_html}
    </div>
    <div style="padding:12px 24px;background:#fafafa;color:#888888;font-size:11px;border-top:1px solid #eeeeee;">
      Message automatique envoyé par le moniteur de logements CROUS.
    </div>
  </div>
</body>
</html>
"""


def _listing_html(listing: Listing) -> str:
    return f"""
    <div style="border:1px solid #e0e0e0;border-radius:6px;padding:12px 16px;margin-bottom:12px;">
      <div style="font-weight:bold;font-size:15px;color:#1e3a5f;">{escape(listing.title)}</div>
      <table style="margin-top:6px;font-size:13px;">
        <tr><td style="color:#666;padding-right:8px;">Résidence&nbsp;:</td><td>{escape(listing.residence)}</td></tr>
        <tr><td style="color:#666;padding-right:8px;">Ville&nbsp;:</td><td>{escape(listing.city or 'Non précisé')}</td></tr>
        <tr><td style="color:#666;padding-right:8px;">Loyer&nbsp;:</td><td>{escape(listing.rent or 'Non précisé')}</td></tr>
        <tr><td style="color:#666;padding-right:8px;">Surface&nbsp;:</td><td>{escape(listing.surface or 'Non précisée')}</td></tr>
        <tr><td style="color:#666;padding-right:8px;">Disponibilité&nbsp;:</td><td>{escape(listing.available_date)}</td></tr>
      </table>
      <div style="margin-top:8px;">
        <a href="{escape(listing.url)}" style="color:#1e6fd9;">Voir l'annonce &rarr;</a>
      </div>
    </div>
    """


def _listing_text(listing: Listing) -> str:
    return (
        f"- {listing.title}\n"
        f"  Résidence      : {listing.residence}\n"
        f"  Ville          : {listing.city or 'Non précisé'}\n"
        f"  Loyer          : {listing.rent or 'Non précisé'}\n"
        f"  Surface        : {listing.surface or 'Non précisée'}\n"
        f"  Disponibilité  : {listing.available_date}\n"
        f"  Lien           : {listing.url}\n"
    )


def send_new_listings_email(
    email_config: EmailConfig, groups: list[NewListingsBySearch]
) -> None:
    """Send the "[CROUS] New housing available" notification."""
    total = sum(len(group.listings) for group in groups)
    subject = "[CROUS] New housing available"

    text_parts = [f"{total} new listing(s) found.\n"]
    html_parts = [f"<p><strong>{total}</strong> new listing(s) found.</p>"]

    for group in groups:
        if not group.listings:
            continue
        text_parts.append(f"\n=== {group.target.name} ({len(group.listings)}) ===\n")
        html_parts.append(
            f'<h3 style="color:#1e3a5f;border-bottom:1px solid #eee;padding-bottom:4px;">'
            f"{escape(group.target.name)} ({len(group.listings)})</h3>"
        )
        for listing in group.listings:
            text_parts.append(_listing_text(listing))
            html_parts.append(_listing_html(listing))

    text_body = "\n".join(text_parts)
    html_body = _html_wrapper("Nouveaux logements CROUS disponibles", "".join(html_parts))

    _send(email_config, subject, text_body, html_body)


def send_heartbeat_email(
    email_config: EmailConfig,
    checked_at: datetime,
    counts_by_search: dict[str, int],
    had_new_listings_this_run: bool,
) -> None:
    """Send the "[CROUS] Monitor running" periodic heartbeat."""
    subject = "[CROUS] Monitor running"
    total = sum(counts_by_search.values())

    status_line = (
        "New listings were also found during this run (see the separate "
        "notification email)."
        if had_new_listings_this_run
        else "No new listings detected since the last check."
    )

    text_lines = [
        "The monitor is working correctly.",
        f"Last check time: {checked_at.isoformat()}",
        f"Number of listings currently visible: {total}",
        status_line,
        "",
        "Breakdown by search:",
    ]
    for name, count in counts_by_search.items():
        text_lines.append(f"  - {name}: {count} listing(s)")
    text_body = "\n".join(text_lines)

    rows_html = "".join(
        f"<tr><td style='padding:4px 12px 4px 0;color:#666;'>{escape(name)}</td>"
        f"<td style='padding:4px 0;font-weight:bold;'>{count}</td></tr>"
        for name, count in counts_by_search.items()
    )
    html_body = _html_wrapper(
        "Le moniteur CROUS fonctionne correctement",
        f"""
        <p>The monitor is working correctly.</p>
        <p><strong>Last check time:</strong> {escape(checked_at.isoformat())}</p>
        <p><strong>Number of listings currently visible:</strong> {total}</p>
        <p>{escape(status_line)}</p>
        <table style="margin-top:12px;font-size:13px;">{rows_html}</table>
        """,
    )

    _send(email_config, subject, text_body, html_body)


def send_error_email(email_config: EmailConfig, context: str, error_details: str) -> None:
    """Send an alert email when the monitor ultimately fails after retries."""
    subject = "[CROUS] Monitor error"

    text_body = (
        "The CROUS housing monitor encountered an error and could not "
        "complete successfully.\n\n"
        f"Context: {context}\n\n"
        f"Details:\n{error_details}\n"
    )
    html_body = _html_wrapper(
        "Erreur du moniteur CROUS",
        f"""
        <p>The CROUS housing monitor encountered an error and could not complete successfully.</p>
        <p><strong>Context:</strong> {escape(context)}</p>
        <pre style="background:#f7f7f7;padding:12px;border-radius:4px;white-space:pre-wrap;font-size:12px;">{escape(error_details)}</pre>
        """,
    )

    _send(email_config, subject, text_body, html_body)
