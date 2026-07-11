# CROUS Housing Monitor

Watches CROUS student-housing search pages
(`trouverunlogement.lescrous.fr`) and emails you as soon as a **new**
accommodation listing appears. Runs automatically every 10 minutes on
GitHub Actions — no server, no database, no browser automation needed.

---

## How it works

1. **Scraping.** The search results on `trouverunlogement.lescrous.fr` are
   rendered server-side — there is no documented public JSON API — so the
   monitor fetches the page HTML directly with `requests` and parses it
   with `BeautifulSoup`. This is faster and far more reliable on a 10-minute
   schedule than driving a real browser (Playwright/Selenium), and it's
   what this repo uses. If CROUS ever moves to a client-side-rendered page,
   `src/crous_monitor/scraper.py` is the only file that would need to
   change (see `SearchState`/`SearchResult` — the rest of the pipeline is
   agnostic to how listings are fetched).
2. **Pagination.** Each search page reports "page X sur Y"; the monitor
   walks every page automatically.
3. **Detection.** Every listing is identified by a stable id built from its
   URL (`tools/<tool_id>/accommodations/<accommodation_id>`). Each run
   compares the current set of ids against the ids seen on the previous
   run.
4. **Notification.** If any id is new, one email is sent listing every new
   accommodation across all configured searches. If nothing changed, no
   email is sent.
5. **Heartbeat.** Every 48 hours (configurable), a "still alive" email is
   sent regardless, summarizing how many listings are currently visible
   per search.
6. **Persistence.** The set of previously-seen ids lives in
   `state/seen_listings.json`, committed straight back to the repository
   by the GitHub Actions workflow after every run — see "How persistence
   works" below.
7. **Resilience.** Network calls retry with exponential backoff. If a
   search ultimately can't be fetched, the monitor emails you an error
   report instead of failing silently, and still processes the other
   configured searches.

---

## Repository layout

```
.
├── main.py                          # CLI entrypoint
├── config/
│   └── searches.yaml                 # The search URLs to monitor
├── state/
│   └── seen_listings.json            # Persisted state (auto-committed)
├── src/crous_monitor/
│   ├── config.py                     # Env vars + YAML config loading
│   ├── models.py                      # Listing / SearchTarget / SearchResult
│   ├── scraper.py                     # HTTP fetch + HTML parsing
│   ├── state.py                       # Load/save state/seen_listings.json
│   ├── notifier.py                    # Email composition + sending
│   └── monitor.py                     # Orchestration (scrape → diff → notify)
├── tests/                             # Unit tests (parser logic, state I/O)
├── .github/workflows/monitor.yml      # The scheduled GitHub Action
├── requirements.txt
└── .env.example                       # Template for local testing
```

---

## Configuring GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository
secret** and add each of the following (all required):

| Secret name     | Example value                | Notes                                   |
|------------------|-------------------------------|------------------------------------------|
| `SMTP_SERVER`    | `smtp.gmail.com`              | Your SMTP provider's host                |
| `SMTP_PORT`      | `587`                         | `587` for STARTTLS, `465` for implicit TLS (set `SMTP_USE_TLS=false` if you use 465 with implicit TLS) |
| `SMTP_USERNAME`  | `you@gmail.com`               | SMTP login                               |
| `SMTP_PASSWORD`  | `xxxxxxxxxxxxxxxx`            | An **app password**, not your normal password, for providers like Gmail |
| `EMAIL_FROM`     | `you@gmail.com`               | Sender address                           |
| `EMAIL_TO`       | `jana.habachy@gmail.com`      | Recipient address                        |

No credentials are ever hardcoded in the source code — everything is read
from environment variables (see `src/crous_monitor/config.py`), and GitHub
Actions injects the secrets above as environment variables at run time
(see the `env:` block in `.github/workflows/monitor.yml`).

**Gmail users:** you must create an
[App Password](https://myaccount.google.com/apppasswords) (requires 2-Step
Verification enabled) — Gmail rejects your normal account password for
SMTP login.

---

## Enabling GitHub Actions

1. Push this repository to GitHub.
2. Add the six secrets above.
3. The workflow in `.github/workflows/monitor.yml` runs automatically on
   its `schedule` (every 10 minutes) once the repo is on GitHub — no
   further action needed. GitHub disables scheduled workflows on repos
   with no activity for 60 days; push a commit or click "Enable workflow"
   under the **Actions** tab if that happens.
4. To trigger a run immediately (e.g. to test your secrets), go to
   **Actions → CROUS Housing Monitor → Run workflow** (this uses the
   `workflow_dispatch` trigger).

---

## Testing locally

```bash
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with real SMTP credentials

set -a; source .env; set +a
python main.py
```

Run the unit tests (pure parsing/state logic, no network calls, no
credentials needed):

```bash
pip install pytest
pytest tests/ -v
```

---

## Changing the search URLs

Edit `config/searches.yaml`. Each entry needs a `name` (used as the state
key and in email subjects/bodies) and a `url` (copy it straight from your
browser's address bar after setting filters on
`trouverunlogement.lescrous.fr`). To monitor a third city, just add a third
block — no code changes required:

```yaml
searches:
  - name: "Villeurbanne"
    url: "https://trouverunlogement.lescrous.fr/tools/47/search?..."
  - name: "Lyon"
    url: "https://trouverunlogement.lescrous.fr/tools/47/search?..."
  - name: "Grenoble"
    url: "https://trouverunlogement.lescrous.fr/tools/47/search?..."
```

> **Note:** CROUS reuses a numeric "tool id" (the `/tools/<id>/` segment)
> per academic-year campaign, and it can change from one year to the next
> (e.g. `42` → `45` → `47` in past campaigns). If a search page stops
> returning results, open it in a browser and re-copy the URL — the tool id
> may have been retired.

Renaming a search resets its detection state (the next run will treat all
of its current listings as "new"), since the name doubles as the state
key.

---

## How persistence works

GitHub Actions runners are stateless — each run starts from a fresh
checkout. To remember what was already seen, `state/seen_listings.json` is
committed directly to the repository:

1. The monitor loads this file at the start of a run.
2. It updates the in-memory state after scraping.
3. `main.py` writes the file back to `state/seen_listings.json`.
4. The workflow's last step commits and pushes that file **only if it
   changed**, with `[skip ci]` in the commit message so the push doesn't
   itself re-trigger anything unexpected.

This was chosen over GitHub Actions cache or Artifacts because:
- **Cache** entries can be evicted (LRU, 7-day max) — not reliable enough
  for something you don't want to silently reset.
- **Artifacts** are per-run and awkward to feed back into the *next* run
  without extra API calls to find/download the latest one.
- A **committed state file** is simple, has zero extra dependencies, is
  trivially inspectable (`git log -- state/seen_listings.json` shows
  exactly what changed and when), and needs no external database.

If you'd rather not grow the repo's history with these commits over time,
you can periodically squash it, or move the state file to its own
lightweight branch — not done here to keep things simple.

---

## Email content

**New listings** — Subject: `[CROUS] New housing available`. Lists, for
every new accommodation: title, residence, city, rent, surface area,
available date (when CROUS publishes one; otherwise the email says so and
links to the listing), and a direct link.

**Heartbeat** — Subject: `[CROUS] Monitor running`, sent every 48 hours
(configurable via `CROUS_HEARTBEAT_INTERVAL_HOURS`). Contains the last
check time and the number of listings currently visible per search.

**Error alert** — Subject: `[CROUS] Monitor error`, sent whenever a search
cannot be fetched after all retries, or an unexpected exception occurs.
Includes the failing search and error details so you can act on it instead
of silently missing updates.

All emails are sent as both plain text and styled HTML (most email clients
will show the HTML version).

> **On the "available date" field:** CROUS's search result cards don't
> always expose an explicit move-in date — it's often just a demand badge
> like *"Dernières places disponibles"*. The monitor extracts an explicit
> date whenever CROUS publishes one on the card, and otherwise reports
> "Non précisée (voir l'annonce)" with a link to the listing, rather than
> guessing.

---

## Configurable settings

All of these are optional environment variables with sensible defaults
(set them as repository/workflow secrets or variables, or in your local
`.env`):

| Variable | Default | Purpose |
|---|---|---|
| `CROUS_HEARTBEAT_INTERVAL_HOURS` | `48` | Hours between heartbeat emails |
| `CROUS_REQUEST_TIMEOUT` | `20` | Per-request HTTP timeout (seconds) |
| `CROUS_MAX_RETRIES` | `5` | Retry attempts per page before giving up |
| `CROUS_RETRY_BACKOFF_BASE` | `2` | Base seconds for exponential backoff (2, 4, 8, 16, ...) |
| `CROUS_REQUEST_DELAY` | `1` | Delay between paginated requests (politeness) |
| `CROUS_MAX_PAGES` | `20` | Safety cap on pages scraped per search |
| `CROUS_SEARCHES_FILE` | `config/searches.yaml` | Path to the searches config |
| `CROUS_STATE_FILE` | `state/seen_listings.json` | Path to the state file |
| `SMTP_USE_TLS` | `true` | Set to `false` if your SMTP port already uses implicit TLS |

The **actual polling cadence** (every 10 minutes) is controlled by the
`cron` schedule in `.github/workflows/monitor.yml`, since that's a
property of the workflow trigger, not something the Python script can
change at runtime. Edit the `cron:` line to change it (GitHub Actions'
finest supported granularity is 5 minutes).

---

## Bonus features included

- **Removed-listings detection**: logged (see the Actions run log — "N
  removed since last check") and reflected in state, though not emailed by
  default (the request only asked for new-listing alerts). Add a call to
  a new `notifier.send_removed_listings_email` if you'd like this emailed
  too.
- **De-duplication**: a listing already present in `seen_ids` is never
  re-notified, even if it temporarily disappears and reappears across
  runs before its next state save.
- **HTML + plain-text emails.**
- **Configurable polling interval** (via the workflow cron) and
  **heartbeat interval** (via `CROUS_HEARTBEAT_INTERVAL_HOURS`).
- **Easy addition of more search URLs** — just add entries to
  `config/searches.yaml`.
- **Unit tests** for the parser (`tests/test_scraper.py`, using a saved
  HTML fixture) and for state persistence (`tests/test_state.py`).

---

## Troubleshooting

- **No emails at all, ever.** Check the Actions tab for a failed run
  first. A `ConfigError` about a missing secret means one of the six
  required secrets isn't set (or is misspelled) in **Settings → Secrets
  and variables → Actions**.
- **"Monitor error" email about authentication.** Your `SMTP_USERNAME` /
  `SMTP_PASSWORD` pair is likely wrong, or (Gmail, Outlook, etc.) you're
  using your normal password instead of an app-specific password.
- **Workflow doesn't seem to run every 10 minutes.** GitHub Actions
  schedules are best-effort; on the free tier, scheduled runs can be
  delayed, especially during periods of high platform load. This is a
  GitHub-side limitation, not a bug in the monitor.
- **Scheduled workflow stopped running entirely.** GitHub automatically
  disables scheduled workflows after 60 days of repository inactivity —
  push any commit, or re-enable it manually under the Actions tab.
- **State file conflicts / push rejected.** The workflow rebases onto the
  latest remote state before pushing (see `monitor.yml`), which handles
  the normal case of two runs finishing close together. If you manually
  edit `state/seen_listings.json` and push at the same time as a run, you
  may need to resolve a conflict once.
- **Listings look wrong / missing fields.** CROUS occasionally tweaks its
  page markup. The parser in `scraper.py` is deliberately
  markup-tolerant (it locates listing cards via their accommodation link,
  not rigid CSS selectors), but if fields start coming back empty, run
  `pytest tests/test_scraper.py -v` against a freshly-saved copy of the
  page HTML to see which extraction regex needs adjusting.
- **I want to see what a run actually scraped without waiting for a
  GitHub Actions run.** Run `python main.py` locally with your `.env`
  sourced — logs print the listing counts and new/removed counts per
  search.

---

## Security notes

- Credentials are read exclusively from environment variables — never
  hardcoded, never committed. `.env` is git-ignored.
- The logger applies a best-effort secret-masking filter
  (`main.py::_SecretMaskingFilter`) so the SMTP username/password never
  appear in Actions logs even indirectly (e.g. inside a caught exception
  message).
- The monitor only ever reads public CROUS search pages; it does not log
  in, apply to housing, or modify anything on CROUS's site.
