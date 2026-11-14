import copy
import io
import json
import sys
import zipfile
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import watan_gold_studio as w
from test_four_tiers import POLICY, CONFIG, WordTokenizer, response, ingest, approve_batch, dialogues


def review_batch(monkeypatch, n=3):
    batch=ingest(dialogues(n))
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*a:response(record,confidence=.9))
    for r in batch['records']:
        w.verify_record(r,CONFIG,POLICY)
    return batch


def edit_pack(data, changes):
    workbook=load_workbook(io.BytesIO(data));changes(workbook)
    out=io.BytesIO();workbook.save(out);return out.getvalue()


def test_excel_good_bad_unsure_masked_pack_and_utc_audit(monkeypatch):
    batch=review_batch(monkeypatch)
    profile=w.profile_hash(CONFIG,POLICY)
    data=w.export_review_pack(batch,profile)
    workbook=load_workbook(io.BytesIO(data))
    sheet=workbook['PTSL Review']
    assert [c.value for c in sheet[1]]==list(w.REVIEW_COLUMNS)
    assert workbook['_Pack'].sheet_state=='hidden'
    assert sheet['F2'].value is None
    assert sheet.data_validations.dataValidation[0].formula1=='"Good,Bad,Unsure"'
    assert all(c.data_type!='f' for row in sheet for c in row)
    for i,verdict in enumerate(['Good','Bad','Unsure'],2):
        sheet.cell(i,6,verdict);sheet.cell(i,7,'Contact siti@example.my for review.')
    out=io.BytesIO();workbook.save(out)
    assert w.import_review_pack(batch,out.getvalue(),'Linguist',profile)=={'Good':1,'Bad':1,'Unsure':1}
    assert [r['status'] for r in batch['records']]==['HUMAN_APPROVED','QUARANTINE','PTSL_REVIEW']
    assert batch['records'][2]['secondary_review']
    manifest=json.loads(w.build_exports(batch,CONFIG,POLICY)['manifest.json'])
    assert len(manifest['review_audit'])==3
    for record in batch['records']:
        event=record['history'][-1]
        assert event['reviewer_role']=='Linguist' and event['timestamp'].endswith('+00:00')
        assert event['channel']=='PTSL_EXCEL' and 'siti@example.my' not in event['reviewer_notes']
    with pytest.raises(ValueError,match='already imported'):
        w.import_review_pack(batch,out.getvalue(),'Linguist',profile)


@pytest.mark.parametrize('tamper', ['text','stage','id','duplicate','verdict','formula','delete','profile'])
def test_excel_tamper_rejected_atomically(monkeypatch,tamper):
    batch=review_batch(monkeypatch);profile=w.profile_hash(CONFIG,POLICY)
    data=w.export_review_pack(batch,profile)
    before=copy.deepcopy(batch['records'])
    def change(workbook):
        sheet=workbook['PTSL Review'];sheet['F2']='Good';sheet['F3']='Good'
        if tamper=='text':sheet['C3']='Changed original'
        if tamper=='stage':sheet['B3']='GOLD_4'
        if tamper=='id':sheet['A3']='UNKNOWN'
        if tamper=='duplicate':sheet['A3']=sheet['A2'].value
        if tamper=='verdict':sheet['F3']='Automatically approve'
        if tamper=='formula':sheet['G3']='=1+1'
        if tamper=='delete':sheet.delete_rows(3)
        if tamper=='profile':workbook['_Pack']['B2']='stale'
    data=edit_pack(data,change)
    with pytest.raises(ValueError):w.import_review_pack(batch,data,'Linguist',profile)
    assert batch['records']==before


def test_excel_stale_reverify_and_missing_reviewer_rejected(monkeypatch):
    batch=review_batch(monkeypatch);profile=w.profile_hash(CONFIG,POLICY)
    data=w.export_review_pack(batch,profile)
    data=edit_pack(data,lambda book:setattr(book['PTSL Review']['F2'],'value','Good'))
    with pytest.raises(ValueError,match='reviewer role'):w.import_review_pack(batch,data,'',profile)
    w.human_decision(batch['records'][0],'UNSURE','Linguist',profile)
    with pytest.raises(ValueError,match='snapshot changed'):w.import_review_pack(batch,data,'Linguist',profile)
    assert batch['records'][1]['status']=='PTSL_REVIEW'


def test_excel_formula_like_corpus_is_literal_and_pii_is_absent(monkeypatch):
    batch=ingest([{'text':'=HYPERLINK("https://example.org", "literal source") Contact siti@example.my'}])
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*a:response(record,confidence=.9))
    w.verify_record(batch['records'][0],CONFIG,POLICY)
    data=w.export_review_pack(batch,w.profile_hash(CONFIG,POLICY))
    sheet=load_workbook(io.BytesIO(data))['PTSL Review']
    assert sheet['C2'].data_type=='s' and sheet['C2'].value.startswith('=')
    assert '<EMAIL>' in sheet['C2'].value and 'siti@example.my' not in sheet['C2'].value


def test_excel_short_queue_filter_and_long_cell_rejected(monkeypatch):
    batch=review_batch(monkeypatch)
    batch['records'][0]['stage']='GOLD_4'
    profile=w.profile_hash(CONFIG,POLICY)
    pack=w.export_review_pack(batch,profile,'GOLD_4')
    assert load_workbook(io.BytesIO(pack))['PTSL Review'].max_row==2
    batch['records'][0]['reason']='x'*32768
    with pytest.raises(ValueError,match='32,767'):w.export_review_pack(batch,profile)


@pytest.mark.parametrize('overlap',[50,64,80])
@pytest.mark.parametrize('n',[300,600,601,1201,2000])
def test_new_model_token_bounds_and_configurable_overlap(overlap,n):
    tokenizer=WordTokenizer();text=' '.join(f'kata{i}' for i in range(n))
    chunks=w.semantic_chunks(text,tokenizer,overlap=overlap)
    assert all(300<=c['tokens']<=600 for c in chunks)
    assert chunks[0]['start']==0 and chunks[-1]['end']==n
    for a,b in zip(chunks,chunks[1:]):
        assert a['end']-b['start']==overlap
        assert tokenizer.encode(a['text'])[-overlap:]==tokenizer.encode(b['text'])[:overlap]


def test_chunk_paragraph_and_page_lineage_is_retained(monkeypatch):
    paragraphs=[' '.join(f'perkara{i}_{j}' for j in range(450)) for i in range(3)]
    batch=approve_batch(monkeypatch,[{'text':'\n\n'.join(paragraphs),'source':'circular','paragraph_ids':['P1','P2','P3'],'page_number':12}])
    files=w.build_exports(batch,CONFIG,POLICY,tokenizer=WordTokenizer())
    chunks=[json.loads(line) for line in files['gold_2_rag_chunks.jsonl'].decode().splitlines()]
    assert {pid for chunk in chunks for pid in chunk['paragraph_ids']}=={'P1','P2','P3'}
    assert chunks[0]['paragraph_ids']==['P1','P2'] or chunks[0]['paragraph_ids']==['P1']
    assert chunks[-1]['paragraph_ids'][-1]=='P3'
    assert all(chunk['page_number']==12 for chunk in chunks)
    assert pq.read_table(io.BytesIO(files['gold_2_rag.parquet'])).to_pylist()==chunks


def test_ambiguous_paragraph_mapping_blocks_release(monkeypatch):
    batch=approve_batch(monkeypatch,[{'text':'Satu perenggan sahaja.','source':'circular','paragraph_ids':['P1','P2']}])
    with pytest.raises(ValueError,match='boundaries'):w.build_exports(batch,CONFIG,POLICY,tokenizer=WordTokenizer())


@pytest.mark.parametrize('reported', ['id','ms'])
def test_indonesian_source_retained_for_review_even_at_low_confidence(monkeypatch,reported):
    batch=ingest([{'text':'Saya membutuhkan informasi tentang perpustakaan.','language':'id'}]);record=batch['records'][0]
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*a:response(record,confidence=.4,language=reported))
    w.verify_record(record,CONFIG,POLICY)
    assert record['status']=='PTSL_REVIEW' and record['language']=='lang_near_malay'
    assert record['flags']['lang_near_malay']
    assert not w.bulk_eligible(record,w.profile_hash(CONFIG,POLICY))


@pytest.mark.parametrize('flags',[{'three_r':['Race']},{'three_r':['Religion','Royalty']},{'damaged_chars':True}])
def test_quality_flags_route_to_review_and_are_counted(monkeypatch,flags):
    batch=ingest([{'text':'Perbincangan neutral yang memerlukan semakan.'}]);record=batch['records'][0]
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*a:response(record,**flags))
    w.verify_record(record,CONFIG,POLICY)
    assert record['status']=='PTSL_REVIEW'
    assert not w.bulk_eligible(record,w.profile_hash(CONFIG,POLICY))
    summary=w.quality_summary(batch)
    assert summary['three_r']==int(bool(flags.get('three_r')))
    assert summary['damaged_chars']==int(bool(flags.get('damaged_chars')))


def test_unsafe_and_corrupt_remain_quarantined_even_if_near_malay(monkeypatch):
    batch=ingest([{'text':'Teks untuk ujian.','language':'id'}]);r=batch['records'][0]
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*a:response(record,unsafe=True,lang_near_malay=True))
    w.verify_record(r,CONFIG,POLICY);assert r['status']=='QUARANTINE'
    bad=ingest([{'text':'Teks rosak\ufffd'}]);assert bad['records'][0]['status']=='QUARANTINE'
    assert w.quality_summary(bad)['damaged_chars']==1


def test_duplicate_provenance_and_file_alias_are_not_deleted(monkeypatch):
    row={'text':'Teks contoh dengan hak sumber yang jelas.'}
    batch=approve_batch(monkeypatch,[row,row])
    files=w.build_exports(batch,CONFIG,POLICY);manifest=json.loads(files['manifest.json'])
    assert len(batch['records'])==2 and batch['records'][1]['status']=='DUPLICATE'
    mapping=next(m for m in manifest['duplicate_mappings'] if m['status']=='DUPLICATE')
    assert mapping['representative_id']==batch['records'][0]['id'] and mapping['duplicate_group_id']
    data=json.dumps(row).encode();aliased=w.ingest_files([('a.jsonl',data),('b.jsonl',data)])
    assert aliased['file_aliases'][0]['representative_source_id']==aliased['sources'][0]['source_id']


def test_synthetic_sft_isolated_with_parquet_and_bundle_checksums(monkeypatch):
    rows=dialogues(40)
    for i,row in enumerate(rows):row['origin_type']='synthetic' if i%2 else 'human'
    batch=approve_batch(monkeypatch,rows)
    files=w.build_exports(batch,CONFIG,POLICY)
    manifest=json.loads(files['manifest.json'])
    human=[json.loads(x)['messages'] for x in files['gold_3_sft_messages.jsonl'].decode().splitlines()]
    synthetic=[json.loads(x)['messages'] for x in files['gold_3_sft_synthetic.jsonl'].decode().splitlines()]
    human_prompts={turns[0]['content'] for turns in human};synthetic_prompts={turns[0]['content'] for turns in synthetic}
    assert human_prompts and synthetic_prompts and not human_prompts&synthetic_prompts
    declared={r['original'][0]['content']:r['origin_type'] for r in batch['records']}
    assert all(declared[p]=='human' for p in human_prompts)
    assert all(declared[p]=='synthetic' for p in synthetic_prompts)
    assert len(files)==12
    canonical=pq.read_table(io.BytesIO(files['gold_3_sft.parquet'])).to_pylist()
    assert len(canonical)==len(human)+len(synthetic)
    assert set(canonical[0])==set(w.CANONICAL_SCHEMAS['GOLD_3']['properties'])
    checks={line.split('  ',1)[1]:line.split('  ',1)[0] for line in files['checksums.sha256'].decode().splitlines()}
    assert set(checks)==set(files)-{'checksums.sha256'}
    assert all(w.sha256(files[name])==value for name,value in checks.items())
    with zipfile.ZipFile(io.BytesIO(w.bundle_zip(files))) as archive:
        assert all(archive.read(name)==data for name,data in files.items())
    assert manifest['sft_origin_counts']=={'human':len(human),'synthetic':len(synthetic)}


def test_origin_validation_and_upload_default():
    data=json.dumps({'prompt':'Ujian sumber','response':'Jawapan contoh.'}).encode()
    batch=w.ingest_files([('s.jsonl',data)],default_origin='synthetic')
    assert batch['records'][0]['origin_type']=='synthetic' and not batch['records'][0]['origin_declared']
    bad=ingest([{'text':'Teks contoh.','origin_type':'unknown'}])
    assert bad['records'][0]['status']=='QUARANTINE'


def test_deferred_release_snapshot_and_failure_return_no_bytes(monkeypatch):
    batch=approve_batch(monkeypatch,dialogues(10))
    job=w.ReleaseJob(batch,CONFIG,POLICY)
    batch['records'][0]['candidate'][0]['content']='New mutation after snapshot'
    archive=job.get_zip()
    assert job.status=='ready' and job.get_zip()==archive
    assert 'New mutation after snapshot' not in str(job.files)
    invalid=copy.deepcopy(job.batch);invalid['records'][0]['candidate'][0]['content']='Changed user'
    failure=w.ReleaseJob(invalid,CONFIG,POLICY)
    with pytest.raises(ValueError):failure.get_zip()
    assert failure.status=='failed' and failure.files is None and failure.archive is None


def test_synthetic_prose_and_benchmark_never_mix_into_human_products(monkeypatch):
    batch=approve_batch(monkeypatch,[{'text':'Kajian contoh ini menggunakan bahasa yang tersusun.','origin_type':'synthetic'}, {'text':'Peraturan contoh hendaklah dipatuhi sebelum permohonan dihantar.','source':'circular','origin_type':'synthetic'}, {'prompt':'Terangkan istilah sintetik unik abcdefghijklm.', 'response':'Istilah rekaan untuk penilaian contoh.', 'tags':['benchmark'], 'origin_type':'synthetic'}])
    files=w.build_exports(batch,CONFIG,POLICY,tokenizer=WordTokenizer())
    for stage in ['gold_1_canonical_cpt.jsonl','gold_2_rag_chunks.jsonl','gold_4_eval_benchmark.jsonl']:
        assert files[stage]==b''
    assert all(name in files for name in ['gold_1_canonical_cpt_synthetic.jsonl','gold_2_rag_chunks_synthetic.jsonl','gold_4_eval_benchmark_synthetic.jsonl'])
    manifest=json.loads(files['manifest.json'])
    assert all(manifest['files'][name]['rows']>=1 for name in ['gold_1_canonical_cpt_synthetic.jsonl','gold_2_rag_chunks_synthetic.jsonl','gold_4_eval_benchmark_synthetic.jsonl'])
    assert all(name in files for name in ['gold_1_canonical_synthetic.parquet','gold_2_rag_synthetic.parquet','gold_4_eval_synthetic.parquet'])
