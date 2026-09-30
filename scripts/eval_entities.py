"""Reproducible, stratified local entity evaluation (no archive mutation).

select includes the baseline failures/empties and balances additional emails by
month, party and template proxy. run checkpoints a bounded manifest with four
local workers. report summarizes structural checks; gold metrics need reviewed
annotations and are deliberately separate from extraction success.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path

import extract_entities as ee


def template(rec):
    body=rec.get('body') or ''
    if 'ngpvan.com' in body: return 'ngpvan'
    if 'list-manage.com' in body: return 'mailchimp'
    if 'winred.com' in body: return 'winred'
    if 'actblue.com' in body: return 'actblue'
    return 'other'


def select(a):
    if (a.work_dir/'manifest.json').exists():
        raise FileExistsError('This evaluation already has a manifest; use a new --work-dir to select another sample')
    baseline={}
    for p in a.baseline.glob('2026/*.json'):
        baseline.update(json.loads(p.read_text())['records'])
    forced={k for k,v in baseline.items() if v['processing_status']=='failed' or not v['entities']}
    # A small paired sample of ordinary successful results accompanies challenges.
    paired=sorted(set(baseline)-forced, key=lambda s:ee.sha([a.seed,s]))[:14]
    targets=forced|set(paired)
    buckets=defaultdict(list); rows={}; population=Counter()
    for p in sorted(a.data_dir.glob('2026/*/*.jsonl')):
        for line in p.open():
            rec=json.loads(line);uid=rec.get('unique_id')
            if not uid or not rec.get('disclaimer') or not ee.normalize_committee(rec.get('committee')): continue
            stratum=(p.parent.name,rec.get('party') or 'unknown',template(rec))
            population[stratum]+=1
            item={'email_id':uid,'source_file':str(p.resolve()),'month':stratum[0],'party':stratum[1],'template':stratum[2],'committee':ee.normalize_committee(rec['committee']),'selection':'challenge' if uid in forced else 'paired' if uid in targets else 'stratified'}
            if uid in targets: rows[uid]=item
            # Keep a bounded deterministic reservoir per stratum.
            rank=ee.sha([a.seed,uid]);b=buckets[stratum];b.append((rank,item))
            b.sort(key=lambda z:z[0]); del b[20:]
    selected=list(rows.values());seen=set(rows);committees=Counter(v['committee'] for v in selected)
    strata=sorted(buckets)
    while len(selected)<a.size:
        progress=False
        for key in strata:
            if len(selected)>=a.size: break
            b=buckets[key]
            candidates=[(i,v) for i,(_,v) in enumerate(b) if v['email_id'] not in seen]
            if not candidates:continue
            i,item=min(candidates,key=lambda iv:(committees[iv[1]['committee']],iv[0]))
            b.pop(i);selected.append(item);seen.add(item['email_id']);committees[item['committee']]+=1;progress=True
        if not progress:break
    if len(selected)!=a.size:raise ValueError('Insufficient eligible records for requested sample')
    ee.write_json(a.work_dir/'manifest.json',{'seed':a.seed,'size':len(selected),'design':'Purposive challenges plus balanced month/party/template sample; not population representative','population':{'/'.join(k):v for k,v in population.items()},'records':selected})
    ee.write_json(a.work_dir/'baseline.json',{'records':{uid:baseline[uid] for uid in seen if uid in baseline}})
    print(json.dumps({'selected':len(selected),'months':dict(Counter(v['month'] for v in selected)),'parties':dict(Counter(v['party'] for v in selected)),'templates':dict(Counter(v['template'] for v in selected)),'committees':len(committees),'paired_baseline':sum(uid in baseline for uid in seen)},indent=2))


def run(a):
    manifest=json.loads((a.work_dir/'manifest.json').read_text())
    by_path=defaultdict(set)
    for x in manifest['records']:by_path[x['source_file']].add(x['email_id'])
    source={}
    for p,ids in by_path.items():
        for line in Path(p).open():
            rec=json.loads(line)
            if rec['unique_id'] in ids:source[rec['unique_id']]=(rec,p)
    registry,rhash=ee.registry_index(a.registry)
    tags=ee.request_json(a.api_base,'/api/tags',timeout=10)
    model=next((v for v in tags['models'] if v['name']==a.model),None)
    if not model or model.get('remote_host'):raise ValueError('Requested local model is unavailable')
    out=a.work_dir/'results.json'
    with ee.writer_lock(a.work_dir),ThreadPoolExecutor(max_workers=a.workers) as pool:
        store=json.loads(out.read_text()) if out.exists() else {'records':{}}
        futures={}
        for item in manifest['records']:
            uid=item['email_id'];rec,path=source[uid]
            new=ee.prepare_record(rec,path,a.model,model['digest'],rhash)
            old=store['records'].get(uid)
            if old and old.get('review_status')=='human':continue
            if ee.fresh(old,new) and (old['processing_status']=='complete' or not a.retry_failed):continue
            futures[pool.submit(ee.process_record,new,a,registry)]=uid
        for future in as_completed(futures):
            row=future.result();store['records'][row['email_id']]=row
            ee.write_json(out,store)
            print(f"{len(store['records'])}/{len(manifest['records'])}: {row['email_id'][:12]} {row['processing_status']} {len(row['entities'] or [])} entities",flush=True)
    report(a)


def report(a):
    rows=json.loads((a.work_dir/'results.json').read_text())['records']
    baseline=json.loads((a.work_dir/'baseline.json').read_text())['records']
    complete=[r for r in rows.values() if r['processing_status']=='complete']
    entities=[e for r in complete for e in r['entities']]
    spans=[(r,m) for r in complete for e in r['entities'] for m in e['mentions']]
    manifest=json.loads((a.work_dir/'manifest.json').read_text())
    out={'selected':len(manifest['records']),'not_processed':len(manifest['records'])-len(rows),'records':len(rows),'complete':len(complete),'failed':len(rows)-len(complete),'empty':sum(not r['entities'] for r in complete),'campaign_empty':sum(not any(e.get('campaign_mention',False) for e in r['entities']) for r in complete),'entities':len(entities),'resolved':sum(bool(e['entity_id']) for e in entities),'campaign_entities':sum(e.get('campaign_mention',False) for e in entities),'bad_spans':sum(r['input_fields'][m['field']][m['start']:m['end']]!=m['text'] for r,m in spans),'contexts':dict(Counter(m.get('context') for r,m in spans)),'failures':[{'email_id':r['email_id'],'error':r.get('error')} for r in rows.values() if r['processing_status']!='complete'],'paired':{'total':len(baseline),'old_failed':sum(r['processing_status']=='failed' for r in baseline.values()),'new_failed':sum(rows[k]['processing_status']=='failed' for k in baseline if k in rows),'old_empty':sum(r['processing_status']=='complete' and not r['entities'] for r in baseline.values()),'new_empty':sum(rows[k]['processing_status']=='complete' and not rows[k]['entities'] for k in baseline if k in rows),'new_campaign_empty':sum(rows[k]['processing_status']=='complete' and not any(e.get('campaign_mention',False) for e in rows[k]['entities']) for k in baseline if k in rows)}}
    labels=defaultdict(set)
    for e in entities:
        if e.get('campaign_mention'):
            labels[ee.alias_key(e['name_as_written'])].add(e['type'])
    out['cross_email_type_conflicts']={k:sorted(v) for k,v in labels.items() if len(v)>1}
    out['repair_counts']={k:sum(bool(r.get(k)) for r in complete) for k in ('input_repairs','display_name_repairs','mention_field_repairs','mention_text_repairs','excluded_response_entities')}
    out['context_conflict_spans']=sum(bool(m.get('context_review_required')) for r,m in spans)
    by_id={x['email_id']:x for x in manifest['records']}
    out['coverage_by_party']={party:{'selected':sum(x['party']==party for x in manifest['records']), 'complete':sum(by_id[r['email_id']]['party']==party for r in complete)} for party in sorted({x['party'] for x in manifest['records']})}
    ee.write_json(a.work_dir/'report.json',out)
    print(json.dumps(out,indent=2))


def revalidate(a):
    registry,_=ee.registry_index(a.registry)
    path=a.work_dir/'results.json'
    with ee.writer_lock(a.work_dir):
        store=json.loads(path.read_text())
        snapshot=a.work_dir/'results-before-revalidation.json'
        if not snapshot.exists():ee.write_json(snapshot,store)
        for row in store['records'].values():
            if row.get('review_status')=='human' or not row.get('raw_response'):continue
            try:ee.validate_record_response(row,registry)
            except (ValueError,KeyError,TypeError) as exc:
                row['entities']=None
                row['processing_status']='failed'
                row['error']=f'{type(exc).__name__}: {exc}'
        ee.write_json(path,store)
    report(a)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['select','run','report','revalidate'])
    p.add_argument('--work-dir',type=Path,default=Path('state/validation/entities-v2'))
    p.add_argument('--data-dir',type=Path,default=Path('data'))
    p.add_argument('--baseline',type=Path,default=Path('metadata/entities'))
    p.add_argument('--registry',type=Path,default=Path('config/entity_registry.json'))
    p.add_argument('--size',type=int,default=150)
    p.add_argument('--seed',default='entities-2026-v2')
    p.add_argument('--model',default=ee.DEFAULT_MODEL)
    p.add_argument('--api-base',default='http://localhost:11434')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--timeout',type=int,default=240)
    p.add_argument('--retry-failed',action='store_true')
    a=p.parse_args()
    if a.workers<1 or a.size<30:p.error('workers must be positive; size must be at least 30')
    {'select':select,'run':run,'report':report,'revalidate':revalidate}[a.command](a)


if __name__=='__main__':main()
