# Entity extraction sidecars

Extract named people, organizations (including explicit party names, committees and media),
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
case-insensitive, whitespace-normalized and type-specific; conflicting aliases fail before extraction.

Unmatched names receive `entity_id: null`, `canonical_name: null`, and
`resolution_status: unresolved`. Their `surface_key` is a deterministic hash of
type and case-folded written name. That key groups text labels, **not verified
identities**: two people with the same name can share it. Do not treat it as a
canonical person ID. Add reviewed aliases and use the independent resolution pass described below.
Model-proposed aliases never modify the registry automatically.

The existing committee registry continues to describe email senders. This first
version does not automatically merge extracted organizations with that registry,
or resolve candidate–committee relationships. It also does not infer endorsements,
beneficiaries, sentiment, or donation allocations. Donation-page metadata can be
joined separately through the same email ID.

## Text and processing policy

Eligibility is `disclaimer == true` plus a nonempty normalized committee; a
resolved FEC ID is not required. This does not itself establish that an email
solicits money. Version 2 derives text from `body` with `clean_body` fallback and preserves
link labels and later copy after subscription notices. Campaign, signature,
payment, legal and recipient contexts are distinguished as described below.
The site mention tracker retains its existing policy; its historical counts
should not be assumed comparable to this new extraction.

Inputs above 24,000 characters are recorded as failures rather than silently
truncated. The request uses a 32,768-token context and an 8,192-token output cap;
incomplete or output-limited responses fail validation. Inputs that need chunking
remain a future improvement.

Each result is checkpointed through an atomic file replacement. An exclusive
output-directory lock prevents two extractors overwriting the same monthly file.
Successful unchanged records are skipped. Changed source fields, model digest, request/prompt/options or extractor
version trigger reprocessing. Registry-only changes trigger resolution without
another model call.
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

## Version 2: evidence, context, and independent resolution

The extractor now derives its own text from the archived `body`, falling back to
`clean_body` only when necessary. It preserves Markdown link labels and paragraph
breaks, strips tracking URLs, and retains copy after introductory unsubscribe
notices. It does not change the archive or the site's existing preview cleaner.
Narrow, recorded repairs insert breaks at recognizable joined signature/title
boundaries; they do not split arbitrary CamelCase names. Offsets always refer to
this derived text. `input_repairs` records the transformations.

Each mention now has a `context`:

- `campaign`: substantive message copy, including appeals naming candidates.
- `signature`: the author or campaign team signing the message.
- `payment`: payment instructions, including named split recipients.
- `legal`: disclaimers, mailing addresses, and subscription notices.
- `recipient`: a personalized reader name or placeholder; excluded from resolved
  entities but retained in the underlying extraction when supplied by the model.

Context is model-proposed with conservative, line-local rules for notices,
payment instructions, and greetings. These labels are evidence to review, not a
guarantee of meaning. Conflicting labels for the same span are retained in
`context_candidates` and flagged with `context_review_required`. If every apparent
campaign/signature span for an entity is conflicted,
`campaign_mention_review_required` is true; inspect it before using that entity in
published counts. `campaign_mention` is true when at least one retained mention
is in campaign copy or a signature. Count distinct email IDs per resolved identity
and filter by this flag for campaign analytics. Payment-only entities remain
available for separate investigation; their presence does not prove a donation
beneficiary or allocation. Footer-only entities must not contribute to campaign
mention counts.

The named-entity policy excludes generic demographic groups, political adjectives,
issues, legislation, and reader placeholders. It includes explicit party and
institution names. Ambiguous generic labels such as `Democrats`, `Republicans`,
and `MAGA` are excluded in this version; track those as a separate vocabulary if
needed. Explicit names such as `Democratic Party` or `GOP` remain eligible.

Validation still requires exact evidence and valid word boundaries. Three auditable
repairs are allowed before validation: choosing an already supplied mention as a
display name when capitalization/titles differ, and correcting a mistaken field
when the exact evidence occurs only in the other field. A unique whitespace-only variant can also recover a name wrapped across lines,
using the exact whitespace from the source. All repairs are recorded;
raw responses are preserved. Known out-of-scope labels are quarantined in
`excluded_response_entities`. Unsupported proper names or evidence still fail the
record; no failed record should be interpreted as no entities.

### Resolve existing evidence without another model call

```bash
uv run python scripts/resolve_entities.py \
  --input-dir metadata/entities \
  --output-dir state/validation/resolved-entities
```

This produces a separate copy and preserves human-reviewed rows. Resolution uses
curated exact aliases plus conservative within-email first-name/surname matching
when one full-name anchor identifies the person. Two full names sharing a surname
block that shortcut. Short names never become global aliases. `resolution_links`
records local matches. Unmatched names remain unresolved.

`extracted_entities` preserves the entries before resolution. Re-resolution starts
from that evidence, so changing or removing a registry entry can undo an earlier
match. `resolution` records its own version and registry fingerprint. The original
extraction provenance stays intact. A registry-only change no longer causes another
model call when the extractor visits a fresh completed record. Old v1 input cannot
recover missing text through resolution alone; a v2 extraction is required.

Registry IDs are local identifiers, not FEC IDs. Reviewed additions include local
source references. Resolution of a campaign organization does not establish its
relationship to a candidate or receiving committee.

### Bounded evaluation

```bash
# Select 150 eligible emails, including old failures/empty results and 14 paired
# ordinary examples; balance the rest across month, party and template proxy.
uv run python scripts/eval_entities.py select
uv run python scripts/eval_entities.py run

# Revalidate saved responses with deterministic repairs, without new inference.
uv run python scripts/eval_entities.py revalidate
uv run python scripts/score_entity_reference.py
```

The default workspace is `state/validation/entities-v2/`. It contains a manifest,
original paired results, checkpointed new results, and reports. The default local
model remains `qwen3.8:latest`, with thinking disabled and four workers. A run
resumes from its checkpoints. Use `--retry-failed` for explicit retries. `select` refuses to replace an existing manifest; use a different `--work-dir`
to create a new evaluation and preserve comparison results.

`reference.json` is an assistant-reviewed source reference, not independent human
gold. The 54-email review separates 16 known challenges, 14 ordinary paired examples,
and 24 examples from outside the original pilot. This is a diagnostic reference,
not a fully blind benchmark: some source inspection informed text repairs.
It defines distinct entity groups and accepted observed aliases in substantive
copy/signatures. The scorer reports false inclusions, missed entities (including
failed emails), duplicate alias entries, and overmerged identities separately.
These are provisional sample metrics, not population-wide accuracy estimates.
Structural checks across all 150 emails are a separate evaluation from that review.
