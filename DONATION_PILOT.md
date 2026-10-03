# Donation-link extraction pilot

A local pilot joins archived emails to dated donation-page snapshots and extracts
recipient evidence. Source emails are never modified. No donation is submitted.

## Run

Requires the existing Python environment. Page capture additionally requires the
Firecrawl CLI installed and authenticated (`firecrawl --status`). The pilot uses
its stored credentials; no API key is written to the repository.

```bash
uv run python scripts/donation_pilot.py select --limit 100 --per-committee 5
uv run python scripts/donation_pilot.py fetch --max-pages 10
uv run python scripts/donation_pilot.py report
```

`select` reads newest dated archive files first, requiring a disclaimer and a
normalized committee. It selects up to 100 emails with detected donation links,
with at most five per committee identity. `--since` and `--until` accept ISO dates.
The default scans backward until the sample fills: September 2026 currently has
no qualifying identified committees, so the initial sample is from August.

This is a convenience sample, not an estimate of archive-wide prevalence. Scan
counts describe only the records visited before the sample filled. Inspect
`eligible_without_detected_links` to see the detector's unresolved population;
these are not necessarily emails without donation links.

`fetch` prioritizes emails with split language in link context, then takes links
round-robin across emails. Each invocation fetches at most `--max-pages` new URLs.
Rerun to continue; `--retry-failed` allows retrying failed captures. Each result is
checkpointed atomically. Run only one writer per output directory.

```bash
# Re-extract saved evidence without network requests or extra scrape credits:
uv run python scripts/donation_pilot.py extract

# A separate sample; preserve the original manifest and snapshots:
uv run python scripts/donation_pilot.py select --since 2026-08-01 --until 2026-08-31 \
  --output-dir state/donation_pilot/august-second-sample
```

Existing manifests are never silently replaced. Fetches use fresh Firecrawl
captures (`--max-age 0`) and request markdown plus raw HTML, including footer
content. Fetching can register tracking-link clicks/page visits with senders.

## Outputs

All outputs default to git-ignored `state/donation_pilot/`. Keep them private:
original URLs and surrounding email copy can include recipient identifiers.

- `manifest.json`: email `unique_id`, date, committee provenance, source file/hash,
  full candidate URLs, link-selection reason, anchor text, and nearby copy.
- `pages.json`: exact-URL-keyed capture results, observed final URL from provider
  metadata, timestamps, content hash, snapshot path, and extracted details.
- `snapshots/*.json`: dated raw provider responses with HTML and markdown.
- `report.json`: selected emails, distinct URLs, fetch coverage, extraction yield,
  multi-recipient pages, and numeric-allocation coverage.
- `review.json`: generated extraction review queue. Copy to a separate decisions
  file before making annotations; regeneration replaces this queue.

The join is `manifest.emails[].links[].url_id` → `pages[url_id]`. Preserve full
query strings: parameters may control allocation or form variants. Only identical
URLs share a cached result; this deliberately under-deduplicates tracking URLs.
Redirect chains are currently unavailable and recorded as null. The provider's
final URL is saved when supplied, with its source recorded separately.

## What is extracted

Donation links are detected from fundraising-platform donation paths, donation
paths on other domains, and donation-like anchor text. Unsubscribe/preferences
links are excluded. Tracking links with opaque paths and no useful anchor text
can be missed. Existing cleaned email bodies cannot fully reconstruct raw HTML
link context; preserving raw links at collection is a future improvement.

The ActBlue adapter parses `window.indigoListResponse` as JSON (never executes
page JavaScript), capturing recipient names, platform entity IDs, kind, location,
and the exact embedded entity objects as evidence. The managing entity and the
platform's own legal disclaimer are not treated as recipients. Other layouts use
a conservative single-beneficiary sentence fallback or remain `needs_review`.
Platform entity IDs are not FEC IDs. Candidate/committee resolution and external
candidate solicitation classification remain unresolved in this pilot.

An explicit "split evenly between" sentence yields a default equal allocation
only when it enumerates the extracted recipients. Percentages are derived from
that explicit equal-split statement. "Customize amounts" records that the donor
can adjust the default. Multiple recipients alone never imply an equal split.
Other allocation language is retained as evidence for review, not guessed.
Dollar ladders, arbitrary percentages, caps and conditional allocations need
additional adapters and evaluation. A fetched page with no recognized recipients
is `needs_review`; a failed fetch is a separate status.

## Review and next steps

Review saved evidence alongside the originating email. Check link relevance,
complete recipient lists, defaults versus adjustable allocations, and any mismatch
with the email's stated beneficiaries. Date every conclusion by the capture:
these are September observations of August links, not verified August snapshots.

The initial 14-page batch includes 13 ActBlue pages and one WinRed page. The
WinRed page yielded an explicit single-beneficiary sentence. This does not
establish broad WinRed/Anedot coverage or detector recall. Expand with platform-stratified and
randomly selected negatives before broader backfill. Add reviewed candidate–
committee mappings, allocation adapters, and durable review overrides before
using these records to publish endorsement classifications.
