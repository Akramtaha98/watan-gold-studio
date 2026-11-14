"""Recover stale/deleted saved-batch references without deleting any test data."""
import copy

from streamlit.testing.v1 import AppTest

from storage import Storage
from test_persistence_api import create
from test_four_tiers import dialogues
from test_streamlit import APP, find_button


def test_stale_saved_batch_callback_keeps_the_healthy_active_workspace(monkeypatch):
    store=Storage()
    missing,*_=create(store,monkeypatch,rows=dialogues(1),confidence=.99)
    current,*_=create(store,monkeypatch,rows=dialogues(2),confidence=.99)
    app=AppTest.from_file(str(APP)).run(timeout=20)
    assert not app.exception and app.session_state['batch_id']==current
    original_records=copy.deepcopy(app.session_state['batch']['records'])
    original_load=Storage.load_batch
    def stale(self,batch_id):
        if batch_id==missing:raise KeyError('Batch not found')
        return original_load(self,batch_id)
    monkeypatch.setattr(Storage,'load_batch',stale)
    next(x for x in app.selectbox if x.label=='Saved batch').set_value(missing).run(timeout=20)
    assert not app.exception
    assert app.session_state['batch_id']==current
    assert app.session_state['batch']['records']==original_records
    assert next(x for x in app.selectbox if x.label=='Saved batch').value==current
    assert any('not available' in x.value for x in app.warning)
    assert len(store.list_batches())==2


def test_missing_active_batch_preserves_records_until_explicit_recovery(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch,rows=dialogues(1),confidence=.99)
    app=AppTest.from_file(str(APP)).run(timeout=20)
    assert not app.exception
    snapshot=copy.deepcopy(app.session_state['batch']['records'])
    original_load=Storage.load_batch
    def missing(self,key):
        if key==batch_id:raise KeyError('Batch not found')
        return original_load(self,key)
    monkeypatch.setattr(Storage,'load_batch',missing)
    app.run(timeout=20)
    assert not app.exception
    assert app.session_state['batch_id'] is None
    assert app.session_state['batch']['records']==snapshot
    assert app.session_state['recovery_required']
    assert not app.get('download_button')
    assert len(store.list_batches())==1  # no implicit duplicate/recovery write
    monkeypatch.setattr(Storage,'load_batch',original_load)
    find_button(app,'Save recovered workspace as a new batch').click().run(timeout=20)
    assert not app.exception and not app.session_state['recovery_required']
    recovered=app.session_state['batch_id']
    assert recovered!=batch_id and len(store.list_batches())==2
    assert store.load_batch(recovered)['snapshot']['records']==snapshot
    assert store.load_batch(batch_id)['snapshot']['records']==snapshot


def test_missing_active_reference_without_records_returns_to_empty_upload(monkeypatch):
    app=AppTest.from_file(str(APP))
    app.session_state['persistence_initialized']=True
    app.session_state['batch']={'records':[],'sources':[],'issues':[]}
    app.session_state['batch_id']='a'*32
    app.session_state['db_revision']=1
    app.run(timeout=20)
    assert not app.exception
    assert app.session_state['batch_id'] is None and app.session_state['active_step']==1
    assert find_button(app,'Run Qwen 3 Verification').disabled
    assert not Storage().list_batches()
    assert any('not available' in x.value for x in app.warning)


def test_stale_initial_autoload_in_reviewer_mode_does_not_rerun_forever(monkeypatch):
    store=Storage();create(store,monkeypatch,rows=dialogues(1))
    def missing(*args):raise KeyError('Batch not found')
    monkeypatch.setattr(Storage,'load_batch',missing)
    app=AppTest.from_file(str(APP));app.query_params['mode']='ptsl'
    app.run(timeout=20)
    assert not app.exception
    assert not app.session_state['batch']['records']
    assert any('Waiting for records' in x.value for x in app.info)
    assert len(store.list_batches())==1


def test_portal_status_race_recovers_without_a_traceback(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch,rows=dialogues(1))
    app=AppTest.from_file(str(APP)).run(timeout=20)
    assert not app.exception and app.session_state['active_step']==2
    snapshot=copy.deepcopy(app.session_state['batch']['records'])
    def missing(*args):raise KeyError('Batch not found')
    monkeypatch.setattr(Storage,'status',missing)
    app.run(timeout=20)
    assert not app.exception and app.session_state['recovery_required']
    assert app.session_state['batch']['records']==snapshot
    assert len(store.list_batches())==1


def test_missing_during_review_save_preserves_the_unsaved_decision(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch,rows=dialogues(1))
    app=AppTest.from_file(str(APP)).run(timeout=20)
    assert not app.exception
    original_save=Storage.save_batch
    def disappeared(*args,**kwargs):raise KeyError('Batch not found')
    monkeypatch.setattr(Storage,'save_batch',disappeared)
    find_button(app,'Approve').click().run(timeout=20)
    assert not app.exception and app.session_state['recovery_required']
    assert app.session_state['batch']['records'][0]['status']=='HUMAN_APPROVED'
    assert store.load_batch(batch_id)['snapshot']['records'][0]['status']=='PTSL_REVIEW'
    monkeypatch.setattr(Storage,'save_batch',original_save)
    find_button(app,'Save recovered workspace as a new batch').click().run(timeout=20)
    assert not app.exception
    assert store.load_batch(app.session_state['batch_id'])['snapshot']['records'][0]['status']=='HUMAN_APPROVED'
