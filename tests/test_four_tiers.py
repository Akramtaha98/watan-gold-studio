import copy
import io
import json
import random
import string
import sys
import zipfile
from pathlib import Path

import pytest
from docx import Document
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import watan_gold_studio as w

POLICY = json.loads(w.POLICY_PATH.read_text())
CONFIG = w.QwenConfig()


class WordTokenizer:
    """Deliberate test double; production never uses this approximation."""
    def __init__(self):
        self.tokens = {}
        self.words = {}
    def encode(self, text):
        result = []
        for word in text.split():
            if word not in self.tokens:
                n = len(self.tokens) + 1
                self.tokens[word] = n
                self.words[n] = word
            result.append(self.tokens[word])
        return result
    def decode(self, tokens):
        return ' '.join(self.words[x] for x in tokens)


def response(record, stage=None, **overrides):
    dialogue = record['kind'] == 'dialogue'
    value = {'explicit_content': False, 'dialect_flag': False, 'script': 'rumi', 'spelling_variant': 'original', 'language': 'ms', 'lang_near_malay': False, 'three_r': [], 'damaged_chars': False, 'confidence': .99, 'dbp_score': .98, 'dbp_errors': [], 'policy_errors': [], 'factual_errors': [], 'unsafe': False, 'corrupted': False, 'reason': 'Semakan selesai.', 'messages': record['original'] if dialogue else [], 'text': '' if dialogue else record['original_text'], 'stage': stage or ('GOLD_4' if record.get('eval_tag') else 'GOLD_3' if dialogue else 'GOLD_1'), 'category': 'General_BM_QA', 'domain': 'Bahasa Melayu'}
    value.update(overrides)
    return copy.deepcopy(value)


def ingest(rows):
    rows=[dict({'licence_status':'approved','allowed_uses':['train','rag_display','eval']},**r) for r in rows]
    return w.ingest_files([('silver.jsonl', ''.join(json.dumps(x, ensure_ascii=False)+'\n' for x in rows).encode())])


def approve_batch(monkeypatch, rows):
    b = ingest(rows)
    def fake(record, config, policy):
        source = record['meta'].get('source', '')
        stage = 'GOLD_2' if source == 'circular' else None
        return response(record, stage=stage)
    monkeypatch.setattr(w, 'verify_with_qwen', fake)
    for r in b['records']:
        if r['status'] == 'PENDING':
            w.verify_record(r, CONFIG, POLICY)
    return b


def dialogues(n=40):
    rng = random.Random(1337)
    rows = []
    for i in range(n):
        nonce = ''.join(rng.choices(string.ascii_letters, k=36))
        # Distinct synthetic prompts prevent accidental near-duplicate template leakage.
        rows.append({'prompt': f'Terangkan istilah rekaan {nonce}.', 'response': f'Ini istilah rekaan untuk ujian {nonce}.'})
    return rows


def test_prose_txt_and_docx_heading_and_table_extraction():
    b = w.ingest_files([('p.txt', 'Bahasa Melayu ialah bahasa kebangsaan.\n\nPenulisan yang jelas memudahkan pembacaan.'.encode())])
    assert len(b['records']) == 2 and all(r['kind'] == 'prose' for r in b['records'])
    doc = Document(); doc.add_heading('Peraturan', level=1); doc.add_paragraph('Permohonan dibuat secara bertulis.')
    table = doc.add_table(rows=1, cols=2); table.cell(0,0).text='Perkara'; table.cell(0,1).text='Syarat'
    doc.add_heading('Rayuan', level=1); doc.add_paragraph('Rayuan hendaklah disertakan bukti.')
    out = io.BytesIO(); doc.save(out)
    b = w.ingest_files([('reference.docx', out.getvalue())])
    assert [r['meta']['section'] for r in b['records']] == ['Peraturan', 'Rayuan']
    assert 'Perkara | Syarat' in b['records'][0]['text']
    assert len({w.document_group(r) for r in b['records']}) == 1


def test_pdf_text_and_blank_page_ocr_quarantine():
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter(); page = writer.add_blank_page(width=600, height=840)
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 40 800 Td (Peraturan permohonan) Tj 0 -20 Td (Permohonan hendaklah dibuat secara bertulis.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    out=io.BytesIO();writer.write(out)
    b = w.ingest_files([('policy.pdf', out.getvalue())])
    assert b['records'][0]['kind'] == 'prose'
    assert b['records'][0]['meta']['page'] == '1'
    writer = PdfWriter(); writer.add_blank_page(width=400, height=500); blank = io.BytesIO(); writer.write(blank)
    b = w.ingest_files([('scan.pdf', blank.getvalue())])
    assert b['records'][0]['status'] == 'QUARANTINE'
    assert 'OCR' in b['records'][0]['reason']


def test_eval_tag_duplicate_takes_precedence_and_conflict_never_wins():
    rows = [{'prompt':'Uji ejaan','response':'Ejaan tepat.'}, {'prompt':'Uji ejaan','response':'Ejaan tepat.','tags':['benchmark']}]
    b = ingest(rows)
    assert [r['status'] for r in b['records']] == ['DUPLICATE','PENDING']
    assert b['records'][1]['eval_tag']


def test_prose_forced_dialogue_is_quarantined_without_api_call(monkeypatch):
    b = ingest([{'text':'Perenggan Bahasa Melayu yang lengkap.'}])
    calls=[]
    monkeypatch.setattr(w, 'verify_with_qwen', lambda *a: calls.append(1))
    w.verify_record(b['records'][0], w.QwenConfig(target='GOLD_4'), POLICY)
    assert not calls and b['records'][0]['status']=='QUARANTINE'


@pytest.mark.parametrize('n',[300,600,601,700,900,1201,2000])
def test_model_token_chunk_bounds_overlap_and_full_coverage(n):
    tokenizer=WordTokenizer(); text=' '.join(f'perkataan{i}' for i in range(n))
    chunks=w.semantic_chunks(text,tokenizer)
    assert all(300<=c['tokens']<=600 for c in chunks)
    assert chunks[0]['start']==0 and chunks[-1]['end']==n
    for a,b in zip(chunks,chunks[1:]):
        assert a['end']-b['start']==64
        assert tokenizer.encode(a['text'])[-64:]==tokenizer.encode(b['text'])[:64]


def test_short_source_is_not_padded_or_fabricated():
    text='Sumber rujukan ringkas.'
    chunks=w.semantic_chunks(text,WordTokenizer())
    assert chunks==[{'text':text,'tokens':3,'start':0,'end':3,'short_source':True}]


def test_stratification_is_deterministic_and_within_fraction(monkeypatch):
    b=approve_batch(monkeypatch,dialogues(40))
    for i,r in enumerate(b['records']):
        r['category']=w.CATEGORIES[i%4]
    first=w.tier_plan(b['records'],holdout_enabled=True)
    second=w.tier_plan(list(reversed(b['records'])),holdout_enabled=True)
    assert .10<=first['holdout_ratio']<=.15
    assert {r['id'] for r in first['by_stage']['GOLD_4']}=={r['id'] for r in second['by_stage']['GOLD_4']}
    assert not first['missing_eval_categories']
    assert not first['excluded']


def test_cross_stage_near_prompt_contamination_removed(monkeypatch):
    rows=[{'prompt':'Bagaimanakah cara membuat permohonan rasmi secara bertulis?', 'response':'Hantar borang lengkap kepada urus setia.', 'tags':['eval']},
          {'text':'Bagaimanakah cara membuat permohonan rasmi secara bertulis? Hantar borang lengkap kepada urus setia.', 'source':'article'},
          {'text':'Bagaimanakah cara membuat permohonan rasmi secara bertulis? Sila patuhi pekeliling.', 'source':'circular'},
          {'prompt':'Bagaimanakah cara membuat permohonan rasmi secara bertulis sekarang?', 'response':'Hantar borang lengkap.'},
          {'text':'Penanaman padi memerlukan pengairan yang mencukupi.', 'source':'agriculture'}]
    b=approve_batch(monkeypatch,rows)
    with pytest.raises(ValueError,match='contamination'):
        w.build_exports(b,CONFIG,POLICY,tokenizer=WordTokenizer())


def test_final_leak_scan_catches_text_added_during_chunking(monkeypatch):
    rows=[{'prompt':'Apakah tanda penamat ayat tanya?', 'response':'Tanda soal.', 'tags':['eval']}, {'text':'Pekeliling ini menetapkan tatacara pentadbiran.', 'source':'circular'}]
    b=approve_batch(monkeypatch,rows)
    monkeypatch.setattr(w,'semantic_chunks',lambda *a,**kw:[{'text':'Apakah tanda penamat ayat tanya?', 'tokens':7,'start':0,'end':7,'short_source':True}])
    with pytest.raises(ValueError,match='contamination'):
        w.build_exports(b,CONFIG,POLICY,tokenizer=WordTokenizer())


def test_shared_document_group_is_held_out(monkeypatch):
    b=approve_batch(monkeypatch,[{'prompt':'Nama watak dalam petikan ini?', 'response':'Kenanga.', 'tags':['benchmark'], 'document_id':'DOC-X'}, {'text':'Maklumat lain yang berlainan sepenuhnya.', 'document_id':'DOC-X'}])
    p=w.tier_plan(b['records'])
    assert not p['by_stage']['GOLD_1']
    assert p['excluded'][0]['reason']=='held_out_source_lineage'


def test_all_five_files_exact_schemas_zip_and_manifest_hashes(monkeypatch):
    rows=dialogues(40)+[{'text':'Penulisan yang tersusun membantu pembaca memahami hujah akademik.', 'source':'journal', 'license':'CC-BY-4.0'}, {'text':'Tatacara permohonan hendaklah mematuhi garis panduan yang diluluskan.', 'source':'circular', 'doc_title':'Tatacara', 'section':'Permohonan', 'year':'2025'}]
    b=approve_batch(monkeypatch,rows)
    files=w.build_exports(b,CONFIG,POLICY,tokenizer=WordTokenizer())
    assert set(w.FILENAMES).issubset(files)
    manifest=json.loads(files['manifest.json'])
    assert set(json.loads(files['gold_1_canonical_cpt.jsonl'].decode().splitlines()[0]))==set(w.GOLD_SCHEMAS['GOLD_1']['properties'])
    assert set(json.loads(files['gold_2_rag_chunks.jsonl']))==set(w.GOLD_SCHEMAS['GOLD_2']['properties'])
    assert files['gold_4_eval_benchmark.jsonl']==b''
    assert 'gold_3_sft_conversations.jsonl' not in files
    for name,info in manifest['files'].items():assert w.sha256(files[name])==info['sha256']
    with zipfile.ZipFile(io.BytesIO(w.bundle_zip(files))) as archive:
        assert set(archive.namelist())==set(files)


def test_human_stage_reassignment_and_benchmark_pinning(monkeypatch):
    b=ingest([{'text':'Perenggan rujukan dalam Bahasa Melayu.'}]); r=b['records'][0]
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*a:response(record,confidence=.9))
    w.verify_record(r,CONFIG,POLICY)
    w.human_decision(r,'APPROVE','Linguist',w.profile_hash(CONFIG,POLICY),stage='GOLD_2')
    assert r['stage']=='GOLD_2' and r['status']=='HUMAN_APPROVED'
    b=ingest([{'prompt':'Ujian identiti?', 'response':'WaRiSAN.', 'tags':'eval'}]);r=b['records'][0]
    w.verify_record(r,CONFIG,POLICY)
    with pytest.raises(ValueError,match='benchmark'):
        w.human_decision(r,'APPROVE','Linguist',w.profile_hash(CONFIG,POLICY),stage='GOLD_3')


def test_tokenizer_endpoints_are_root_paths_and_fail_closed(monkeypatch):
    seen=[]
    def fake(config,path,payload=None,root=False):
        seen.append((path,payload,root))
        return {'tokens':[1,2,3]} if path=='/tokenize' else {'prompt':'Teks contoh ringkas.'}
    monkeypatch.setattr(w,'request_json',fake)
    tokenizer=w.VLLMTokenizer(CONFIG)
    assert tokenizer.encode('Teks contoh ringkas.')==[1,2,3]
    assert tokenizer.decode([1,2,3])=='Teks contoh ringkas.'
    assert all(x[2] for x in seen)
    assert seen[0][1]['add_special_tokens'] is False
    monkeypatch.setattr(w,'request_json',lambda *a,**k:{'tokens':['approximate']})
    with pytest.raises(w.VerificationError):
        w.VLLMTokenizer(CONFIG).encode('blocked')


def test_preflight_masks_metadata_or_blocks_direct_transmission(monkeypatch):
    b=ingest([{'text':'Perenggan tanpa maklumat peribadi.', 'source':'siti@example.my'}])
    assert b['records'][0]['meta']['source']=='<EMAIL>'
    record=b['records'][0]; record['meta']['source']='siti@example.my'
    called=[]
    monkeypatch.setattr(w.requests,'request',lambda *a,**k:called.append(True))
    with pytest.raises(ValueError,match='transmission blocked'):
        w.verify_with_qwen(record,CONFIG,POLICY)
    assert not called


def test_canonical_qa_rejected_and_single_word_near_duplicate_detected():
    record=ingest([{'text':'Soalan: Apakah ejaan betul?\nJawapan: Kerjasama.'}])['records'][0]
    with pytest.raises(ValueError,match='dialogue/Q&A'):
        w.validate_result(record,response(record,stage='GOLD_1'))
    assert w.overlap_reason('pentadbiran','pentadbiram')=='character_similarity_0.90'
