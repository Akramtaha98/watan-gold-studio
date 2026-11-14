import copy
import json
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import watan_gold_studio as w

APP = Path(__file__).resolve().parents[1] / 'watan_gold_studio.py'
POLICY = json.loads(w.POLICY_PATH.read_text())
PAIR = [{'role': 'user', 'content': 'Apakah fungsi tanda soal?'}, {'role': 'assistant', 'content': 'Tanda soal menandakan ayat tanya.'}]


def source():
    return w.ingest_files([('input.jsonl', json.dumps({'messages': PAIR, 'licence_status':'approved', 'allowed_uses':['train','rag_display','eval']}).encode())])


def find_button(app, label):
    return next(b for b in app.button if b.label == label)


def find_input(app, label):
    return next(t for t in app.text_input if t.label == label)


def result(m, confidence=.99, **kw):
    out = {'explicit_content': False, 'dialect_flag': False, 'script': 'rumi', 'spelling_variant': 'original', 'language': 'ms', 'lang_near_malay': False, 'three_r': [], 'damaged_chars': False, 'confidence': confidence, 'dbp_score': .99, 'stage': 'GOLD_3', 'category': 'General_BM_QA', 'domain': 'Malay', 'text': '', 'dbp_errors': [], 'policy_errors': [], 'factual_errors': [], 'unsafe': False, 'corrupted': False, 'reason': 'Semakan selesai.', 'messages': copy.deepcopy(m)}
    out.update(kw)
    return out


def test_empty_first_wizard_step():
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    assert not app.tabs
    assert app.session_state['active_step']==1
    assert find_button(app, '2. PTSL Human Review').disabled
    assert find_button(app, '3. Validate & Export').disabled
    assert find_button(app, 'Run Qwen 3 Verification').disabled
    assert not app.get('download_button')
    assert [m.value for m in app.metric] == ['0', '0.0%', '0', '0', '0', '0', '0', '0']


def test_run_real_button_to_exact_downloads_with_mock_transport(monkeypatch):
    calls = []
    class Response:
        status_code = 200
        def __init__(self, payload):
            self.text = json.dumps(payload)
    def transport(method, url, **kw):
        calls.append(kw['json'])
        m = json.loads(kw['json']['messages'][1]['content'])['corpus_record']['messages']
        return Response({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result(m))}}]})
    monkeypatch.setattr(w.requests, 'request', transport)
    app = AppTest.from_file(str(APP))
    app.session_state['batch'] = source()
    app.run(timeout=15)
    find_button(app, 'Run Qwen 3 Verification').click().run(timeout=15)
    assert not app.exception
    assert calls and calls[0]['chat_template_kwargs']['enable_thinking'] is False
    assert app.session_state['batch']['records'][0]['status'] == 'AUTO_PASS'
    assert [m.value for m in app.metric] == ['1', '100.0%', '0', '0', '0', '0', '1', '0']
    app.session_state['release_job'].get_zip()
    app.run(timeout=15)
    assert not app.exception
    files = app.session_state['release_job'].files
    assert set(w.FILENAMES).issubset(files)
    assert len(app.get('download_button')) == 6


def review_app(confidence=.9, **flags):
    b = source()
    r = b['records'][0]
    r.update(stage='GOLD_3', status='PTSL_REVIEW', reason='Perlu semakan.', verification={'result': result(PAIR, confidence=confidence, **flags), 'profile_sha256': w.profile_hash(w.QwenConfig(), POLICY), 'config': w.QwenConfig().public(), 'policy_sha256': w.sha256(w.canonical_json(POLICY)), 'verified_at': w.now()})
    app = AppTest.from_file(str(APP))
    app.session_state['batch'] = b
    app.run(timeout=15)
    next(s for s in app.selectbox if s.label=='Reviewer Role').set_value('Linguist').run(timeout=15)
    return app


def test_single_approve_and_quarantine():
    app = review_app()
    assert not app.exception
    assert not find_button(app, 'Approve').disabled
    find_button(app, 'Approve').click().run(timeout=15)
    assert not app.exception
    assert app.session_state['batch']['records'][0]['status'] == 'HUMAN_APPROVED'
    app = review_app()
    find_button(app, 'Quarantine').click().run(timeout=15)
    assert not app.exception
    assert app.session_state['batch']['records'][0]['status'] == 'QUARANTINE'
    assert not any(t.label == 'Review record' for t in app.selectbox)


def test_bulk_approval_and_bad_edit_blocking():
    app = review_app(.99, dbp_errors=['Ejaan'])
    find_button(app, 'Approve All High-Confidence').click().run(timeout=15)
    assert not app.exception
    assert app.session_state['batch']['records'][0]['status'] == 'HUMAN_APPROVED'
    app = review_app()
    find_button(app, 'Edit & Approve').click().run(timeout=15)
    editor = next(t for t in app.text_area if t.label == 'Candidate messages JSON')
    changed = copy.deepcopy(PAIR); changed[0]['content'] = 'Modified user'
    editor.set_value(json.dumps(changed))
    find_button(app, 'Save edit & approve').click().run(timeout=15)
    assert not app.exception
    assert app.session_state['batch']['records'][0]['status'] == 'PTSL_REVIEW'
    assert any('Approval blocked' in e.value for e in app.error)


def test_stage_filter_reassignment_and_explicit_benchmark_pin():
    app = review_app()
    next(s for s in app.get('button_group') if s.label == 'Stage filter').set_value('Gold 1').run(timeout=15)
    assert not any(s.label == 'Review record' for s in app.selectbox)
    next(s for s in app.get('button_group') if s.label == 'Stage filter').set_value('Gold 3').run(timeout=15)
    next(s for s in app.selectbox if s.label == 'Assign Gold tier').set_value('GOLD_4').run(timeout=15)
    find_button(app, 'Approve').click().run(timeout=15)
    assert not app.exception
    assert app.session_state['batch']['records'][0]['stage'] == 'GOLD_4'


def test_all_tier_ui_exports_via_mocked_openai_compatible_transport(monkeypatch):
    import random
    import string
    rng = random.Random(44)
    rows = [{'prompt': 'Takrifkan istilah rekaan ' + ''.join(rng.choices(string.ascii_letters, k=40)), 'response': 'Ini kandungan ujian sintetik.'} for _ in range(40)]
    rows += [{'text':'Penulisan akademik yang jelas memudahkan pemahaman pembaca.', 'source':'article'}, {'text':'Permohonan perlu disertakan dokumen sokongan yang lengkap.', 'source':'circular'}]
    batch = w.ingest_files([('mixed.jsonl', ''.join(json.dumps(r)+'\n' for r in rows).encode())])
    dictionary, reverse, requests_seen = {}, {}, []
    class Response:
        status_code = 200
        def __init__(self, value):
            self.text = json.dumps(value)
    def transport(method, url, **kw):
        payload = kw['json']; requests_seen.append(url)
        if url.endswith('/tokenize'):
            tokens=[]
            for word in payload['prompt'].split():
                if word not in dictionary:
                    n=len(dictionary)+1; dictionary[word]=n; reverse[n]=word
                tokens.append(dictionary[word])
            return Response({'tokens':tokens})
        if url.endswith('/detokenize'):
            return Response({'prompt':' '.join(reverse[t] for t in payload['tokens'])})
        r = json.loads(payload['messages'][1]['content'])['corpus_record']
        out = result(r['messages'])
        if r['kind']=='prose':
            out.update(messages=[],text=r['text'],stage='GOLD_2' if r['metadata']['source']=='circular' else 'GOLD_1')
        return Response({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(out)}}]})
    monkeypatch.setattr(w.requests,'request',transport)
    app=AppTest.from_file(str(APP)); app.session_state['batch']=batch; app.run(timeout=15)
    find_button(app,'Run Qwen 3 Verification').click().run(timeout=20)
    assert not app.exception
    app.session_state['release_job'].get_zip()
    app.run(timeout=20)
    assert not app.exception
    assert len(app.get('download_button'))==6
    files=app.session_state['release_job'].files
    manifest=json.loads(files['manifest.json'])
    assert manifest['row_counts']['gold_4_eval.parquet']==0
    assert any(url.endswith('/tokenize') for url in requests_seen)
    assert all('api_key' not in name for name in manifest)
