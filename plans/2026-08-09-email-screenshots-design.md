# Programmatic email screenshots

**Date:** 2026-08-09
**Status:** Approved (part 2 of the datacenter effort)

## Goal

Render faithful PNG screenshots of archived emails matching a keyword (or any
regex), on demand. Keyword-agnostic: datacenter is just the first use.

## Feasibility

Confirmed. Emails always remain in the Gmail account, so screenshots re-fetch
the raw message by `Message-ID` rather than needing saved HTML. Playwright's
headless Chromium is verified working in this environment (renders modern CSS
and remote images to PNG). The archived JSONL discards raw HTML, so re-fetching
over IMAP is required — every record carries `message_id` for that.

Trackers: the user is fine with senders registering opens, so the renderer
loads images and remote assets normally (no request blocking) for maximum
fidelity.

## Design

New on-demand script `scripts/screenshot_emails.py`. Not part of daily CI.

### 1. Select targets (pure, testable)

Scan `data/**.jsonl`, keep records whose `subject + body` matches the pattern.
Pattern comes from `config/tracked_keywords.json` via `--keyword datacenter`
(shared with part 1) or an explicit `--pattern`. Filters: `--since` / `--until`
(date), `--party D|R`, `--limit N`. Returns `(message_id, unique_id, date)`
tuples.

Default `--limit 25` as a safety valve so an accidental run doesn't fetch
thousands of messages and fire thousands of trackers; `--limit 0` means all.

### 2. Fetch raw message over IMAP

Reuse `connect_imap` from `collect_emails.py`. For each `message_id`:
`mail.uid('search', None, f'HEADER Message-ID "{mid}"')` in "[Gmail]/All Mail",
then fetch `RFC822` and `email.message_from_bytes`. Needs `GMAIL_USER` /
`GMAIL_APP_PASSWORD` (same env vars as collection).

### 3. Extract HTML

Add `extract_html(message)` to `process_email.py`, reusing its existing
charset-safe decode helpers (refactored to module level). Returns the raw
`text/html` payload, or `""` if the email is plain-text only — in which case the
plain text is wrapped in a minimal HTML shell so it still renders.

### 4. Render

Playwright chromium, 600px viewport width (standard email width), full-page
PNG. `wait_until="networkidle"` so remote images finish loading.

### 5. Output

`screenshots/<keyword-or-"custom">/<date>_<unique_id>.png`. Git-ignored (PNGs
are large; the archive would bloat), same policy as generated `docs/`.

## Testing (no live IMAP required)

- `select_targets(records, pattern, since, until, party, limit)` — filtering,
  limit, and match correctness.
- `extract_html(message)` — HTML part from a multipart fixture; `""` for a
  plain-text-only fixture.
- `wrap_plaintext(text)` — produces a valid HTML shell containing the text.
- `render_html_to_png(html, path)` — real Playwright render; asserts a valid
  PNG (magic bytes) is written. Skipped if Playwright/browser is unavailable.

The IMAP fetch is a thin injectable function, exercised manually against the
live account (no committed credentials to test it in CI).

## Dependencies

Add Playwright in a dedicated `screenshots` dependency group so the daily
collect/deploy pipeline stays lightweight. Document `uv sync --group screenshots`
and `uv run playwright install chromium` in the README.

## Out of scope

Saving raw HTML at collection time (retention hedge) — unnecessary, since
messages persist in the account.
