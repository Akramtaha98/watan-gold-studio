import copy
import hashlib
import hmac
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

import watan_gold_studio as w
from analytics import summarize
from ptsl_api import create_app, deliver_pending
from storage import ConflictError, Storage, database_url, outbox
from test_four_tiers import CONFIG, POLICY, approve_batch, dialogues, ingest, response
from test_streamlit import APP, find_button

TOKEN='local-test-token-'+'x'*40


def create(store,monkeypatch,rows=None,confidence=.9,**flags):
    batch=ingest(rows or dialogues(2))
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*a:response(record,confidence=confidence,**flags))
    for r in batch['records']:w.verify_record(r,CONFIG,POLICY)
    context={'config':dict(CONFIG.public(),api_key='SHOULD-NEVER-BE-STORED'), 'policy':POLICY,'gold_version':'v0.1'}
    batch_id,revision=store.create_batch(batch,context)
    return batch_id,batch,revision,context


def submit_item(item,verdict='Good',role='Linguist',**kwargs):
    return {'record_id':item['record_id'],'expected_revision':item['expected_revision'],'verdict':verdict,'reviewer_role':role,**kwargs}


def client(store,roles=None):
    return TestClient(create_app(store,token=TOKEN,allowed_roles=roles))


def auth():return {'Authorization':'Bearer '+TOKEN}


def test_sqlite_survives_new_store_and_only_masks_are_persisted(tmp_path,monkeypatch):
    store=Storage()
    batch_id,batch,revision,context=create(store,monkeypatch,[{'prompt':'Masukkan siti@example.my dalam latihan.','response':'Maklumat dirahsiakan.'}])
    store.close();reopened=Storage()
    restored=reopened.load_batch(batch_id)
    assert restored['snapshot']==batch
    assert '<EMAIL>' in restored['snapshot']['records'][0]['original'][0]['content']
    assert 'siti@example.my' not in json.dumps(restored)
    assert 'SHOULD-NEVER-BE-STORED' not in json.dumps(restored)
    assert reopened.queue(batch_id) and reopened.status(batch_id)['remaining']==1


def test_unsafe_persistence_is_rejected(monkeypatch):
    store=Storage();batch_id,batch,revision,context=create(store,monkeypatch)
    batch['records'][0]['candidate'][-1]['content']='Hubungi real@example.my.'
    with pytest.raises(ValueError):store.save_batch(batch_id,batch,context,revision)
    assert store.load_batch(batch_id)['revision']==revision


def test_postgres_urls_are_adapter_ready():
    assert database_url('postgres://user:pass@host/db').startswith('postgresql+psycopg://')
    assert database_url('postgresql://user:pass@host/db').startswith('postgresql+psycopg://')
    with pytest.raises(ValueError):database_url('https://project.supabase.co')


def test_queue_bearer_auth_and_strict_masks(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch,[{'prompt':'Gunakan fake@example.com dalam latihan.','response':'Alamat ujian disamarkan.','origin_type':'synthetic','allowlist_synthetic_pii':True}])
    with client(store) as api:
        assert api.get('/api/ptsl/queue').status_code==401
        assert api.get('/api/ptsl/status',headers={'Authorization':'Bearer invalid'}).status_code==401
        reply=api.get('/api/ptsl/queue',headers=auth())
        assert reply.status_code==200 and reply.headers['Cache-Control']=='no-store'
        item=reply.json()['items'][0]
        assert 'fake@example.com' not in reply.text and '<EMAIL>' in item['masked_text']
        assert item['record_id'].startswith(batch_id+':') and item['expected_revision']==1
        assert set(item)>={'masked_text','qwen_suggestion','reason','stage','metadata'}


def test_atomic_batch_submission_and_utc_audit(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch)
    with client(store) as api:
        items=api.get('/api/ptsl/queue',headers=auth()).json()['items']
        decisions=[submit_item(items[0],reviewer_notes='Contact siti@example.my'),submit_item(items[1],'Bad')]
        reply=api.post('/api/ptsl/submit',headers=auth(),json=decisions)
        assert reply.status_code==200 and reply.json()['accepted']==2
        progress=api.get('/api/ptsl/status',headers=auth(),params={'batch_id':batch_id}).json()
        assert progress['remaining']==0 and progress['completed']==2 and progress['ready_for_release']
        restored=store.load_batch(batch_id)['snapshot']['records']
        assert [r['status'] for r in restored]==['HUMAN_APPROVED','QUARANTINE']
        for r in restored:
            h=r['history'][-1]
            assert h['channel']=='PTSL_API' and h['reviewer_role']=='Linguist' and h['timestamp'].endswith('+00:00')
        assert 'siti@example.my' not in json.dumps(restored)
        assert api.post('/api/ptsl/submit',headers=auth(),json=decisions).status_code==409


def test_bad_second_verdict_rolls_back_everything(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch)
    before=store.load_batch(batch_id)
    with client(store) as api:
        items=api.get('/api/ptsl/queue',headers=auth()).json()['items']
        bad=copy.deepcopy(before['snapshot']['records'][1]['candidate']);bad[0]['content']='User turn replaced'
        reply=api.post('/api/ptsl/submit',headers=auth(),json=[submit_item(items[0]),submit_item(items[1],edited_text=bad)])
        assert reply.status_code==422
    assert store.load_batch(batch_id)==before


def test_api_role_scope_and_existing_mutable_fact_rules(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch,[{'prompt':'Siapa membiayai projek?','response':'KPT menyediakan pembiayaan projek.'}])
    item=store.queue(batch_id)[0]
    with client(store) as api:
        assert api.post('/api/ptsl/submit',headers=auth(),json=submit_item(item,role='Project_Lead')).status_code==403
        assert api.post('/api/ptsl/submit',headers=auth(),json=submit_item(item)).status_code==422
    with client(store,roles={'Project_Lead'}) as api:
        assert api.post('/api/ptsl/submit',headers=auth(),json=submit_item(item,role='Project_Lead')).status_code==200


def test_unsure_keeps_queue_and_edited_assistant_is_validated(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch,rows=dialogues(1))
    with client(store) as api:
        item=store.queue(batch_id)[0]
        assert api.post('/api/ptsl/submit',headers=auth(),json=submit_item(item,'Unsure')).status_code==200
        assert store.status(batch_id)['remaining']==1
        item=store.queue(batch_id)[0]
        assert api.post('/api/ptsl/submit',headers=auth(),json=submit_item(item,edited_text='Hubungi real@example.my.')).status_code==422
        assert api.post('/api/ptsl/submit',headers=auth(),json=submit_item(item,edited_text='Jawapan yang disemak oleh ahli bahasa.')).status_code==200
        assert store.load_batch(batch_id)['snapshot']['records'][0]['candidate'][-1]['content']=='Jawapan yang disemak oleh ahli bahasa.'


def test_validation_errors_do_not_echo_private_payloads(monkeypatch):
    store=Storage();create(store,monkeypatch)
    with client(store) as api:
        reply=api.post('/api/ptsl/submit',headers=auth(),json={'record_id':'real@example.my','verdict':'secret','reviewer_role':'admin'})
        assert reply.status_code==422 and 'real@example.my' not in reply.text and 'secret' not in reply.text
        assert api.post('/api/ptsl/submit',headers=auth(),content='x'*(2*1024*1024+1)).status_code==413


def test_unconfigured_auth_fails_closed():
    with pytest.raises(ValueError,match='PTSL_API_TOKEN'):
        with TestClient(create_app(Storage(),token='')):pass


def test_conflicts_and_concurrent_verdicts_cannot_lose_updates(monkeypatch):
    store=Storage();batch_id,batch,revision,context=create(store,monkeypatch,rows=dialogues(1))
    item=store.queue(batch_id)[0]
    def decide(_):
        try:store.submit([submit_item(item)]);return 'accepted'
        except ConflictError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(decide,range(2)))
    assert sorted(results)==['accepted','conflict']
    with pytest.raises(ConflictError):store.save_batch(batch_id,batch,context,revision)
    assert store.load_batch(batch_id)['snapshot']['records'][0]['status']=='HUMAN_APPROVED'


def test_release_history_jsonl_default_and_full_governance(tmp_path,monkeypatch):
    store=Storage();batch_id,batch,revision,context=create(store,monkeypatch,confidence=.99)
    job=w.ReleaseJob(batch,CONFIG,POLICY,store=store,batch_id=batch_id,expected_revision=revision)
    archive=job.get_zip()
    with zipfile.ZipFile(io.BytesIO(archive)) as z:assert set(z.namelist())==set(w.FILENAMES)
    history=store.list_releases();assert len(history)==1
    key=history[0]['key']
    assert (store.release_root/key/'gold_3_sft_messages.jsonl').is_file()
    restored=Storage().release_files(key)
    assert restored==job.files and 'gold_3_sft.parquet' in restored
    full=w.ReleaseJob(batch,CONFIG,POLICY,include_governance=True)
    with zipfile.ZipFile(io.BytesIO(full.get_zip())) as z:assert 'checksums.sha256' in z.namelist() and 'gold_3_sft.parquet' in z.namelist()
    # Missing mirror files are restored from the checksum-verified DB copy.
    (store.release_root/key/'gold_3_sft_messages.jsonl').unlink()
    store.materialize_release(key,restored)
    assert (store.release_root/key/'gold_3_sft_messages.jsonl').read_bytes()==restored['gold_3_sft_messages.jsonl']


def test_release_rejects_a_changed_batch(monkeypatch):
    store=Storage();batch_id,batch,revision,context=create(store,monkeypatch,confidence=.99)
    changed=copy.deepcopy(batch);changed['issues'].append('An external update')
    store.save_batch(batch_id,changed,context,revision)
    job=w.ReleaseJob(batch,CONFIG,POLICY,store=store,batch_id=batch_id,expected_revision=revision)
    with pytest.raises(ValueError,match='changed during release'):job.get_zip()
    assert not store.list_releases()


def test_restart_ui_restore_and_external_submission_advances(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch,rows=dialogues(1))
    app=AppTest.from_file(str(APP)).run(timeout=20)
    assert not app.exception and app.session_state['active_step']==2
    assert app.session_state['batch_id']==batch_id
    item=store.queue(batch_id)[0]
    with client(store) as api:assert api.post('/api/ptsl/submit',headers=auth(),json=submit_item(item)).status_code==200
    app.run(timeout=20)
    assert not app.exception and app.session_state['active_step']==3
    assert app.session_state['batch']['records'][0]['status']=='HUMAN_APPROVED'
    assert any(x.label.startswith('Validate & Download') for x in app.get('download_button'))
    app.session_state['release_job'].get_zip();app.run(timeout=20)
    assert not app.exception
    # A brand-new Streamlit session restores the same persisted state and history.
    new=AppTest.from_file(str(APP)).run(timeout=20)
    assert new.session_state['batch_id']==batch_id and new.session_state['active_step']==3
    next(x for x in new.toggle if x.label=='Release History').set_value(True).run(timeout=20)
    assert not new.exception and any(x.label=='Download saved release (.zip)' for x in new.get('download_button'))
    find_button(new,'Start new batch').click().run(timeout=20)
    assert not new.exception and new.session_state['active_step']==1
    assert not next(x for x in new.toggle if x.label=='Release History').value


def test_excel_pack_survives_store_recreation(monkeypatch):
    from openpyxl import load_workbook
    store=Storage();batch_id,batch,revision,context=create(store,monkeypatch,rows=dialogues(1))
    pack=w.export_review_pack(batch,w.profile_hash(CONFIG,POLICY))
    store.save_batch(batch_id,batch,context,revision)
    store.close();store=Storage();saved=store.load_batch(batch_id)
    book=load_workbook(io.BytesIO(pack));book['PTSL Review']['F2']='Good';out=io.BytesIO();book.save(out)
    assert w.import_review_pack(saved['snapshot'],out.getvalue(),'Linguist',w.profile_hash(CONFIG,POLICY))=={'Good':1}
    store.save_batch(batch_id,saved['snapshot'],saved['context'],saved['revision'])
    assert store.status(batch_id)['remaining']==0


def test_signed_webhook_delivery_and_retry_are_persistent(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch)
    secret='s'*48;sent=[]
    def send(url,**kwargs):sent.append(kwargs);return SimpleNamespace(status_code=200)
    assert deliver_pending(store,'https://portal.example/webhook',secret,sender=send)==1
    header=sent[0]['headers']['X-WaRiSAN-Signature'];timestamp,signature=header.split(',')
    assert hmac.compare_digest(signature[3:],hmac.new(secret.encode(),timestamp[2:].encode()+b'.'+sent[0]['data'],hashlib.sha256).hexdigest())
    assert b'candidate' not in sent[0]['data'] and b'original' not in sent[0]['data']
    # Another event fails: do not mark delivered or drop it after restart.
    item=store.queue(batch_id)[0];store.submit([submit_item(item)])
    assert deliver_pending(store,'https://portal.example/webhook',secret,sender=lambda *a,**kw:SimpleNamespace(status_code=503))==0
    with Storage().engine.connect() as conn:
        pending=conn.execute(select(outbox).where(outbox.c.event_type=='ptsl.reviewed')).mappings().one()
        assert pending['status']=='pending' and pending['attempts']==1 and pending['next_attempt']>0


def test_analytics_counts_and_histogram_are_aggregate_only(monkeypatch):
    batch=approve_batch(monkeypatch,[*dialogues(1),{'text':'Perenggan contoh.'},{'prompt':'Terjemahan','response':'Selamat pagi.','origin_type':'synthetic'}])
    result=summarize(batch['records'])
    assert result['tiers']['Gold 1']==1 and result['tiers']['Gold 3 (Human)']==1 and result['tiers']['Gold 3 (Synthetic)']==1
    assert result['quality_gate']['AUTO_PASS']==3 and sum(result['dbp_histogram'].values())==3
    assert 'Perenggan contoh' not in json.dumps(result)


def test_status_not_ready_when_public_verifier_profile_changes(monkeypatch):
    store=Storage();batch_id,batch,revision,context=create(store,monkeypatch,confidence=.99)
    context['config']['model']='Qwen/Qwen3-32B'
    store.save_batch(batch_id,batch,context,revision)
    assert not store.status(batch_id)['ready_for_release']


def test_new_batch_and_saved_batch_selection_do_not_erase_history(monkeypatch):
    store=Storage();batch_id,*_=create(store,monkeypatch,rows=dialogues(1),confidence=.99)
    app=AppTest.from_file(str(APP)).run(timeout=20)
    find_button(app,'Start new batch').click().run(timeout=20)
    assert not app.exception and app.session_state['active_step']==1
    assert not app.session_state['batch']['records']
    assert store.load_batch(batch_id)['snapshot']['records']
    next(x for x in app.selectbox if x.label=='Saved batch').set_value(batch_id).run(timeout=20)
    assert not app.exception and app.session_state['batch_id']==batch_id and app.session_state['batch']['records']
    # A second browser still sees the authoritative saved batch.
    other=AppTest.from_file(str(APP)).run(timeout=20)
    assert other.session_state['batch_id']==batch_id


def test_production_workspace_gate_fails_closed(monkeypatch):
    monkeypatch.setenv('STUDIO_ENV','production')
    app=AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    assert any('STUDIO_ACCESS_TOKEN' in x.value for x in app.error)
    assert not app.get('file_uploader')


def test_workspace_access_secret_is_never_persisted(monkeypatch):
    secret='workspace-secret-'+('z'*40)
    monkeypatch.setenv('STUDIO_ACCESS_TOKEN',secret)
    app=AppTest.from_file(str(APP)).run(timeout=15)
    next(x for x in app.text_input if x.label=='Workspace access token').set_value(secret)
    find_button(app,'Open workspace').click().run(timeout=15)
    assert not app.exception
    assert any(x.label=='Run Qwen 3 Verification' for x in app.button)
    assert not any(x.label=='Workspace access token' for x in app.text_input)


def test_api_unknown_batch_and_strict_role_policy(monkeypatch):
    store=Storage();create(store,monkeypatch)
    with client(store) as api:
        assert api.get('/api/ptsl/queue',params={'batch_id':'0'*32},headers=auth()).status_code==404
    with pytest.raises(ValueError):create_app(store,token=TOKEN,allowed_roles=set())


def test_postgresql_adapter_with_real_ci_database(monkeypatch):
    import os
    url=os.getenv('TEST_POSTGRES_URL')
    if not url:pytest.skip('TEST_POSTGRES_URL not set; CI provides an isolated PostgreSQL service.')
    store=Storage(url)
    batch_id,batch,revision,context=create(store,monkeypatch,rows=dialogues(1))
    item=store.queue(batch_id)[0]
    store.submit([submit_item(item)])
    assert Storage(url).load_batch(batch_id)['snapshot']['records'][0]['status']=='HUMAN_APPROVED'
    with pytest.raises(ConflictError):store.save_batch(batch_id,batch,context,revision)
    store.close()


def test_consistent_sqlite_backup_can_be_reopened(tmp_path,monkeypatch):
    import sys
    import manage
    store=Storage();batch_id,*_=create(store,monkeypatch,rows=dialogues(1))
    backup=tmp_path/'private-backup.db'
    monkeypatch.setattr(sys,'argv',['manage.py','backup','--output',str(backup)])
    manage.main()
    restored=Storage(f'sqlite:///{backup}')
    assert restored.load_batch(batch_id)['snapshot']==store.load_batch(batch_id)['snapshot']
    assert restored.queue(batch_id)[0]['expected_revision']==store.queue(batch_id)[0]['expected_revision']
    assert backup.stat().st_mode & 0o777==0o600
    with pytest.raises(SystemExit):manage.main()
