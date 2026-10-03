"""Build a local source-and-result review page and unresolved-name queue."""
import argparse
from collections import Counter, defaultdict
from html import escape
import json
from pathlib import Path

from extract_entities import write_json


def build(work):
    rows=json.loads((work/'results.json').read_text())['records']
    manifest=json.loads((work/'manifest.json').read_text())['records']
    scores=json.loads((work/'reference-scores.json').read_text()) if (work/'reference-scores.json').exists() else {}
    checks={d['email_id']:d for d in scores.get('current',{}).get('details',[])}
    sections=[];queue=defaultdict(lambda:{'emails':set(),'aliases':set(),'examples':[]})
    for item in manifest:
        row=rows.get(item['email_id'])
        if not row:continue
        check=checks.get(item['email_id']);entries=[]
        for e in row['entities'] or []:
            contexts=', '.join(sorted({m.get('context','campaign') for m in e['mentions']}))
            if e.get('campaign_mention_review_required'): contexts += ' (context review needed)'
            mentions='; '.join(sorted({m['text'] for m in e['mentions']}))
            identity=e.get('canonical_name') or 'Unresolved'
            entries.append('<tr>'+''.join(f'<td>{escape(str(v))}</td>' for v in (e['name_as_written'],e['type'],identity,contexts,mentions))+'</tr>')
            if not e.get('entity_id') and e.get('campaign_mention'):
                q=queue[(e['type'],e['name_as_written'])];q['emails'].add(row['email_id']);q['aliases'].update(m['text'] for m in e['mentions'])
                if len(q['examples'])<3:q['examples'].append({'email_id':row['email_id'],'source_file':row['source_file'],'subject':row['input_fields']['subject']})
        audit=''
        if check:
            audit='<p><b>Source-reviewed reference:</b> '+escape(f"{check['matched']}/{check['expected']} entity groups found; {len(check['false_inclusions'])} false inclusions; {check['extra_alias_entries']} extra alias entries.")+'</p>'
            audit+='<details><summary>Reference differences</summary><pre>'+escape(json.dumps(check,indent=2,ensure_ascii=False))+'</pre></details>'
        body=escape(row['input_fields']['campaign_body'])
        sections.append(f'''<article><h2>{escape(row['input_fields']['subject'])}</h2>
<p>{escape(item['committee'])} · {escape(row['date'])} · {escape(row['processing_status'])}</p>
<small>{escape(row['email_id'])}</small>{audit}
<div class="columns"><section><h3>Input text</h3><pre>{body}</pre></section><section><h3>Extracted entities</h3>
<p class="error">{escape(row.get('error',''))}</p><table><thead><tr><th>Name</th><th>Type</th><th>Identity</th><th>Context</th><th>Evidence names</th></tr></thead><tbody>{''.join(entries)}</tbody></table>
<details><summary>Repairs and exclusions</summary><pre>{escape(json.dumps({k:row.get(k) for k in ['input_repairs','display_name_repairs','mention_field_repairs','mention_text_repairs','excluded_response_entities','excluded_entities','resolution_links']},ensure_ascii=False,indent=2))}</pre></details>
</section></div></article>''')
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Entity evaluation review</title>
<style>body{font:16px/1.5 system-ui;margin:2rem;color:#152638;background:#f4f6f8}header{position:sticky;top:0;background:#f4f6f8;padding:1rem 0}input{padding:.6rem;width:min(95%,650px);font:inherit}article{background:white;padding:1.5rem;margin:1.5rem 0;border:1px solid #ccd5df;border-radius:8px}h2{font-size:1.2rem}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.55 system-ui}.columns{display:grid;grid-template-columns:1fr 1fr;gap:2rem}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid #ddd;text-align:left;padding:6px;vertical-align:top;overflow-wrap:anywhere}.error{color:#a22}small{overflow-wrap:anywhere}details{margin-top:1rem}@media(max-width:1000px){.columns{display:block}}</style>
<header><h1>Entity extraction evaluation</h1><p>Local source review. Reference judgments are assistant-reviewed and provisional. Search by subject, committee, name, or email ID.</p><input id="search" placeholder="Filter emails" aria-label="Filter emails"><span id="count"></span></header>'''+''.join(sections)+'''
<script>const a=[...document.querySelectorAll('article')];document.querySelector('#search').addEventListener('input',e=>{const q=e.target.value.toLowerCase();let n=0;for(const x of a){x.hidden=!x.textContent.toLowerCase().includes(q);if(!x.hidden)n++}document.querySelector('#count').textContent=' '+n+' emails';});</script></html>'''
    (work/'review.html').write_text(page)
    candidates=[{'type':k[0],'name':k[1],'email_count':len(v['emails']),'aliases':sorted(v['aliases']),'examples':v['examples']} for k,v in queue.items()]
    candidates.sort(key=lambda c:(-c['email_count'],c['type'],c['name']))
    write_json(work/'registry-candidates.json',{'status':'review suggestions only; unresolved labels are not verified identities','candidates':candidates})
    print(work/'review.html')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work-dir',type=Path,default=Path('state/validation/entities-v2'));build(p.parse_args().work_dir)
