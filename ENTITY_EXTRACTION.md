# Entity extraction sidecars

Extract named people, organizations (including committees, parties and media),
and places from disclaimer-bearing emails with an identified committee. Original
archive records are not modified. This is local enrichment, not a site-build step.

## Run

Ollama must be running with `qwen3.8:latest` installed. The native `/api/chat`
request always specifies `think: false`, `stream: false`, a JSON schema, and
`temperature: 0`. There is no DSPy dependency, cloud fallback, automatic model
pull, or thinking-enabled retry. The installed model digest is recorded because
`:latest` may change. The model name is configurable with `--model`.

```bash
# Process up to 100 new eligible emails, newest archive day first:
uv run python scripts/extract_entities.py

# Bounded date-range run:
uv run python scripts/extract_entities.py \
  --since 2026-08-01 --until 2026-08-31 --limit 100

# Continue: completed, unchanged records are skipped automatically:
uv run python scripts/extract_entities.py --limit 100

# Explicitly retry previously failed records (within the same overall limit):
uv run python scripts/extract_entities.py --retry-failed --limit 10
```

Use `--workers N` to control concurrent Ollama requests (default **4**);
`--workers 1` runs sequentially. Requests are bounded by both the worker count
and the remaining record limit. Actual model throughput depends on the Ollama
server's parallel-request capacity and available memory. Sidecar writes remain
serialized on the coordinator thread, with each result checkpointed as it finishes.

Other options: `--api-base` (default `http://localhost:11434`), `--timeout`
(default 180 seconds per generation), `--data-dir`, `--output-dir`, and
`--registry`. `--limit` counts attempted new/stale/retried records, not cached
records, so rerunning a limit of three processes the next three eligible emails.
There is no automatic full-archive backfill or scheduled run.

## Sidecar structure

Monthly files are written to `metadata/entities/YYYY/MM.json`, with a
`schema_version` and `records` object keyed by the source email's `unique_id`.
They are git-ignored because they contain campaign text and model responses.
Each record contains:

| Field | Meaning |
| --- | --- |
| `email_id`, `date` | Join to source email and time series |
| `committee`, `committee_id`, `committee_canonical`, `committee_source` | Sending committee and existing identity provenance |
| `source_file`, `source_hash` | Source location and relevant source-field fingerprint |
| `input_fields` | Exact subject and campaign body supplied to the model |
| `provenance` | Extractor version, model tag/digest, `think: false`, registry hash, request hash |
| `processing_status` | `complete` or `failed` |
| `entities` | Validated entity records; empty array means none found; null means failed |
| `raw_response` | Model's JSON text when available, including responses rejected by validation |
| `error` | Failure reason, when applicable |
| `started_at`, `processed_at`, `usage` | Timing and available Ollama token counts |
| `review_status` | `unreviewed`; set to `human` to preserve a reviewed record |

A completed entity looks like:

```json
{
  "entity_id": "person:donald_trump",
  "surface_key": "person:donald_trump",
  "name_as_written": "President Trump",
  "canonical_name": "Donald Trump",
  "type": "person",
  "resolution_status": "registry",
  "mentions": [
    {"field": "subject", "text": "President Trump", "start": 0, "end": 15}
  ]
}
```

Offsets are zero-based Python character indexes with exclusive ends, into the
stored `input_fields` value—not HTML, bytes, or the original untrimmed body.
The validator locates every exact occurrence of each model-supplied field/name
pair and deduplicates identical spans. Invented names, unsupported fields/types,
partial-word matches, and invalid JSON fail the record instead of becoming data.
A valid empty list is distinct from failure. Evidence validation establishes that
the names occur, not that the model detected every entity or assigned every type
correctly; human review is still needed for accuracy evaluation.

## Identity resolution

`config/entity_registry.json` holds version-controlled canonical IDs, types,
names and exact aliases. It starts with Donald Trump's full-name and presidential
title variants from the existing tracked-person configuration. Bare `Trump` is
intentionally excluded because the surname alone is ambiguous. Lookup is
case-insensitive and type-specific; conflicting aliases fail before extraction.

Unmatched names receive `entity_id: null`, `canonical_name: null`, and
`resolution_status: unresolved`. Their `surface_key` is a deterministic hash of
type and case-folded written name. That key groups text labels, **not verified
identities**: two people with the same name can share it. Do not treat it as a
canonical person ID. Add reviewed aliases to resolve names on a later run.
Model-proposed aliases never modify the registry automatically.

The existing committee registry continues to describe email senders. This first
version does not automatically merge extracted organizations with that registry,
or resolve candidate–committee relationships. It also does not infer endorsements,
beneficiaries, sentiment, or donation allocations. Donation-page metadata can be
joined separately through the same email ID.

## Text and processing policy

Eligibility is `disclaimer == true` plus a nonempty normalized committee; a
resolved FEC ID is not required. This does not itself establish that an email
solicits money. Extraction uses the same subject/campaign-copy policy as the
existing mention tracker: `clean_body` with `body` fallback, truncated at the
first recognized disclaimer/footer marker. A named signatory in campaign copy
can be extracted; named disclaimer sponsors should not count solely from legal
text. Existing cleaning can omit useful material or miss unusual boilerplate.

Inputs above 24,000 characters are recorded as failures rather than silently
truncated. The request uses a 32,768-token context and an 8,192-token output cap;
incomplete or output-limited responses fail validation. Inputs that need chunking
remain a future improvement.

Each result is checkpointed through an atomic file replacement. An exclusive
output-directory lock prevents two extractors overwriting the same monthly file.
Successful unchanged records are skipped. Changed source fields, model digest,
request/prompt/options, extractor version, or registry content trigger reprocessing.
Failed unchanged records require `--retry-failed`. Human-reviewed records are
preserved; `human_stale` in the report flags reviewed records whose inputs or
configuration changed. Do not edit sidecars concurrently with extraction.

Unreviewed records found to have become ineligible are removed when their source
file is visited. This is not a full reconciliation job: deleted emails and files
outside the visited date range are not automatically removed. Downstream analysis
should join to the currently eligible archive population and require fresh,
completed results. Pending records have no sidecar row. Report incomplete coverage
separately from mention rates; deduplicate by email ID per entity rather than
counting repeated name occurrences as separate emails.

`metadata/entities/report.json` describes **the most recent invocation**, including
attempted/completed/failed records, extracted/resolved entity counts and cache
skips. It is not an all-time coverage report. Exit status is nonzero when that
invocation attempts any failed extractions; cached failures remain visible in the
report even when they are not retried.
