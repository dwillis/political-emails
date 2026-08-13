# Political Email Archive

An archive of political fundraising emails, with daily updates via IMAP.

Data is stored as daily JSONL files organized by date: `data/YYYY/MM/YYYY-MM-DD.jsonl`

## Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- Gmail account with [App Password](https://support.google.com/accounts/answer/185833) enabled

### Install

```bash
uv sync
```

### Environment Variables

```bash
export GMAIL_USER="your-email@gmail.com"
export GMAIL_APP_PASSWORD="your-app-password"
```

## Scripts

### Initial Migration

Convert an existing MBOX file into daily JSONL files:

```bash
cd scripts
uv run python migrate_mbox.py --mbox-path /path/to/your.mbox
```

### Daily Collection

Fetch new emails via IMAP since the last watermark:

```bash
cd scripts
uv run python collect_emails.py
```

Options:
- `--since YYYY-MM-DD` — override the start date
- `--dry-run` — count messages without processing
- `--folder "INBOX"` — specify IMAP folder (default: `"[Gmail]/All Mail"`)

### Build Site

Generate the static index page and download archives:

```bash
cd scripts
uv run python build_site.py
```

The dashboard includes a per-day line chart of emails mentioning tracked
keywords, split by party. Keywords and their match patterns live in
[`config/tracked_keywords.json`](config/tracked_keywords.json) — add an entry to
track another term.

### Committee Enrichment

The `committee` field records which political committee sent each email. It is
populated in two ways, both run **locally** (never in GitHub Actions):

**One-time backfill** from a pre-computed export. `ijson` streams the multi-GB
JSON array and its committee assignments are joined onto the archive by
`(email, subject, date)`:

```bash
cd scripts
uv run --with ijson python backfill_committees.py --dry-run   # preview stats
uv run --with ijson python backfill_committees.py             # write changes
```

**Monthly top-up** for newly collected emails. Scans records whose `committee`
is still `null` and runs each through
[`scripts/identify_committee.py`](scripts/identify_committee.py), a DSPy module
that first parses the "Paid for by ..." disclaimer deterministically and falls
back to a local LLM (via [Ollama](https://ollama.com)) only when that fails:

```bash
uv run --group enrich python scripts/enrich_committees.py --month 2026-02
```

Options:
- `--month YYYY-MM` — month to process (default: previous calendar month)
- `--since / --until YYYY-MM-DD` — explicit date range instead of `--month`
- `--model` — Ollama model tag for the LLM fallback (default: `qwen3:4b`)
- `--ollama-url` — Ollama base URL (default: `http://localhost:11434`)
- `--limit N` — cap records processed, useful for a smoke test
- `--dry-run` — identify committees but don't write files

Requires the `enrich` dependency group (`uv sync --group enrich`) and a running
Ollama with the model pulled (`ollama pull qwen3:4b`). Each day file is
rewritten as it finishes, so an interrupted run resumes cleanly. Unknown results
are stored as `null` (indistinguishable from "not yet processed"), so re-running
a month retries any records still unresolved.

### Screenshot Emails

Render faithful PNG screenshots of archived emails matching a keyword. Because
the archive stores only cleaned text, this re-fetches the raw message from Gmail
by `Message-ID`, so `GMAIL_USER` / `GMAIL_APP_PASSWORD` must be set.

One-time setup (Playwright is an optional dependency group):

```bash
uv sync --group screenshots
uv run playwright install chromium
```

Run it:

```bash
cd scripts
uv run python screenshot_emails.py --keyword datacenter
```

Options:
- `--keyword NAME` — a keyword from `config/tracked_keywords.json`
- `--pattern REGEX` — an arbitrary case-insensitive regex instead of a keyword
- `--year YYYY` — limit to a calendar year (defaults to the current year)
- `--all-years` — do not restrict by year
- `--past-day` — only emails from the past day (yesterday and today)
- `--since YYYY-MM-DD` / `--until YYYY-MM-DD` — inclusive date range (overrides `--year`)
- `--party D|R` — filter by party
- `--disclaimer` — only emails with a campaign disclaimer
- `--limit N` — cap the number of emails (default 25; `0` means all)

By default only the current year's emails are considered.

PNGs are written to `screenshots/<keyword>/<date>_<unique_id>.png` (git-ignored).

Note: rendering loads remote images, so senders may register email "opens".

### Topic Pages

The site publishes a page per tracked keyword at `topic/<keyword>/` (e.g.
`https://thescoop.org/political-emails/topic/datacenter/`) showing up to 3
recent emails — with a campaign disclaimer — from the most recent day that has
any, as a scrolling view of screenshots with each email's date, sender, and
party. These are built once a day by the deploy workflow after the main site.

To build them locally (needs the `screenshots` setup and Gmail credentials):

```bash
cd scripts
uv run python build_topic_pages.py
```

Pages and their PNGs are written under `docs/topic/<keyword>/` (git-ignored).
The build is resilient — a Gmail or render failure logs a warning and is
skipped rather than failing the deploy.

## Data Format

Each line in a JSONL file is a JSON record with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | string | Email Message-ID header |
| `name` | string | Sender display name |
| `email` | string | Sender email address |
| `subject` | string | Email subject line |
| `domain` | string | Sender domain |
| `party` | string/null | Political party (D, R, or null) |
| `disclaimer` | boolean | Has "Paid for by" disclaimer |
| `disclaimer_text` | string | Full disclaimer text |
| `date` | string | ISO 8601 datetime |
| `year` | integer | Year |
| `month` | integer | Month (1-12) |
| `day` | integer | Day of month |
| `hour` | integer | Hour (0-23) |
| `minute` | integer | Minute (0-59) |
| `body` | string | Lightly cleaned email body |
| `clean_body` | string | Aggressively cleaned body (no HTML, no boilerplate) |
| `urls` | array | URLs found in the email body |
| `committee` | string/null | Political committee that sent the email (LLM-extracted; `null` when unknown or not yet determined) |

## Automation

GitHub Actions runs daily:
1. **Collect** (10am UTC): Fetches new emails via IMAP, commits to `data/`
2. **Deploy** (11:30am UTC): Builds the static site and deploys to GitHub Pages

## License

MIT
