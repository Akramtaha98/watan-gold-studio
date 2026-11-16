"""Adversarial row isolation, verifier faults and portal contention.

All records are synthetic; conftest provides a temporary persistent backend.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests

import mock_qwen as mock
import watan_gold_studio as w
from storage import Storage, ConflictError
from test_four_tiers import CONFIG, POLICY, ingest, response


@pytest.mark.parametrize('invalid',[[],['secret@example.my'],None,True,3,'secret@example.my'])
def test_nonobject_json_row_is_quarantined_and_later_rows_survive(invalid):
    payload=w.jsonl_bytes([{'text':'Perenggan pertama.'},invalid,{'text':'Perenggan terakhir.'}])
    batch=w.ingest_files([('rows.jsonl',payload)])
    assert [r['status'] for r in batch['records']]==['PENDING','QUARANTINE','PENDING']
    assert not batch['issues']
    assert 'secret@example.my' not in json.dumps(batch)
    assert batch['records'][-1]['source_row']=='3'


def test_hundreds_of_malformed_lines_do_not_hide_valid_tail():
    data=(b'not json\n'+b'{"text":"duplicate","text":"key"}\n'+b'{"text":NaN}\n')*100
    batch=w.ingest_files([('bad.jsonl',data+w.jsonl_bytes([{'text':'Ayat sah terakhir.'}]))])
    assert len(batch['records'])==301
    assert sum(r['status']=='QUARANTINE' for r in batch['records'])==300
    assert batch['records'][-1]['status']=='PENDING'


def source():
    return ingest([{'messages':[{'role':'system','content':'Kekalkan <EMAIL>.'},{'role':'user','content':'Semak rekod <IC>.'},{'role':'assistant','content':'Rekod <IC> dan <EMAIL> dirahsiakan.'}]}])['records'][0]


@pytest.mark.parametrize('attack',['truncated','fences','missing','extra','boolean_score','nan','changed_user','changed_system','removed_mask','invented_role','wrong_stage','unmasked_pii'])
def test_untrusted_model_output_never_auto_passes(monkeypatch,attack):
    record=source();result=response(record)
    finish='stop'
    if attack=='truncated':finish='length'
    elif attack=='missing':del result['confidence']
    elif attack=='extra':result['unrequested']='value'
    elif attack=='boolean_score':result['confidence']=True
    elif attack=='nan':result['confidence']=float('nan')
    elif attack=='changed_user':result['messages'][1]['content']='Replaced user'
    elif attack=='changed_system':result['messages'][0]['content']='Replaced system'
    elif attack=='removed_mask':result['messages'][-1]['content']='Rekod <IC> dirahsiakan.'
    elif attack=='invented_role':result['messages'].append({'role':'tool','content':'Invented'})
    elif attack=='wrong_stage':result['stage']='GOLD_1'
    elif attack=='unmasked_pii':result['messages'][-1]['content']+=' real@example.my'
    content=json.dumps(result)
    if attack=='fences':content='```json\n'+content+'\n```'
    monkeypatch.setattr(w,'request_json',lambda *a,**kw:{'choices':[{'finish_reason':finish,'message':{'content':content}}]})
    w.verify_record(record,CONFIG,POLICY)
    if attack=='removed_mask':
        assert record['status']=='PTSL_REVIEW' and record['candidate']==record['original']
        assert any('PII placeholder' in e for e in record['verification']['result']['policy_errors'])
        return
    assert record['status']=='ERROR' and record['verification'] is None
    assert record['candidate']==record['original']
    assert 'real@example.my' not in record['reason']
    with pytest.raises(ValueError):w.build_exports({'records':[record],'sources':[]},CONFIG,POLICY)


@pytest.mark.parametrize('failure',[429,503,'timeout','malformed'])
def test_transient_and_invalid_transport_fail_closed(monkeypatch,failure):
    calls=[]
    def transport(*a,**kw):
        calls.append(kw)
        if failure=='timeout':raise requests.Timeout('secret@example.my')
        return SimpleNamespace(status_code=200 if failure=='malformed' else failure,text='private invalid reply secret@example.my')
    monkeypatch.setattr(w.requests,'request',transport)
    monkeypatch.setattr(w.time,'sleep',lambda *_:None)
    record=source();w.verify_record(record,CONFIG,POLICY)
    assert record['status']=='ERROR' and not record['verification']
    assert 'secret@example.my' not in record['reason']
    assert len(calls)==(1 if failure=='malformed' else 2)


def queued_batch(monkeypatch,n):
    rows=[{'prompt':f'Code {i}: Apakah maksud istilah ini?', 'response':'Jawapan disemak.'} for i in range(n)]
    batch=ingest(rows)
    monkeypatch.setattr(w,'verify_with_qwen',lambda r,*_:response(r,confidence=.85))
    for r in batch['records']:w.verify_record(r,CONFIG,POLICY)
    store=Storage();key,*_=store.create_batch(batch,{'config':CONFIG.public(),'policy':POLICY})
    return store,key


def decision(item):return {'record_id':item['record_id'],'expected_revision':item['expected_revision'],'verdict':'Good','reviewer_role':'Linguist'}


def test_sixteen_reviewers_cannot_overwrite_same_record(monkeypatch):
    store,key=queued_batch(monkeypatch,1);item=store.queue(key)[0]
    def work(_):
        try:store.submit([decision(item)]);return 'accepted'
        except ConflictError:return 'conflict'
    with ThreadPoolExecutor(max_workers=16) as pool:results=list(pool.map(work,range(16)))
    assert results.count('accepted')==1 and results.count('conflict')==15
    assert store.status(key)['remaining']==0
    assert len(store.load_batch(key)['snapshot']['records'][0]['history'])==2
    store.close()


def test_hundred_verdict_atomic_submission_and_last_row_failure(monkeypatch):
    store,key=queued_batch(monkeypatch,100);items=store.queue(key);assert len(items)==100
    before=store.load_batch(key)
    decisions=[decision(item) for item in items]
    broken=copy.deepcopy(decisions);broken[-1]['edited_text']='real@example.my'
    with pytest.raises(ValueError):store.submit(broken)
    assert store.load_batch(key)==before
    assert store.submit(decisions)['accepted']==100
    assert store.status(key)['remaining']==0
    store.close()


def test_thousand_record_saved_workspace_renders_in_guided_wizard():
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    batch=ingest([{'text':f'Perenggan ujian nombor {i}: Penulisan yang jelas membantu pembaca.'} for i in range(1000)])
    store=Storage();key,_=store.create_batch(batch,{'config':CONFIG.public(),'policy':POLICY})
    app=AppTest.from_file(str(Path(w.__file__))).run(timeout=30)
    assert not app.exception
    assert app.session_state['batch_id']==key
    assert app.session_state['active_step']==1
    assert len(app.session_state['batch']['records'])==1000
    assert app.metric[0].value=='1000'
    assert not next(b for b in app.button if b.label=='Run Qwen 3 Verification').disabled
    store.close()
