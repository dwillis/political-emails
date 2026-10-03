from score_entity_reference import score


def test_failures_count_as_misses_and_duplicate_aliases_are_separate():
    reference={'scope':['campaign','signature'],'records':[{'email_id':'a','selection':'stratified','entities':[{'type':'person','aliases':['Jane Smith','Jane']}]},{'email_id':'b','selection':'challenge','entities':[{'type':'person','aliases':['Joe']}] }]}
    def ent(name):return {'name_as_written':name,'type':'person','mentions':[{'text':name,'context':'campaign'}]}
    predictions={'a':{'processing_status':'complete','entities':[ent('Jane Smith'),ent('Jane'),ent('Wrong')]},'b':{'processing_status':'failed','entities':None}}
    s=score(reference,predictions)['overall']
    assert s['matched_entities']==1 and s['missed_entities']==1
    assert s['false_inclusions']==1 and s['extra_alias_entries']==1
    assert s['precision_deduplicated']==0.5 and s['recall_including_failed_emails']==0.5


def test_reference_scoring_tolerates_source_line_wraps():
    from score_entity_reference import normalize
    assert normalize('Senator Jane\nSmith')==normalize('Jane Smith')


def test_sample_includes_challenges_balances_strata_and_refuses_overwrite(tmp_path):
    import argparse,json,pytest
    from eval_entities import select
    data=tmp_path/'data';baseline=tmp_path/'baseline';work=tmp_path/'work'
    challenge_id='01-D-0'
    for month in ['01','02']:
        path=data/'2026'/month/f'2026-{month}-01.jsonl';path.parent.mkdir(parents=True)
        rows=[{'unique_id':f'{month}-{party}-{i}','date':f'2026-{month}-01','party':party,'committee':f'Committee {month} {party} {i}','disclaimer':True,'body':'Help our campaign.'} for party in ['D','R',None] for i in range(8)]
        path.write_text('\n'.join(json.dumps(r) for r in rows))
    p=baseline/'2026/01.json';p.parent.mkdir(parents=True)
    p.write_text(json.dumps({'records':{challenge_id:{'processing_status':'failed','entities':None}}}))
    args=argparse.Namespace(data_dir=data,baseline=baseline,work_dir=work,seed='test',size=30)
    select(args)
    result=json.loads((work/'manifest.json').read_text())['records']
    assert len(result)==len({r['email_id'] for r in result})==30
    assert {r['month'] for r in result}=={'01','02'}
    assert {r['party'] for r in result}=={'D','R','unknown'}
    assert next(r for r in result if r['email_id']==challenge_id)['selection']=='challenge'
    with pytest.raises(FileExistsError):select(args)
