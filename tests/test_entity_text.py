import entity_text as et
import extract_entities as ee
from resolve_entities import resolve_record


def test_linked_names_survive_without_tracking_urls():
    fields,repairs=et.prepare_text({'body':'[Support Jane Smith](https://example.org/donate?id=42).\n\nThanks,\nJane','clean_body':'Thanks, Jane'})
    assert fields['campaign_body']=='Support Jane Smith.\n\nThanks,\nJane'
    assert repairs==[]


def test_intro_unsubscribe_does_not_remove_campaign_copy():
    fields,_=et.prepare_text({'body':'[Unsubscribe](https://example.org).\n\nSenator Jane Smith represents Ohio.'})
    assert 'Senator Jane Smith represents Ohio.' in fields['campaign_body']


def test_known_join_repairs_preserve_real_camelcase():
    fields,repairs=et.prepare_text({'body':'Ken PaxtonAttorney General of Texas\nMcCarthy supports ActBlue. KateTeam Knott'})
    assert 'Ken Paxton\nAttorney' in fields['campaign_body']
    assert 'McCarthy supports ActBlue' in fields['campaign_body']
    assert len(repairs)==2


def test_recipient_exclusion_is_contextual():
    assert et.recipient_span('Hi Peter, please help.',3,8)
    assert not et.recipient_span('Peter Thiel supports Jane.',0,5)


def test_footer_override_is_line_local():
    text='Unsubscribe.\nJane Smith speaks.\nPaid for by Smith PAC.'
    pos=text.index('Jane')
    assert et.mention_context('campaign_body',text,pos,pos+10)=='campaign'
    pos=text.index('Smith PAC')
    assert et.mention_context('campaign_body',text,pos,pos+9)=='legal'


def make_row(names):
    text='; '.join(names)
    payload={'entities':[{'name':n,'type':'person','mentions':[{'field':'campaign_body','text':n}]} for n in names]}
    return {'entities':ee.validate_entities(payload,{'campaign_body':text},{}),'review_status':'unreviewed'}


def test_local_aliases_combine_but_ambiguous_surnames_do_not():
    row=make_row(['Elon Musk','Elon'])
    resolve_record(row,{})
    assert len(row['entities'])==1
    assert len(row['extracted_entities'])==2
    row=make_row(['Donald Trump','Mary Trump','Trump'])
    resolve_record(row,{})
    assert len(row['entities'])==3


def test_resolution_can_change_registry_without_losing_original():
    row=make_row(['Jane Smith','Jane'])
    resolve_record(row,{})
    canonical={'id':'person:jane','name':'Jane Smith','type':'person'}
    resolve_record(row,{('person','jane smith'):canonical})
    assert row['entities'][0]['entity_id']=='person:jane'
    resolve_record(row,{})
    assert row['entities'][0]['entity_id'] is None
    assert len(row['extracted_entities'])==2


def test_human_review_never_overwritten():
    row=make_row(['Jane Smith','Jane']);row['review_status']='human'
    resolve_record(row,{})
    assert len(row['entities'])==2
    assert 'resolution' not in row


def test_payment_entities_available_but_not_campaign_counts():
    row={'entities':[{'name_as_written':'ActBlue Express','type':'organization','mentions':[{'field':'campaign_body','text':'ActBlue Express','start':0,'end':15,'context':'payment'}]}]}
    resolve_record(row,{})
    assert len(row['entities'])==1
    assert row['entities'][0]['campaign_mention'] is False


def test_payment_instruction_does_not_relabel_earlier_appeal():
    text='Support Jane Smith. If you saved payment information with ActBlue Express, your donation will go through immediately.'
    assert et.mention_context('campaign_body',text,8,18)=='campaign'
    pos=text.index('ActBlue')
    assert et.mention_context('campaign_body',text,pos,pos+15)=='payment'


def test_display_label_repair_does_not_expand_unsupported_identity():
    payload={'entities':[{'name':'Sara Jacobs','mentions':[{'text':'Congresswoman Sara Jacobs'}]}, {'name':'Democratic Party','mentions':[{'text':'Democratic'}]}]}
    repairs=ee.repair_display_names(payload)
    assert len(repairs)==1
    assert payload['entities'][0]['name']=='Congresswoman Sara Jacobs'
    assert payload['entities'][1]['name']=='Democratic Party'


def test_repaired_display_label_still_requires_verbatim_evidence():
    row={'raw_response':'{"entities":[{"name":"Sara Jacobs","type":"person","mentions":[{"field":"campaign_body","text":"Congresswoman Sara Jacobs"}]}]}', 'input_fields':{'campaign_body':'Sara is speaking.'}}
    import pytest
    with pytest.raises(ValueError):ee.validate_record_response(row,{})


def test_wrong_field_repaired_only_when_evidence_exists_in_other_field():
    import json
    row={'raw_response':json.dumps({'entities':[{'name':'Jane Smith','type':'person','mentions':[{'field':'subject','text':'Jane Smith','context':'campaign'}]}]}),'input_fields':{'subject':'Please help','campaign_body':'Jane Smith asks.'}}
    ee.validate_record_response(row,{})
    assert row['entities'][0]['mentions'][0]['field']=='campaign_body'
    assert row['mention_field_repairs'][0]['original']=='subject'


def test_demonym_expansion_is_quarantined_not_promoted_to_place():
    import json
    row={'raw_response':json.dumps({'entities':[{'name':'Michigan','type':'place','mentions':[{'field':'campaign_body','text':'Michiganders','context':'campaign'}]}]}),'input_fields':{'campaign_body':'Michiganders deserve better.'}}
    ee.validate_record_response(row,{})
    assert row['entities']==[]
    assert len(row['excluded_response_entities'])==1


def test_title_registry_anchor_resolves_surname_only_within_email():
    registry={('person','president trump'):{'id':'person:donald_trump','name':'Donald Trump','type':'person'}}
    row=make_row(['President Trump','Trump'])
    resolve_record(row,registry)
    assert len(row['entities'])==1
    assert row['entities'][0]['entity_id']=='person:donald_trump'
    row=make_row(['President Trump','Mary Trump','Trump'])
    resolve_record(row,registry)
    assert len(row['entities'])==3


def test_repeated_name_in_signature_and_legal_does_not_erase_signature():
    fields={'campaign_body':'Thank you, Ken Paxton\nPaid for by Ken Paxton'}
    payload={'entities':[{'name':'Ken Paxton','type':'person','mentions':[{'field':'campaign_body','text':'Ken Paxton','context':c} for c in ['signature','legal']]}]}
    result=ee.validate_entities(payload,fields,{})
    assert [m['context'] for m in result[0]['mentions']]==['signature','legal']


def test_personalized_petition_excludes_reader_but_not_letter_targets():
    text='Add your name to sign the letter.\nTO: Jane Smith\nFROM: Willis\nDear Jane Smith,\nPlease help.\nX Willis\n'
    first=text.index('Willis');last=text.rindex('Willis')
    assert et.recipient_span(text,first,first+6)
    assert et.recipient_span(text,last,last+6)
    pos=text.index('Jane Smith')
    assert not et.recipient_span(text,pos,pos+10)
    forwarded='FROM: Willis\nThis is a forwarded message.'
    pos=forwarded.index('Willis')
    assert not et.recipient_span(forwarded,pos,pos+6)


def test_wrapped_name_uses_exact_source_whitespace_and_offsets():
    import json
    row={'raw_response':json.dumps({'entities':[{'name':'Jane Smith','type':'person','mentions':[{'field':'campaign_body','text':'Jane Smith','context':'campaign'}]}]}),'input_fields':{'subject':'','campaign_body':'Support Jane\nSmith today.'}}
    ee.validate_record_response(row,{})
    mention=row['entities'][0]['mentions'][0]
    assert mention['text']=='Jane\nSmith'
    assert row['input_fields'][mention['field']][mention['start']:mention['end']]==mention['text']
    assert row['mention_text_repairs'][0]['original']=='Jane Smith'


def test_whitespace_repair_does_not_correct_spelling():
    import json,pytest
    row={'raw_response':json.dumps({'entities':[{'name':'Jane Smith','type':'person','mentions':[{'field':'campaign_body','text':'Jane Smith','context':'campaign'}]}]}),'input_fields':{'subject':'','campaign_body':'Support Jane\nSmyth today.'}}
    with pytest.raises(ValueError):ee.validate_record_response(row,{})


def test_address_and_signature_boundaries_without_splitting_brands():
    fields,repairs=et.prepare_text({'body':'Susan CollinsU.S. Senator for Maine\nPO Box 1156Birmingham, MI 48012United States\nDel Webb BlvdLas Vegas, NV 88134\n7Eleven and 3M are brands.'})
    body=fields['campaign_body']
    assert 'Collins\nU.S. Senator' in body
    assert '1156\nBirmingham' in body and '48012\nUnited States' in body
    assert 'Blvd\nLas Vegas' in body
    assert '7Eleven and 3M' in body
    assert len(repairs)==4


def test_club_and_venue_joins_have_specific_grammatical_context():
    fields,_=et.prepare_text({'body':'TheMagic Circle Republican Women’s Clubhad to postpone.\n8:30pmIAFF Local 29 Union Hall804 S. Monroe St., SpokaneRSVP HERE\nTheStreet reports news.'})
    body=fields['campaign_body']
    assert 'The\nMagic Circle' in body and 'Club\nhad to' in body
    assert '8:30pm\nIAFF' in body and 'Hall\n804' in body and 'Spokane\nRSVP' in body
    assert 'TheStreet reports' in body
