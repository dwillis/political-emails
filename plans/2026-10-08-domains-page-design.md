# Domains → committees page (reverse of committees.html)

**Date:** 2026-10-08
**Status:** Approved

## Goal

A public page at `domains.html`: for a given sender domain, show the
committees that have used it. The default view lists the top 50 domains by
volume; search covers every committee-linked domain
(~4,200). The mirror image of `committees.html`.

## Decisions (from brainstorming)

- **Committee-identified emails only** — counts cover exactly the emails
  behind `committees.html` (committee + `committee_group_key`). Domains that
  never sent identified-committee email simply don't appear. The page states
  this, which also prevents confusion with the dashboard's "Top 10 sender
  domains" chart (all emails, committee or not).
- **Approach A — reuse existing JSONs, invert in the browser.** No new data
  files. The page fetches `committees.json` (names, parties, FEC IDs, totals)
  and `committee_domains.json` (per-committee `[domain, emails, first, last]`
  rows, aligned by index with `committees.json`), and builds the reverse index
  client-side (~29k pairs, trivial). One source of truth; the index-alignment
  invariant stays in exactly one place (the build's tracker).
- **Top 50 only, no "show all"** — search covers the rest, so the default
  table stays small.
- **All-time totals** — no period filter. `committee_domains.json` aggregates
  over the whole archive; adding monthly granularity would multiply the data
  for little value.

## Page (`build_site.py`: new template + nav edits)

Same site chrome (header, serif, footer, `SHARED_CSS`). Title "Domains —
Political Email Archive"; nav Home / All Downloads / Committees / GitHub.
`committees.html`'s nav gains a "Domains" link. Intro subtitle states the
committee-identified-only scope and links to `committee_domains.json` as the
downloadable data.

### Default view — top 50

Summary line ("N distinct sender domains used by X committees…"), then a
sortable table defaulting to emails desc: Rank, Domain, Committees (distinct
count), Emails, First seen, Last seen. Exactly 50 rows. The Committees column
is sortable — breadth highlights shared broadcast/platform domains.

### Search

One input in the tracker-controls row. As you type, a dropdown lists up to 15
case-insensitive substring matches with email counts, ranked by volume. Enter
or click selects. Selecting swaps the table for the detail view and writes
`?d=<domain>` via `history.replaceState` — every domain is deep-linkable.

### Domain detail view

"← All domains" breadcrumb back to the top 50; the domain as heading; summary
line (emails, committee count, first/last seen). Sortable committee table,
emails desc: Committee (linked to FEC when `fec_id`, as on the committees
page) | Party | Emails | % of committee's total | First seen | Last seen.
The % column, computed from `committees.json` totals, shows whether the domain
is a committee's main channel or one rented slot among many.

### Cross-link

On `committees.html`, the expanded per-committee "Domains" table's domain
cells become links to `domains.html?d=<domain>`.

### States

Unknown `?d=` → "No committee-linked emails found for <domain>" plus the back
link. JSON load failure → same `tracker-error` pattern as the other pages.

## Testing

Follow `tests/` conventions for any new build_site page tests; verify locally
with `uv run python scripts/build_site.py` and a browser pass over both pages
(top-50 sort, search, select, `?d=` deep link, committees.html link-out).

## Out of scope

Period/party filters; "no identified committee" rows; a build-time reverse
JSON (Approach B remains an easy later swap — the page's data contract
doesn't change).