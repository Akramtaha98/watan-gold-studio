"""Guided navigation and reviewer-only mode; backend rules remain authoritative."""
import copy
import io
import json
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

import watan_gold_studio as w
from test_four_tiers import CONFIG, POLICY, ingest, response, dialogues
from test_streamlit import APP, find_button, find_input, source


def mode(app,value):
    return next(x for x in app.radio if x.label=='Workspace Mode').set_value(value).run(timeout=15)


def app_with_batch(batch,ptsl=False):
    app=AppTest.from_file(str(APP))
    app.session_state['batch']=batch
    if ptsl:app.query_params['mode']='ptsl'
    app.run(timeout=15)
    assert not app.exception
    return app


def verified(monkeypatch,rows=None,confidence=.9,**flags):
    batch=ingest(rows or dialogues(1))
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*a:response(record,confidence=confidence,**flags))
    for r in batch['records']:w.verify_record(r,CONFIG,POLICY)
    return batch


def assert_step(app,number):
    assert not app.exception
    assert app.session_state['active_step']==number



def transport_fixture(monkeypatch, confidence=.9, **flags):
    def transport(method, url, **kwargs):
        corpus=json.loads(kwargs['json']['messages'][1]['content'])['corpus_record']
        record={'kind':corpus['kind'],'original':corpus['messages'],'original_text':corpus['text'],'eval_tag':corpus['eval_tag']}
        result=response(record,confidence=confidence,**flags)
        return SimpleNamespace(status_code=200,text=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}))
    monkeypatch.setattr(w.requests,'request',transport)

def test_auto_advance_to_review_then_full_release(monkeypatch):
    batch=source();app=app_with_batch(batch)
    transport_fixture(monkeypatch,confidence=.85)
    find_button(app,'Run Qwen 3 Verification').click().run(timeout=15)
    assert_step(app,3)
    assert not any(x.label=='Upload corpus files' for x in app.get('file_uploader'))
    find_button(app,'Approve').click().run(timeout=15)
    assert_step(app,4)
    assert any('All PTSL reviews completed' in x.value for x in app.success)
    assert not any(x.label=='Approve' for x in app.button)
    assert any(x.label=='Validate & Download Gold Release (.zip)' for x in app.get('download_button'))


@pytest.mark.parametrize('unsafe',[False,True])
def test_clean_or_quarantined_verification_skips_review(monkeypatch,unsafe):
    app=app_with_batch(source())
    transport_fixture(monkeypatch,confidence=.98,unsafe=unsafe)
    find_button(app,'Run Qwen 3 Verification').click().run(timeout=15)
    assert_step(app,4)
    assert not any(x.label=='Review record' for x in app.selectbox)
    download=next(x for x in app.get('download_button') if x.label.startswith('Validate & Download'))
    assert download.disabled is unsafe


def test_api_failure_stays_on_upload_and_is_retryable(monkeypatch):
    app=app_with_batch(source())
    def failure(*a,**kw):raise w.requests.ConnectionError('Offline test service')
    monkeypatch.setattr(w.requests,'request',failure)
    find_button(app,'Run Qwen 3 Verification').click().run(timeout=15)
    assert_step(app,1)
    assert app.session_state['batch']['records'][0]['status']=='ERROR'
    assert not find_button(app,'Run Qwen 3 Verification').disabled
    assert find_button(app,'4. Gold Release').disabled


@pytest.mark.parametrize('action',['Approve','Quarantine','Save edit & approve','Approve All High-Confidence'])
def test_every_direct_resolution_advances_when_global_queue_empty(monkeypatch,action):
    flags={'dbp_errors':['Demo correction']} if action=='Approve All High-Confidence' else {}
    batch=verified(monkeypatch,confidence=.99 if flags else .9,**flags)
    app=app_with_batch(batch)
    next(x for x in app.selectbox if x.label=='Reviewer Role').set_value('Linguist').run(timeout=15)
    mode(app,w.WORKSPACE_MODES[0])
    if action=='Save edit & approve':
        find_button(app,'Edit & Approve').click().run(timeout=15)
        editor=next(x for x in app.text_area if x.label=='Candidate messages JSON')
        turns=copy.deepcopy(batch['records'][0]['candidate']);turns[-1]['content']='Jawapan contoh yang telah disemak.'
        editor.set_value(json.dumps(turns))
    find_button(app,action).click().run(timeout=15)
    assert_step(app,4)
    assert any(x.label.startswith('Validate & Download') for x in app.get('download_button'))
    assert not any(r['status']=='PTSL_REVIEW' for r in app.session_state['batch']['records'])


def test_filtered_empty_queue_never_completes_global_review(monkeypatch):
    batch=verified(monkeypatch,[{'text':'Perenggan contoh yang perlu disemak.'},*dialogues(1)])
    app=app_with_batch(batch)
    next(x for x in app.get('button_group') if x.label=='Stage filter').set_value('Gold 3').run(timeout=15)
    find_button(app,'Approve').click().run(timeout=15)
    assert_step(app,3)
    assert sum(r['status']=='PTSL_REVIEW' for r in app.session_state['batch']['records'])==1
    assert not any(x.label=='Review record' for x in app.selectbox)
    assert find_button(app,'4. Gold Release').disabled


def test_reviewer_waiting_state_hides_engineering_controls():
    app=app_with_batch(w.ingest_files([]),ptsl=True)
    assert_step(app,3)
    assert [m.label for m in app.metric]==['Remaining in Queue','Reviewed','Reviewer Role']
    assert not app.text_input and not app.get('file_uploader')
    assert not any(x.label in {'Test Connection','Run Qwen 3 Verification','1. Bronze & Silver (Extract + MinHash)'} for x in app.button)
    assert any('Waiting for records queued for PTSL review' in x.value for x in app.info)
    assert next(x for x in app.selectbox if x.label=='Reviewer Role').value=='Linguist'


def test_reviewer_completion_confirmation_never_exposes_release_controls(monkeypatch):
    app=app_with_batch(verified(monkeypatch),ptsl=True)
    assert_step(app,3)
    assert any(x.label=='Export PTSL Excel Pack (.xlsx)' for x in app.get('download_button'))
    find_button(app,'Approve').click().run(timeout=15)
    assert_step(app,4)
    assert [m.value for m in app.metric][:2]==['0','1']
    assert any('All PTSL reviews completed' in x.value for x in app.success)
    assert not app.get('download_button') and not app.text_input
    mode(app,w.WORKSPACE_MODES[0])
    assert_step(app,4)
    assert any(x.label.startswith('Validate & Download') for x in app.get('download_button'))


def test_linguist_defaults_to_focused_mode_unless_mode_explicit():
    app=app_with_batch(w.ingest_files([]))
    next(x for x in app.selectbox if x.label=='Reviewer Role').set_value('Linguist').run(timeout=15)
    assert app.session_state['workspace_mode']==w.WORKSPACE_MODES[1]
    mode(app,w.WORKSPACE_MODES[0])
    assert any(x.label=='API Base URL' for x in app.text_input)
    next(x for x in app.selectbox if x.label=='Reviewer Role').set_value('Project_Lead').run(timeout=15)
    next(x for x in app.selectbox if x.label=='Reviewer Role').set_value('Linguist').run(timeout=15)
    assert app.session_state['workspace_mode']==w.WORKSPACE_MODES[0]


def test_nondefault_profile_and_batch_survive_hidden_widgets(monkeypatch):
    app=app_with_batch(source())
    find_input(app,'API Base URL').set_value('http://127.0.0.1:9001/v1').run(timeout=15)
    next(x for x in app.selectbox if x.label=='JSON mode').set_value('json_object').run(timeout=15)
    next(x for x in app.selectbox if x.label=='Target Mode').set_value('Gold 3').run(timeout=15)
    transport_fixture(monkeypatch,confidence=.9)
    find_button(app,'Run Qwen 3 Verification').click().run(timeout=15)
    assert_step(app,3)
    mode(app,w.WORKSPACE_MODES[1])
    assert not app.text_input
    app.run(timeout=15) # hidden widget cleanup has now run
    assert not find_button(app,'Approve').disabled
    find_button(app,'Approve').click().run(timeout=15)
    mode(app,w.WORKSPACE_MODES[0])
    assert_step(app,4)
    assert find_input(app,'API Base URL').value=='http://127.0.0.1:9001/v1'
    assert app.session_state['pipeline_settings']['mode']=='json_object'
    assert app.session_state['pipeline_settings']['target_label']=='Gold 3'
    assert len(app.session_state['batch']['records'])==1


def test_backward_navigation_preserves_batch_export_options_and_resume(monkeypatch):
    app=app_with_batch(verified(monkeypatch,confidence=.99))
    assert_step(app,4)
    next(x for x in app.checkbox if x.label=='Generate ShareGPT conversations').check().run(timeout=15)
    next(x for x in app.slider if x.label=='Overlap (tokens)').set_value(72).run(timeout=15)
    find_button(app,'1. Bronze & Silver (Extract + MinHash)').click().run(timeout=15)
    assert_step(app,1)
    assert len(app.session_state['batch']['records'])==1
    assert find_button(app,'Run Qwen 3 Verification').disabled
    assert next(x for x in app.selectbox if x.label=='Target Mode').value=='Auto-Route All 4 Gold Stages'
    find_button(app,'4. Gold Release').click().run(timeout=15)
    assert_step(app,4)
    assert next(x for x in app.checkbox if x.label=='Generate ShareGPT conversations').value
    assert next(x for x in app.slider if x.label=='Overlap (tokens)').value==72


@pytest.mark.parametrize('verdict,expected_step',[('Good',4),('Bad',4),('Unsure',3)])
def test_excel_commit_advances_only_when_resolved(monkeypatch,verdict,expected_step):
    import streamlit as st
    app=app_with_batch(verified(monkeypatch),ptsl=True)
    data=app.session_state['ptsl_pack']
    workbook=load_workbook(io.BytesIO(data));workbook['PTSL Review']['F2']=verdict
    out=io.BytesIO();workbook.save(out)
    original=st.file_uploader
    def uploader(label,*args,**kwargs):
        return SimpleNamespace(getvalue=lambda:out.getvalue()) if label=='Import Reviewed Excel (.xlsx)' else original(label,*args,**kwargs)
    monkeypatch.setattr(st,'file_uploader',uploader)
    app.run(timeout=15)
    find_button(app,'Apply Excel verdicts').click().run(timeout=15)
    assert_step(app,expected_step)
    if verdict=='Unsure':assert app.session_state['batch']['records'][0]['secondary_review']


def test_stale_settings_return_full_pipeline_to_verification(monkeypatch):
    app=app_with_batch(verified(monkeypatch,confidence=.99))
    assert_step(app,4)
    find_input(app,'Served Model ID').set_value('Qwen/Qwen3-32B').run(timeout=15)
    assert_step(app,1)
    assert not find_button(app,'Run Qwen 3 Verification').disabled
    assert not app.get('download_button')


def test_upload_column_options_survive_step_changes(monkeypatch):
    app=app_with_batch(source())
    find_input(app,'Prompt column (optional)').set_value('question').run(timeout=15)
    find_input(app,'Answer column (optional)').set_value('answer').run(timeout=15)
    next(x for x in app.selectbox if x.label=='Origin of unlabelled records').set_value('synthetic').run(timeout=15)
    transport_fixture(monkeypatch,confidence=.98)
    find_button(app,'Run Qwen 3 Verification').click().run(timeout=15)
    assert_step(app,4)
    find_button(app,'1. Bronze & Silver (Extract + MinHash)').click().run(timeout=15)
    assert_step(app,1)
    assert find_input(app,'Prompt column (optional)').value=='question'
    assert find_input(app,'Answer column (optional)').value=='answer'
    assert next(x for x in app.selectbox if x.label=='Origin of unlabelled records').value=='synthetic'
    assert len(app.session_state['batch']['records'])==1
