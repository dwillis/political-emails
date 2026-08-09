# Public topic pages (recent emails per keyword)

**Date:** 2026-08-09
**Status:** Approved

## Goal

A public page per tracked keyword at `/topic/<keyword>/` (e.g.
`https://thescoop.org/political-emails/topic/datacenter/`) showing up to 3
recent emails mentioning that keyword, as a scrolling view of screenshots with
each email's date, sender, and party. Regenerated once a day during the deploy.

## Decisions (from brainstorming)

- **Disclaimer required** — only emails with a campaign disclaimer.
- **A page per keyword** in `config/tracked_keywords.json` (config-driven, like
  the chart).
- **Never empty** — show the most recent date that has matching emails, up to 3
  from that date (labelled with its date). Since selection spans the whole
  archive, no cross-run state is needed; a quiet past day simply falls back to
  the last active day.

## Architecture

Keep the network-free site builder and the network/render code separate.

- `build_site.py` (no network): add `generate_topic_html(topic, day, emails,
  generated_iso)` — pure HTML using the existing `SHARED_CSS` / `PARTY_COLORS`
  and site chrome. Also add a small "Topics" nav on the dashboard linking to
  each `/topic/<kw>/`.
- `build_topic_pages.py` (new orchestrator; network + Playwright): for each
  keyword — select the latest day's matches, fetch each raw message from Gmail,
  render PNGs into `docs/topic/<kw>/`, then call `generate_topic_html` and write
  `docs/topic/<kw>/index.html`.
- Reuses `iter_records`, `select_targets`, `connect_imap`, `screenshot_email`
  from `screenshot_emails.py`.

### Selection (pure, testable)

`pick_latest_day_targets(records, pattern, disclaimer=True, limit=3)`:
match keyword + disclaimer across all records, find the max `YYYY-MM-DD` present,
return `(day, up-to-limit records from that day, newest first)`. Empty archive
match → `(None, [])`.

### Page layout

Vertical scroll of email cards. Each card: header with date, sender
(name + address), and a party badge coloured via `PARTY_COLORS`; below it the
screenshot image (max 600px wide) inside a capped, inner-scrolling frame so all
three are reachable. No JS. Relative links (`../../`) back to the home page.

## Daily automation

Extend `.github/workflows/deploy-site.yml` (runs 11:30 UTC, after collect):

1. `python scripts/build_site.py` (unchanged — builds main pages).
2. `uv sync --group screenshots` + `uv run playwright install chromium`.
3. `uv run python scripts/build_topic_pages.py` with `GMAIL_USER` /
   `GMAIL_APP_PASSWORD` from the existing repo secrets.
4. Upload `docs/` and deploy.

The topic step is resilient: per-keyword failures are logged and skipped, and
the script always exits 0 so a transient Gmail/render problem never fails the
site deploy. PNGs live under `docs/` (git-ignored, ephemeral, regenerated
daily), consistent with the rest of the generated site.

## Testing

- `pick_latest_day_targets` — latest-day selection, limit, disclaimer filter,
  empty archive.
- `generate_topic_html` — contains topic, each email's date/sender/party, `<img>`
  per screenshot, and a sensible empty state.

The IMAP fetch + render path is already covered by `screenshot_emails` tests.

## Out of scope

Historical per-day archives of topic pages (only the latest day is shown);
committing PNGs to the repo (kept ephemeral).
