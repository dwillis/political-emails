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
- `--workers N` — concurrent LLM workers per day (also set `OLLAMA_NUM_PARALLEL`)
- `--limit N` — cap records processed, useful for a smoke test
- `--allow-thinking` — keep model reasoning on (for non-thinking instruct models)
- `--dry-run` — identify committees but don't write files

Requires the `enrich` dependency group (`uv sync --group enrich`) and a running
Ollama with the model pulled (`ollama pull qwen3:4b`). Each day file is
rewritten as it finishes, so an interrupted run resumes cleanly. Unknown results
are stored as `null` (indistinguishable from "not yet processed"), so re-running
a month retries any records still unresolved.

**Model choice matters a lot.** Only ~25% of records reach the LLM fallback, but
those calls dominate runtime. Use a *thinking-capable* model (default `qwen3:4b`);
the script disables reasoning via Ollama's `think: false`, which cuts a call from
minutes to well under a second. A reasoning model left in "thinking" mode is
~200x slower and tends to emit rambling non-answers. Avoid community MLX
conversions that ignore the thinking toggle.

To scrub garbage committee values (rambling model essays, adapter-marker
leakage) that predate the stricter `normalize_committee`, run the cleanup — it
re-normalizes every record and nulls rejects so they can be re-enriched:

```bash
uv run python scripts/clean_committees.py --dry-run   # preview
uv run python scripts/clean_committees.py             # apply
```

### Committee provenance & validation

Every record carries `committee_source` recording how its committee was derived:

| value | meaning |
|-------|---------|
| `human` | set by a person during review (most authoritative) |
| `disclaimer` | parsed from the email's "Paid for by ..." text (authoritative) |
| `llm:<model>` | produced by the LLM fallback during enrichment |
| `backfill` | pre-existing label whose disclaimer doesn't confirm it |
| `null` | committee is null (not determined) |

The deterministic extractor lives in
[`scripts/committee_extract.py`](scripts/committee_extract.py) (pure, no deps).
Because a disclaimer names the committee by law, when one is present the
committee comes from the disclaimer text *only* — never the sender's name.

**Measuring accuracy** against the 1,000-row hand-labeled gold set
([LLM-Extraction-Challenge](https://github.com/dwillis/LLM-Extraction-Challenge)):

```bash
uv run python scripts/eval_committees.py                          # stored + regex
uv run --group enrich python scripts/eval_committees.py --model qwen3:4b
```

**One-time data sweep** — adds `committee_source`, recovers committees from
missed disclaimers, nulls garbage (idempotent; run the dry-run first):

```bash
uv run python scripts/apply_committee_fixes.py --dry-run
uv run python scripts/apply_committee_fixes.py
```

**FEC cross-reference** — matches committee names to the FEC committee master
(a confidence signal; only *exact* matches are trusted, fuzzy are review hints):

```bash
uv run python scripts/fec_match.py            # writes state/fec/fec_matches.csv
```

**Validation report** — tiers every labeled record and builds a review queue:

```bash
uv run python scripts/validate_committees.py  # state/validation/{report.md,review_queue.csv}
```

Tiers: **CONFIRMED** (disclaimer-sourced or exact FEC match) · **CONSISTENT**
(matches the dominant committee on a candidate-owned domain) · **SUSPECT**
(review queue — the sharpest signal is *contradicts-disclaimer*: the stored
label disagrees with what the "Paid for by" text says) · **UNVERIFIED** (labeled
but unconfirmable — an honest "don't know", not an error claim).

**Reviewing SUSPECT records** — generate a self-contained HTML page (no server;
open it in a browser) that shows each record's full body with the disclaimer
highlighted and stored-vs-disclaimer side by side. Review with the keyboard
(keep / use disclaimer / correct / skip); decisions persist in the browser and
export to a CSV, which `apply_corrections.py` writes back as `committee_source=human`:

```bash
uv run python scripts/build_review_site.py --reason contradicts-disclaimer
# open state/validation/review.html, review, click "Export decisions CSV"
uv run python scripts/apply_corrections.py committee_decisions.csv --dry-run
uv run python scripts/apply_corrections.py committee_decisions.csv
```

### Party derivation

`party` (D/R/I/G or null) is derived with committee-grounded signals taking
precedence over the sender domain, since the committee is disclaimer-grounded.
`party_source` records which signal won:

| value | meaning |
|-------|---------|
| `human` | set by a person during review (most authoritative) |
| `override` | curated [`config/committee_party_overrides.csv`](config/committee_party_overrides.csv) (`NONE` blocks derivation) |
| `fec` | FEC committee master, preferring the linked candidate's registered party across cycles |
| `fec-candidate` | matched a person named in the committee/sender to the FEC candidate master (full name, or nickname via first-initial) |
| `committee-name` | committee-name keyword (only for disclaimer/human/FEC-matched committees) |
| `committee-majority` | dominant party of the committee's other emails (≥20 records, ≥95%) |
| `domain-map` | [`config/domain_party_mapping.csv`](config/domain_party_mapping.csv) |
| `platform` | ActBlue/NGP VAN (D) vs WinRed (R) link counts |
| `null` | party is null |

Precedence: `human > override > fec > committee-name > committee-majority >
fec-candidate > domain-map > platform`. `fec-candidate` and `domain-map` fill
only null records (they never overwrite a stronger committee-derived label).
Media/newsletter senders (Trump Train News, BizPac Review, …) stay null —
`party` means the sender committee's affiliation, not a partisan lean. New
emails get a provisional domain/platform party at collection; committee- and
candidate-derived party is applied at enrichment and by the sweep.

**Hand-curating the remainder.** After the sweep, the leftover party-null
records are mostly media orgs (legitimately null) plus state-level candidates
and blank-party JFCs the FEC files can't resolve. Generate a review CSV:

```bash
uv run python scripts/suggest_party_overrides.py   # -> state/validation/party_suggestions.csv
```

Paste accepted committee rows into
[`config/committee_party_overrides.csv`](config/committee_party_overrides.csv)
(`party,NONE` blocks a committee) and domain rows into
[`config/domain_party_mapping.csv`](config/domain_party_mapping.csv) — the domain
map is applied **retroactively** by the sweep — then rerun `apply_party_fixes.py`.

**One-time sweep** — derives party from committees, fills nulls, corrects
contradictions (rented-domain contamination), adds `party_source`; idempotent:

```bash
uv run python scripts/apply_party_fixes.py --dry-run
uv run python scripts/apply_party_fixes.py
```

Needs the FEC cache (`scripts/fec_match.py --download`, which now also fetches
the candidate master `cn*.zip`). Score against the gold set's party column via
`scripts/eval_committees.py`. Note: I and G still render as "unknown" in
`build_site.py` party buckets — a known follow-up.

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
| `committee_source` | string/null | How `committee` was derived: `disclaimer`, `llm:<model>`, `backfill`, or `null` |
| `party_source` | string/null | How `party` was derived (see Party derivation below) |

## Automation

GitHub Actions runs daily:
1. **Collect** (10am UTC): Fetches new emails via IMAP, commits to `data/`
2. **Deploy** (11:30am UTC): Builds the static site and deploys to GitHub Pages

## License

MIT
