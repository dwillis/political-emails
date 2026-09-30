"""Score a source-reviewed reference separately from structural validity.

Entity presence uses observed aliases, not external identity inference. Duplicate
predicted entries matching one reference identity are measured as fragmentation,
not counted as additional correct identities. Failed rows yield missing entities.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import unicodedata

from extract_entities import write_json


def normalize(text):
    text=' '.join(unicodedata.normalize('NFKC',text).casefold().replace('’',"'").split())
    text=re.sub(r'^(?:(?:senator|sen\.|representative|rep\.|president|congresswoman|congressman|governor|gov\.|dr\.|speaker|sheriff)\s+)+','',text)
    return text.strip(' .—–-')


def score(reference, predictions, allowed_ids=None):
    details=[]
    for gold in reference['records']:
        uid=gold['email_id']
        if allowed_ids is not None and uid not in allowed_ids:continue
        row=predictions.get(uid)
        expected=gold['entities']
        predicted=[] if not row or row['processing_status']!='complete' else [e for e in row['entities'] if any(m.get('context','campaign') in reference['scope'] for m in e['mentions'])]
        by_gold=Counter();extra=[];merges=[]
        for e in predicted:
            aliases={normalize(m['text']) for m in e['mentions'] if m.get('context','campaign') in reference['scope']}
            match=[i for i,g in enumerate(expected) if g['type']==e['type'] and aliases.intersection(normalize(v) for v in g['aliases'])]
            if not match:extra.append({'name':e['name_as_written'],'type':e['type'],'aliases':sorted(aliases)})
            for i in match:by_gold[i]+=1
            if len(match)>1:merges.append([expected[i] for i in match])
        missing=[g for i,g in enumerate(expected) if i not in by_gold]
        details.append({'email_id':uid,'selection':gold['selection'],'status':row['processing_status'] if row else 'not_processed','expected':len(expected),'matched':len(by_gold),'expected_by_type':dict(Counter(g['type'] for g in expected)),'matched_by_type':dict(Counter(expected[i]['type'] for i in by_gold)),'false_inclusions':extra,'missed':missing,'extra_alias_entries':sum(max(0,n-1) for n in by_gold.values()),'overmerged_groups':merges})
    def summarize(ds):
        matched=sum(d['matched'] for d in ds);fp=sum(len(d['false_inclusions']) for d in ds);expected=sum(d['expected'] for d in ds)
        by_type = {}
        for kind in ('person','organization','place'):
            total = sum(d['expected_by_type'].get(kind,0) for d in ds)
            found = sum(d['matched_by_type'].get(kind,0) for d in ds)
            extra = sum(e['type']==kind for d in ds for e in d['false_inclusions'])
            by_type[kind] = {'expected':total,'matched':found,'false_inclusions':extra,'missed':total-found,'precision':found/(found+extra) if found+extra else None,'recall':found/total if total else None}
        return {'emails':len(ds),'completed':sum(d['status']=='complete' for d in ds),'expected_entities':expected,'matched_entities':matched,'false_inclusions':fp,'missed_entities':expected-matched,'precision_deduplicated':matched/(matched+fp) if matched+fp else None,'recall_including_failed_emails':matched/expected if expected else None,'extra_alias_entries':sum(d['extra_alias_entries'] for d in ds),'overmerged_entries':sum(len(d['overmerged_groups']) for d in ds),'by_type':by_type}
    return {'overall':summarize(details),'challenge':summarize([d for d in details if d['selection']=='challenge']),'held_out':summarize([d for d in details if d['selection']=='stratified']),'ordinary_paired':summarize([d for d in details if d['selection']=='paired']),'details':details}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir',type=Path,default=Path('state/validation/entities-v2'))
    a=p.parse_args();w=a.work_dir
    reference=json.loads((w/'reference.json').read_text())
    predictions=json.loads((w/'results.json').read_text())['records']
    baseline=json.loads((w/'baseline.json').read_text())['records']
    result={'reference_limitations':reference['limitations'],'current':score(reference,predictions),'baseline_on_available_reference':score(reference,baseline,set(baseline))}
    write_json(w/'reference-scores.json',result)
    print(json.dumps({k:{s:v[s] for s in ('overall','challenge','held_out','ordinary_paired')} for k,v in result.items() if isinstance(v,dict)},indent=2))


if __name__=='__main__':main()
