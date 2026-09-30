"""Resolve saved entity evidence without calling a language model.

First names/surnames are linked only within an email with one unambiguous full
name anchor; unresolved surface keys never assert cross-email identity.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

VERSION = 'resolution-v2'
EXCLUDED = {'person': {'god', 'americans', 'kansans', 'washingtonians', 'south sounders', 'granite staters', 'californians', 'michiganders'},
            'organization': {'democratic', 'republican', 'democrat', 'democrats', 'republicans', 'independents', 'maga', 'voter id', 'no kings act', 'state democrats', 'state dems', 'national democrats', 'national republicans', 'democrat establishment', 'republican establishment'}}


def resolution_fingerprint(registry):
    from extract_entities import sha
    return {'version': VERSION,
            'registry_hash': sha(sorted((str(k),v) for k,v in registry.items())),
            'policy_hash': sha({k: sorted(v) for k,v in EXCLUDED.items()})}


def _name_parts(name):
    name = re.sub(r'^(?:President|Senator|Sen\.|Governor|Gov\.|Rep\.|Dr\.|Congresswoman|Congressman|Speaker)\s+', '', name)
    return name.split()


def resolve_record(row, registry):
    from extract_entities import sha, alias_key
    if row.get('review_status') == 'human' or row.get('entities') is None:
        return row
    # Preserve extraction before filtering or merging so every pass is reversible.
    if 'extracted_entities' not in row:
        row['extracted_entities'] = deepcopy(row['entities'])
    entries = deepcopy(row['extracted_entities'])
    rejected, active = [], []
    for e in entries:
        e['mentions'] = [m for m in e['mentions'] if m.get('context') != 'recipient']
        if not e['mentions'] or e['name_as_written'].casefold() in EXCLUDED.get(e['type'], set()):
            rejected.append({'entity': e, 'reason': 'recipient_or_excluded_category'})
            continue
        active.append(e)
    # Full names establish local anchors; canonical ID combines spelling variants.
    anchors = {}
    for i,e in enumerate(active):
        found = {registry[(e['type'], alias_key(m['text']))]['id']:registry[(e['type'], alias_key(m['text']))] for m in e['mentions'] if (e['type'], alias_key(m['text'])) in registry}
        if len(found) > 1:
            raise ValueError('Conflicting registry identities in stored extraction')
        canonical = next(iter(found.values()), None)
        e['entity_id'] = canonical['id'] if canonical else None
        e['canonical_name'] = canonical['name'] if canonical else None
        e['resolution_status'] = 'registry' if canonical else 'unresolved'
        e['surface_key'] = canonical['id'] if canonical else 'unresolved:' + sha([e['type'], alias_key(e['name_as_written'])])[:24]
        if e['type'] == 'person':
            anchor_names = [m['text'] for m in e['mentions']]
            if canonical:
                anchor_names.append(canonical['name'])
            for anchor_name in anchor_names:
                parts = _name_parts(anchor_name)
                if len(parts) >= 2:
                    for alias in (parts[0],parts[-1]):
                        anchors.setdefault(alias.casefold(), set()).add(i)
    # Do not merge a short name if multiple distinct full-name anchors could match.
    local_links = []
    for e in active:
        if e['type'] != 'person' or len(_name_parts(e['name_as_written'])) != 1:
            continue
        candidates = [active[i] for i in anchors.get(_name_parts(e['name_as_written'])[0].casefold(), set())]
        keys = {a['surface_key'] for a in candidates}
        if len(keys) == 1:
            a = candidates[0]
            if e['surface_key'] == a['surface_key']:
                continue
            if e['entity_id'] and e['entity_id'] != a['entity_id']:
                continue
            for k in ('entity_id','canonical_name','surface_key'):
                e[k] = a[k]
            e['resolution_status'] = 'email_context'
            e['resolution_anchor'] = a['name_as_written']
            local_links.append({'alias': e['name_as_written'], 'anchor': a['name_as_written'], 'target_key': a['surface_key']})
    grouped = {}
    for e in active:
        key = e['surface_key']
        if key not in grouped:
            grouped[key] = e
        else:
            grouped[key]['mentions'].extend(e['mentions'])
            if len(e['name_as_written']) > len(grouped[key]['name_as_written']):
                grouped[key]['name_as_written'] = e['name_as_written']
    for e in grouped.values():
        e['mentions'] = list({(m['field'],m['start'],m['end'],m.get('context','campaign')):m for m in e['mentions']}.values())
        e['mentions'].sort(key=lambda m:(m['field'],m['start'],m['end']))
        # Counts should use this field plus distinct email IDs, not raw mentions.
        campaign_spans = [m for m in e['mentions'] if m.get('context','campaign') in ('campaign','signature')]
        e['campaign_mention'] = bool(campaign_spans)
        e['campaign_mention_review_required'] = bool(campaign_spans) and all(m.get('context_review_required',False) for m in campaign_spans)
    row['entities'] = sorted(grouped.values(), key=lambda e:e['surface_key'])
    row['excluded_entities'] = rejected
    row['resolution_links'] = local_links
    row['resolution'] = resolution_fingerprint(registry)
    return row


def main():
    from extract_entities import registry_index, write_json, writer_lock
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir',type=Path,default=Path('metadata/entities'))
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--registry',type=Path,default=Path('config/entity_registry.json'))
    a=p.parse_args()
    if a.input_dir.resolve()==a.output_dir.resolve():
        p.error('Use a separate output directory to preserve the original extraction')
    registry,_=registry_index(a.registry)
    count=0
    with writer_lock(a.output_dir):
        for path in sorted(a.input_dir.glob('*/*.json')):
            store=json.loads(path.read_text())
            for row in store['records'].values():
                resolve_record(row,registry)
                count+=1
            write_json(a.output_dir/path.relative_to(a.input_dir),store)
    print(f'Resolved {count} saved records without model calls')


if __name__=='__main__':
    main()
