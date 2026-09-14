import argparse
import json
from pathlib import Path

import pytest
import extract_entities as ee


def entity(name='Jane Smith', kind='person', field='campaign_body', text=None):
    return {'name': name, 'type': kind, 'mentions': [{'field': field, 'text': text or name}]}


def test_native_request_disables_thinking_and_requires_schema():
    request = ee.ollama_request({'subject': 'Test', 'campaign_body': ''}, ee.DEFAULT_MODEL)
    assert request['model'] == 'qwen3.8:latest'
    assert request['think'] is False
    assert request['stream'] is False
    assert request['format'] == ee.SCHEMA


def test_footer_removed_subject_preserved():
    fields = ee.input_fields({'subject': 'Jane Smith', 'clean_body': 'Help Jane Smith. Paid for by Jane Smith for Congress. Unsubscribe'})
    assert fields == {'subject': 'Jane Smith', 'campaign_body': 'Help Jane Smith. '}


def test_mentions_have_exact_offsets_and_deduplicate():
    fields = {'subject': '', 'campaign_body': 'Jane Smith asks. Support Jane Smith.'}
    result = ee.validate_entities({'entities': [entity(), entity()]}, fields, {})
    assert len(result) == 1
    assert len(result[0]['mentions']) == 2
    for span in result[0]['mentions']:
        assert fields[span['field']][span['start']:span['end']] == span['text']
    assert result[0]['entity_id'] is None


@pytest.mark.parametrize('item', [entity('Invented Person'), entity('Jane', text='Jane Smith'), entity('Jane Smith', kind='issue'), entity('Jane Smith', field='disclaimer')])
def test_invalid_or_invented_mentions_rejected(item):
    with pytest.raises(ValueError):
        ee.validate_entities({'entities': [item]}, {'subject': '', 'campaign_body': 'Jane Smith'}, {})


def test_no_substring_false_match():
    with pytest.raises(ValueError):
        ee.validate_entities({'entities': [entity('Ann')]}, {'campaign_body': 'Anne'}, {})


def test_empty_is_valid():
    assert ee.validate_entities({'entities': []}, {'subject': '', 'campaign_body': 'Please donate.'}, {}) == []


def test_registry_requires_explicit_alias(tmp_path):
    path = tmp_path / 'registry.json'
    path.write_text(json.dumps({'entities': [{'id': 'person:trump', 'type': 'person', 'name': 'Donald Trump', 'aliases': ['President Trump']}]}))
    index, _ = ee.registry_index(path)
    known = ee.validate_entities({'entities': [entity('President Trump')]}, {'campaign_body': 'President Trump'}, index)
    assert known[0]['entity_id'] == 'person:trump'
    unknown = ee.validate_entities({'entities': [entity('Trump')]}, {'campaign_body': 'Trump'}, index)
    assert unknown[0]['entity_id'] is None


def test_alias_collisions_fail(tmp_path):
    path = tmp_path / 'registry.json'
    path.write_text(json.dumps({'entities': [{'id': id_, 'type': 'person', 'name': 'Jane', 'aliases': []} for id_ in ['a', 'b']]}))
    with pytest.raises(ValueError):
        ee.registry_index(path)


def setup_run(tmp_path, monkeypatch):
    data = tmp_path / 'data/2026/08/2026-08-31.jsonl'
    data.parent.mkdir(parents=True)
    rec = {'unique_id': 'abc', 'date': '2026-08-31', 'disclaimer': True, 'committee': 'Test for Congress', 'clean_body': 'Jane Smith'}
    data.write_text(json.dumps(rec) + '\n')
    registry = tmp_path / 'registry.json'
    registry.write_text('{"entities": []}')
    calls = []
    def request(base, route, payload=None, timeout=180):
        if route == '/api/tags':
            return {'models': [{'name': ee.DEFAULT_MODEL, 'digest': 'digest1'}]}
        calls.append(payload)
        return {'done': True, 'message': {'content': json.dumps({'entities': [entity()]})}}
    monkeypatch.setattr(ee, 'request_json', request)
    args = argparse.Namespace(registry=registry, model=ee.DEFAULT_MODEL, api_base='http://localhost:11434', output_dir=tmp_path/'output', data_dir=tmp_path/'data', since=None, until=None, timeout=10, limit=1, workers=4, retry_failed=False)
    return args, data, rec, calls


def test_resume_and_source_changes(tmp_path, monkeypatch):
    args, data, rec, calls = setup_run(tmp_path, monkeypatch)
    assert ee.run(args) == 0
    assert ee.run(args) == 0
    assert len(calls) == 1
    rec['subject'] = 'New subject'
    data.write_text(json.dumps(rec))
    assert ee.run(args) == 0
    assert len(calls) == 2
    row = json.loads((args.output_dir/'2026/08.json').read_text())['records']['abc']
    assert row['processing_status'] == 'complete'
    assert row['provenance']['think'] is False


def test_failed_is_not_empty_and_retry_is_explicit(tmp_path, monkeypatch):
    args, data, rec, calls = setup_run(tmp_path, monkeypatch)
    rec['clean_body'] = 'Nobody named here'
    data.write_text(json.dumps(rec))
    assert ee.run(args) == 1
    row = json.loads((args.output_dir/'2026/08.json').read_text())['records']['abc']
    assert row['entities'] is None
    assert row['processing_status'] == 'failed'
    ee.run(args)
    assert len(calls) == 1
    args.retry_failed = True
    ee.run(args)
    assert len(calls) == 2


def test_remove_newly_ineligible_and_preserve_human(tmp_path, monkeypatch):
    args, data, rec, calls = setup_run(tmp_path, monkeypatch)
    ee.run(args)
    rec['disclaimer'] = False
    data.write_text(json.dumps(rec))
    ee.run(args)
    path = args.output_dir/'2026/08.json'
    assert json.loads(path.read_text())['records'] == {}
    rec['disclaimer'] = True
    data.write_text(json.dumps(rec))
    ee.run(args)
    store = json.loads(path.read_text())
    store['records']['abc']['review_status'] = 'human'
    path.write_text(json.dumps(store))
    rec['subject'] = 'Updated'
    data.write_text(json.dumps(rec))
    before = len(calls)
    ee.run(args)
    assert len(calls) == before


def test_writer_lock(tmp_path):
    with ee.writer_lock(tmp_path):
        with pytest.raises(RuntimeError):
            with ee.writer_lock(tmp_path):
                pass


def test_workers_overlap_preserve_results_and_respect_limit(tmp_path, monkeypatch):
    import threading

    args, data, rec, _ = setup_run(tmp_path, monkeypatch)
    args.limit = 4
    args.workers = 4
    data.write_text('\n'.join(json.dumps(dict(rec, unique_id=str(i), subject=str(i))) for i in range(6)))
    barrier = threading.Barrier(4)
    callers = set()
    guard = threading.Lock()
    coordinator = threading.get_ident()
    original_write = ee.write_json

    def write(path, value):
        assert threading.get_ident() == coordinator
        original_write(path, value)

    def request(base, route, payload=None, timeout=180):
        if route == '/api/tags':
            return {'models': [{'name': ee.DEFAULT_MODEL, 'digest': 'digest1'}]}
        subject = json.loads(payload['messages'][1]['content'])['subject']
        with guard:
            callers.add(threading.get_ident())
        barrier.wait(timeout=5)
        if subject == '1':
            raise OSError('Test connection failure')
        return {'done': True, 'message': {'content': json.dumps({'entities': [entity()]})}}

    monkeypatch.setattr(ee, 'request_json', request)
    monkeypatch.setattr(ee, 'write_json', write)
    assert ee.run(args) == 1
    assert len(callers) == 4
    records = json.loads((args.output_dir / '2026/08.json').read_text())['records']
    assert set(records) == {'0', '1', '2', '3'}
    assert records['1']['processing_status'] == 'failed'
    assert all(records[i]['processing_status'] == 'complete' for i in ['0', '2', '3'])
    report = json.loads((args.output_dir / 'report.json').read_text())
    assert report['counts']['attempted'] == 4
    assert report['counts']['completed'] == 3
    assert report['workers'] == 4


def test_default_workers_and_validation(monkeypatch):
    import sys

    seen = []
    monkeypatch.setattr(ee, 'run', lambda args: seen.append(args.workers) or 0)
    monkeypatch.setattr(sys, 'argv', ['extract_entities.py'])
    assert ee.main() == 0
    assert seen == [4]
    monkeypatch.setattr(sys, 'argv', ['extract_entities.py', '--workers', '0'])
    with pytest.raises(SystemExit) as exc:
        ee.main()
    assert exc.value.code == 2
