# Datacenter keyword flagging + daily party line chart

**Date:** 2026-08-09
**Status:** Approved (part 1 of a two-part effort; part 2 = programmatic screenshots, deferred)

## Goal

Flag emails that mention "datacenter" / "data center" and show a daily tally on
the site as a line chart split by party (D / R / unknown).

## Feasibility

Fully feasible with the existing pipeline. Every email's text is stored in the
`subject`, `body`, and `clean_body` fields of the daily JSONL files, and
`build_site.py` already re-scans the whole archive on each daily build and embeds
dependency-free inline-SVG charts. Because counts are recomputed from the full
archive every build, the entire history is available immediately and any later
tweak to the match pattern retroactively fixes the whole series.

## Design

### Matching

Case-insensitive regex `\bdata[\s-]?centers?\b`, matched against
`subject + " " + body`. Catches "data center", "datacenter", "data-center", and
their plurals. Not matching "centre" (per request). An email counts once per day
regardless of how many times the term appears.

Config-driven for reuse by part 2: `config/tracked_keywords.json` maps a keyword
slug to its pattern:

```json
{ "datacenter": "\\bdata[\\s-]?centers?\\b" }
```

### Counting

Fold the tally into the existing single pass in `compute_stats()` (no second scan).
Add `keyword_daily` to the returned stats:

```
keyword_daily = { keyword: { "YYYY-MM-DD": {"D": n, "R": n, "unknown": n} } }
```

The date comes from the JSONL filename stem (one file per day). Party bucket
follows the existing D / R / unknown convention.

### Persistence

Write `docs/keyword_daily.json` at build time so the tally is downloadable and
inspectable, not just baked into the SVG.

### Chart

Add `line_chart(dates, series, colors, title)` to `charts.py`, following the
existing generators exactly (800x400 viewBox, shared margins, `_nice_max`,
`aria-label`, no JS):

- `dates`: ordered list of "YYYY-MM-DD" strings (x-axis)
- `series`: `{label: [values aligned to dates]}` — one polyline per party
- `colors`: `{label: css_color}` — reuse `PARTY_COLORS`
- x-axis labels drawn only at month boundaries to avoid crowding ~220 points

Embed it in `generate_dashboard_html()` alongside the other three charts, under a
heading like "Datacenter mentions per day".

## Testing

- `charts.py`: line chart returns valid SVG root, includes title / aria / series
  colors, handles empty and single-point data.
- `build_site.py`: `compute_stats` tallies keyword matches per day per party;
  matching hits the intended spellings and skips non-matches (e.g. "data" alone,
  "datacentre").

## Out of scope

Part 2 (screenshots) — separate effort. The `config/tracked_keywords.json`
primitive is shared groundwork for it.
