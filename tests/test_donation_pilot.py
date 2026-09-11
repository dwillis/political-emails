import json

from donation_pilot import donation_links, extract_details, select, report, digest


def test_links_keep_query_and_exclude_unsubscribe():
    a = 'https://click.example.org/path?recipient=x&split=60'
    b = 'https://secure.actblue.com/donate/test?amount=20'
    record = {'body': f'[Chip in]({a}) [Unsubscribe](https://example.org/unsubscribe)', 'urls': [a, b]}
    links = donation_links(record)
    assert {link['url'] for link in links} == {a, b}
    assert next(link for link in links if link['url'] == a)['occurrences'][0]['anchor'] == 'Chip in'


def test_platform_host_spoof_not_selected():
    assert donation_links({'urls': ['https://actblue.com.evil.test/about', 'javascript:alert(1)']}) == []


def test_embedded_recipients_not_manager_or_platform():
    payload = {'managing_entity': {'display_name': 'Page sponsor'}, 'entities': [{'id': 1, 'display_name': 'Candidate A', 'kind': 'candidate'}, {'id': 2, 'display_name': 'Committee B', 'kind': 'pac'}]}
    raw = 'window.indigoListResponse = ' + json.dumps(payload) + ';'
    details = extract_details({'rawHtml': raw, 'markdown': 'Platform paid for by ActBlue'}, 'https://secure.actblue.com/donate/test')
    assert [r['name_as_written'] for r in details['recipients']] == ['Candidate A', 'Committee B']
    assert details['allocation']['type'] == 'unknown'
    assert all(r['committee_id'] is None for r in details['recipients'])


def test_unsupported_and_ambiguous_pages_need_review():
    for text in ['Donate to help defeat Jane Smith', 'Your contribution will benefit Jane and John.', 'Your contribution will benefit 3 candidates']:
        details = extract_details({'markdown': text}, 'https://example.org/donate')
        assert details['recipients'] == []
        assert details['status'] == 'needs_review'


def test_single_explicit_beneficiary():
    details = extract_details({'markdown': 'Your contribution will benefit Chris Pappas.Candidate'}, 'https://secure.actblue.com/donate/a')
    assert details['recipients'][0]['name_as_written'] == 'Chris Pappas'


def test_selection_eligibility_caps_and_counts(tmp_path):
    day = tmp_path / '2026/08/2026-08-31.jsonl'
    day.parent.mkdir(parents=True)
    rows = [{'unique_id': str(i), 'disclaimer': True, 'committee': 'Test for Congress', 'urls': ['https://secure.actblue.com/donate/test']} for i in range(3)]
    rows += [dict(rows[0], unique_id='4', committee=None), dict(rows[0], unique_id='5', disclaimer=False)]
    day.write_text('\n'.join(json.dumps(r) for r in rows))
    manifest = select(tmp_path, per_committee=2)
    assert len(manifest['emails']) == 2
    assert manifest['counts']['eligible'] == 3
    summary = report(manifest, {})
    assert summary['pending_pages'] == 1
    assert summary['emails_with_recipient_evidence'] == 0


def test_failed_fetch_is_not_empty_extraction():
    url = 'https://example.org/donate'
    manifest = {'emails': [{'committee': 'Test', 'committee_id': None, 'links': [{'url_id': digest(url)}]}]}
    summary = report(manifest, {digest(url): {'status': 'fetch_failed'}})
    assert summary['failed_pages'] == 1
    assert summary['pages_with_recipients'] == 0


def test_explicit_equal_split_and_customizable_default():
    payload = {'entities': [{'id': 1, 'display_name': 'Jane'}, {'id': 2, 'display_name': 'PAC'}]}
    doc = {'rawHtml': 'window.indigoListResponse = ' + json.dumps(payload), 'markdown': 'Your contribution will be split evenly between **Jane** and **PAC**.\nCustomize amounts'}
    details = extract_details(doc, 'https://secure.actblue.com/donate/test')
    assert details['allocation']['type'] == 'equal'
    assert [s['percent'] for s in details['allocation']['shares']] == [50, 50]
    assert details['allocation']['donor_adjustable'] is True
    doc['markdown'] = 'Your contribution will be split evenly between **Jane** and **Other PAC**.'
    assert extract_details(doc, 'https://secure.actblue.com/donate/test')['allocation']['type'] == 'unknown'


def test_equal_split_recipient_order_and_fee_not_allocation():
    payload = {'entities': [{'id': 1, 'display_name': 'PAC'}, {'id': 2, 'display_name': 'Jane'}]}
    doc = {'rawHtml': 'window.indigoListResponse = ' + json.dumps(payload), 'markdown': 'Your contribution will be split evenly between **Jane** and **PAC**.'}
    assert extract_details(doc, 'https://secure.actblue.com/donate/test')['allocation']['type'] == 'equal'
    doc = {'markdown': 'Your contribution will benefit Lawler for Congress.\nCover the fee so 100% goes to the campaign.'}
    result = extract_details(doc, 'https://secure.winred.com/lawler/form')
    assert result['recipients'][0]['name_as_written'] == 'Lawler for Congress'
    assert result['allocation']['type'] == 'unknown'
