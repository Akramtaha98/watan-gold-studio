"""Real local HTTP compatibility tests; the mock itself uses only the stdlib."""
import copy
import io
import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

import mock_qwen as mock
import watan_gold_studio as w


@pytest.fixture(scope='module')
def api():
    server=mock.ThreadingHTTPServer(('127.0.0.1',0),mock.MockHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close();thread.join(timeout=5)


def call(api,path,payload=None):
    request=urllib.request.Request(api+path,data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=5) as reply:
        assert reply.headers['X-WaRiSAN-Mock']=='true'
        return json.load(reply)


def test_models_and_unknown_endpoint(api):
    assert [m['id'] for m in call(api,'/v1/models')['data']]==list(mock.MODELS)
    with pytest.raises(urllib.error.HTTPError) as error:call(api,'/v1/tokenize',{'prompt':'Hello'})
    assert error.value.code==404


@pytest.mark.parametrize('text',['Ayat Melayu.\n\nRuang  berganda\takhir.','توليسن جاوي 🇲🇾 é e\u0301',''])
def test_stateless_unicode_token_roundtrip(api,text):
    first=call(api,'/tokenize',{'prompt':text,'model':mock.MODELS[0]})
    second=call(api,'/tokenize',{'prompt':text,'model':mock.MODELS[1]})
    assert first['tokens']==second['tokens']==mock.encode(text)
    assert call(api,'/detokenize',{'tokens':first['tokens']})['prompt']==text
    assert mock.decode(mock.encode(text))==text


@pytest.mark.parametrize('tokens',[[True],[-1],[1114112],[0xD800],['a'],None])
def test_bad_tokens_return_400(api,tokens):
    with pytest.raises(urllib.error.HTTPError) as error:call(api,'/detokenize',{'tokens':tokens})
    assert error.value.code==400


@pytest.mark.parametrize('record,stage',[
    ({'text':'Perenggan Bahasa Melayu yang lengkap.'},'GOLD_1'),
    ({'text':'Rujukan pentadbiran.', 'doc_title':'Manual Contoh'},'GOLD_2'),
    ({'text':'Rujukan pentadbiran.', 'metadata':{'section':'Permohonan'}},'GOLD_2'),
    ({'messages':[{'role':'user','content':'Selamat pagi.'},{'role':'assistant','content':'Selamat pagi!'}]},'GOLD_3'),
    ({'turns':[{'role':'user','content':'Pilih ejaan: jurutera atau juru tera.'}],'reference_answer':'jurutera'},'GOLD_4'),
])
def test_strict_routes_and_clean_scores(record,stage):
    result=mock.verification(record)
    w.Draft202012Validator(w.RESULT_SCHEMA).validate(result)
    assert result['stage']==stage and result['confidence']==result['dbp_score']==.98
    assert not result['dbp_errors'] and not result['policy_errors'] and not result['three_r']


def test_default_ingest_title_is_not_a_reference_signal():
    batch=w.ingest_files([('ordinary.txt','Perenggan bersih yang lengkap.'.encode())])
    r=batch['records'][0]
    assert mock.verification({'text':r['text'],'metadata':r['meta']})['stage']=='GOLD_1'


def test_edits_preserve_system_user_turns_and_pii_placeholders():
    messages=[{'role':'system','content':'Urus <EMAIL> dan <PHONE> dengan selamat.'},{'role':'user','content':'Betulkan diatas; apakah Gemma4-31B? <IC>'},{'role':'assistant','content':'Gemma4-31B: rekod <IC> berada diatas meja. <EMAIL> <PHONE>'}]
    result=mock.verification({'messages':messages})
    assert result['messages'][:2]==messages[:2]
    assert result['messages'][-1]['content']=='google/gemma-4-31B-it: rekod <IC> berada di atas meja. <EMAIL> <PHONE>'
    w.validate_candidate(messages,result['messages'])
    assert result['confidence']==.85 and result['dbp_errors'] and result['policy_errors']
    assert messages[-1]['content'].startswith('Gemma4-31B')


def test_near_malay_and_three_r_flags():
    result=mock.verification({'text':'Silakan baca panduan karena topik ini melibatkan kaum, agama dan sultan.'})
    assert result['lang_near_malay'] and result['language']=='id'
    assert result['three_r']==['Race','Religion','Royalty'] and not result['unsafe']


@pytest.mark.parametrize('mode',['structured_outputs','json_object','guided_json'])
def test_real_dashboard_connection_and_modes(api,mode):
    config=w.QwenConfig(base_url=api+'/v1',model=mock.MODELS[1],mode=mode)
    policy=json.loads(w.POLICY_PATH.read_text())
    assert config.model in [m['id'] for m in w.request_json(config,'/models')['data']]
    messages=[{'role':'user','content':'Selamat pagi.'},{'role':'assistant','content':'Selamat pagi!'}]
    result=w.verify_with_qwen(messages,config,policy)
    assert result['messages']==messages and result['confidence']==.98


def test_actual_rag_tokenizer_chunk_bounds_and_overlap(api):
    tokenizer=w.VLLMTokenizer(w.QwenConfig(base_url=api+'/v1'))
    text=('Bahasa Melayu memperjelas maksud ayat.\n\n'*70).rstrip()
    chunks=w.semantic_chunks(text,tokenizer)
    original=tokenizer.encode(text)
    assert all(300<=c['tokens']<=600 for c in chunks)
    assert chunks[0]['start']==0 and chunks[-1]['end']==len(original)
    for chunk in chunks:assert tokenizer.encode(chunk['text'])==original[chunk['start']:chunk['end']]
    for left,right in zip(chunks,chunks[1:]):assert left['end']-right['start']==64


def test_full_dashboard_adapter_review_and_gold_release(api):
    config=w.QwenConfig(base_url=api+'/v1');policy=json.loads(w.POLICY_PATH.read_text())
    rows=[{'text':'Penulisan yang jelas memudahkan pembaca memahami hujah.','document_id':'canonical-doc'},
          {'text':'Permohonan bengkel hendaklah dihantar kepada urus setia.','doc_title':'Panduan Bengkel','section':'Permohonan','document_id':'rag-doc'},
          {'messages':[{'role':'user','content':'Betulkan ayat tentang kedudukan buku.'},{'role':'assistant','content':'Buku itu berada diatas meja.'}]},
          {'turns':[{'role':'user','content':'Pilih ejaan betul: jurutera atau juru tera.'}],'reference_answer':'jurutera','category':'DBP_Grammar','scoring_method':'MCQ','choices':['jurutera','juru tera']},
          {'text':'Silakan membaca teks karena ini ialah contoh bahasa serantau.','document_id':'near-malay'}]
    rows=[dict(r,licence_status='approved',allowed_uses=['train','rag_display','eval']) for r in rows]
    batch=w.ingest_files([('demo.jsonl',''.join(json.dumps(r)+'\n' for r in rows).encode())])
    for r in batch['records']:w.verify_record(r,config,policy)
    assert [r['status'] for r in batch['records']]==['AUTO_PASS','AUTO_PASS','PTSL_REVIEW','AUTO_PASS','PTSL_REVIEW']
    assert [r['stage'] for r in batch['records'][:4]]==['GOLD_1','GOLD_2','GOLD_3','GOLD_4']
    for r in batch['records']:
        if r['status']=='PTSL_REVIEW':w.human_decision(r,'APPROVE','Linguist',w.profile_hash(config,policy))
    files=w.build_exports(batch,config,policy)
    assert all(files[n] for n in ['gold_1_canonical_cpt.jsonl','gold_2_rag_chunks.jsonl','gold_3_sft_messages.jsonl','gold_4_eval_benchmark.jsonl'])
    assert b'di atas meja' in files['gold_3_sft_messages.jsonl']
    assert w.bundle_zip(files)


def test_real_streamlit_connection_button_uses_mock_http(api):
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_file(str(Path(w.__file__))).run(timeout=15)
    next(x for x in app.text_input if x.label=='API Base URL').set_value(api+'/v1').run(timeout=15)
    next(x for x in app.button if x.label=='Test Connection').click().run(timeout=15)
    assert not app.exception
    assert app.session_state['connection']['ok'] is True
    assert any('Connected' in x.value for x in app.markdown)
