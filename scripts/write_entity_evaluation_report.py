"""Write the evaluation summary from saved results and reference scores."""
import argparse
from collections import Counter
import json
from pathlib import Path


def percent(value):
    return 'not available' if value is None else f'{value:.1%}'


def write_report(w):
    w=w.resolve()
    report=json.loads((w/'report.json').read_text())
    scores=json.loads((w/'reference-scores.json').read_text())
    manifest=json.loads((w/'manifest.json').read_text())
    rows=json.loads((w/'results.json').read_text())['records']
    baseline=json.loads((w/'baseline.json').read_text())['records']
    current=scores['current'];old=scores['baseline_on_available_reference']
    ordinary=current['ordinary_paired'];prior=old['ordinary_paired']
    failed_before=[uid for uid,r in baseline.items() if r['processing_status']=='failed']
    recovered=sum(rows.get(uid,{}).get('processing_status')=='complete' for uid in failed_before)
    empty_before=[uid for uid,r in baseline.items() if r['processing_status']=='complete' and not r['entities']]
    restored=sum(any(e.get('campaign_mention') for e in rows.get(uid,{}).get('entities') or []) for uid in empty_before)
    overall=current['overall'];parts=[]
    missed_labels=Counter(alias for d in current['details'] if d['status']=='complete' for entity in d['missed'] for alias in entity['aliases'][:1])
    frequent_misses=', '.join(f'{name} ({count} emails)' for name,count in missed_labels.most_common(8))
    failure_reasons=Counter(r.get('error','Unknown failure') for r in rows.values() if r['processing_status']=='failed')
    current_digests={r['provenance']['model_digest'] for r in rows.values()}
    baseline_digests={r['provenance']['model_digest'] for r in baseline.values()}
    model_note='The same local model digest is used.' if len(current_digests)==1 and current_digests==baseline_digests else 'Model digests differ; the comparison also includes a model change.'
    dates=sorted(r['date'][:10] for r in rows.values())
    registry_count=len(json.loads((w/'registry-used.json').read_text())['entities']) if (w/'registry-used.json').exists() else 'the reviewed'
    checks=json.loads((w/'review-checks.json').read_text()) if (w/'review-checks.json').exists() else {}
    visual_note='The HTML file has not been visually verified.'
    if checks.get('browser_preview')=='blocked_by_auto_review':
        visual_note='The HTML file was checked structurally and has no external resources. Its visual browser preview was not verified: automatic approval review blocked the browser connector from accessing the local page containing email data.'
    parts.append(f'''# Entity extraction evaluation — version 2

## Outcome

Processed **{report['records']} of {report['selected']} selected emails**, with **{report['complete']} complete** and **{report['failed']} failed**. {report['not_processed']} remain unprocessed.

The paired comparison supports continuing a bounded discovery workflow. It does not establish complete archive-wide counts: missed entities, unresolved identities, context ambiguity, and some damaged source text remain.

## Scope and method

- {manifest['size']}-email diagnostic sample covering {dates[0]} through {dates[-1]}, with {len({x['committee'] for x in manifest['records']})} committee labels.
- Parties as recorded in source metadata: {', '.join(f'{k}: {v}' for k,v in sorted(Counter(x['party'] for x in manifest['records']).items()))}.
- Balanced sampling uses month, party and URL-based template proxies, plus all original failures/empty results and 14 ordinary pilot examples. This is not a representative population sample.
- The reference contains {overall['emails']} source-reviewed emails and {overall['expected_entities']} expected distinct entity groups: {current['challenge']['emails']} known challenges, {ordinary['emails']} ordinary baseline emails, and {current['held_out']['emails']} examples outside the original pilot.
- {scores['reference_limitations']}
- Both versions are scored against the version-2 campaign/signature policy. Some gains reflect the clearer policy and text preparation, not necessarily a change in model capability. {model_note}
- Scores count distinct entity groups per email. False inclusions are unmatched predicted entries; missed entities include names in failed records. Duplicate alias entries and overmerges are reported separately. Strict name boundaries can penalize some differences in geographic specificity.

## Ordinary paired emails: before and after

These are the {ordinary['emails']} ordinary successful original emails, separate from the deliberately selected problem cases.

| Measure | Original | Version 2 |
|---|---:|---:|
| Reference entity groups | {prior['expected_entities']} | {ordinary['expected_entities']} |
| Groups found | {prior['matched_entities']} | {ordinary['matched_entities']} |
| False inclusions | {prior['false_inclusions']} | {ordinary['false_inclusions']} |
| Missed groups | {prior['missed_entities']} | {ordinary['missed_entities']} |
| Extra entries from ungrouped aliases | {prior['extra_alias_entries']} | {ordinary['extra_alias_entries']} |
| Overmerged entries | {prior['overmerged_entries']} | {ordinary['overmerged_entries']} |
| Precision after deduplicating alias entries | {percent(prior['precision_deduplicated'])} | {percent(ordinary['precision_deduplicated'])} |
| Recall, including failed emails | {percent(prior['recall_including_failed_emails'])} | {percent(ordinary['recall_including_failed_emails'])} |

## Original problem cases

- {recovered}/{len(failed_before)} originally failed records now pass validation.
- {restored}/{len(empty_before)} originally empty records now contain campaign/signature entities.
- An originally empty record with no readable substantive text should remain empty for campaign analysis, even if its legal footer has named entities.

## All source-reviewed emails

| Subset | Emails | Expected groups | Found | False inclusions | Missed | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|---:|''')
    for label,key in [('All reviewed','overall'),('Original challenges','challenge'),('Ordinary baseline','ordinary_paired'),('Outside the original pilot','held_out')]:
        s=current[key];parts.append(f"| {label} | {s['emails']} | {s['expected_entities']} | {s['matched_entities']} | {s['false_inclusions']} | {s['missed_entities']} | {percent(s['precision_deduplicated'])} | {percent(s['recall_including_failed_emails'])} |")
    parts.append('''
### Results by type across the reviewed reference

| Type | Expected | Found | False inclusions | Missed | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|''')
    for kind,s in overall['by_type'].items():parts.append(f"| {kind} | {s['expected']} | {s['matched']} | {s['false_inclusions']} | {s['missed']} | {percent(s['precision'])} | {percent(s['recall'])} |")
    parts.append(f'''
## Structural checks across the full sample

- {report['bad_spans']} stored mention spans fail the exact-text check.
- {report['entities']} retained email–entity entries, of which {report['resolved']} have curated canonical IDs. These are not counts of unique real-world entities.
- {report['campaign_entities']} entries have campaign/signature mentions; {report['campaign_empty']} completed emails have none.
- {report['context_conflict_spans']} spans have conflicting proposed contexts retained for review.
- {len(report['cross_email_type_conflicts'])} written labels have more than one type among campaign/signature entries. See `report.json` for the labels; a conflict can reflect either an error or a legitimately different use.

## Implemented changes

1. Entity-specific text preparation retains linked wording and paragraphs, preserves copy after introductory notices, and records narrow signature/title boundary repairs.
2. Mentions distinguish campaign, signature, payment, legal and recipient contexts. Recipient references and known out-of-scope labels are excluded with evidence retained.
3. Display-name, field and whitespace-only repairs reuse exact source evidence. Unsupported proper names still fail. Raw responses and repair records are preserved.
4. A separate resolver uses {registry_count} curated identities and conservative within-email aliases. Registry-only changes do not trigger new inference. Ambiguous surnames stay unresolved; human-reviewed records are preserved.
5. The original 113-email metadata remains unchanged. A separately re-resolved copy is in `resolved-original/`; new evaluation results are in `results.json`.

## Remaining limitations and recommended use

Recurring missed labels in completed, source-reviewed emails: {frequent_misses or 'none'}. These counts describe this diagnostic sample only. The full per-email differences are in `reference-scores.json`.

Remaining processing failures: {'; '.join(f'{reason}: {count}' for reason,count in failure_reasons.items()) or 'none'}. A longer retry cannot guarantee recovery, especially when a response reaches the output limit. The local run and retries took substantial wall-clock time; a large backfill needs a throughput check as well as an accuracy check.

Use this for discovery, finding examples, and proposing additions to curated tracking lists. For published comparisons, resolve identities, inspect context-review flags, deduplicate by email ID, and report extraction coverage alongside the mention denominator. Do not count a failure as an absence, treat an unresolved surface key as a verified identity, or infer endorsements/beneficiaries from co-occurrence.

The most useful next investment is a broader reviewed registry and better coverage of missing names, checked against independent human annotations. A further full-archive backfill should remain a separate decision after reviewing these results and the local processing cost.

## Files

- [review.html]({w/'review.html'}): searchable source-and-result comparison, with reference differences and repairs.
- [manifest.json]({w/'manifest.json'}): reproducible selection and source references.
- [reference.json]({w/'reference.json'}): source-reviewed annotations, scope and ambiguity notes.
- [reference-scores.json]({w/'reference-scores.json'}): per-email misses, false inclusions and grouping errors.
- [results.json]({w/'results.json'}): complete new sidecars, raw responses and provenance.
- [registry-candidates.json]({w/'registry-candidates.json'}): unresolved-name suggestions; no automatic registry additions.
- [report.json]({w/'report.json'}): structural checks and coverage details.

{visual_note}
''')
    (w/'ASSESSMENT.md').write_text('\n'.join(parts)+'\n')
    print(w/'ASSESSMENT.md')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work-dir',type=Path,default=Path('state/validation/entities-v2'));write_report(p.parse_args().work_dir)
