"""Offline acceptance checks for the review-PDF dual-layer architecture."""
import copy
import io
import json

import pyarrow.parquet as pq
import pytest
from streamlit.testing.v1 import AppTest

import watan_gold_studio as w
from test_four_tiers import CONFIG, POLICY, WordTokenizer, approve_batch, dialogues, ingest, response
from test_refinement import edit_pack


def parquet(files, name):
    return pq.read_table(io.BytesIO(files[name])).to_pylist()


def jsonl(files, name):
    return [json.loads(line) for line in files[name].splitlines()]


def release(monkeypatch, rows, **kwargs):
    return w.build_exports(approve_batch(monkeypatch, rows), CONFIG, POLICY, **kwargs)


def test_rich_canonical_contract_and_lean_projection(monkeypatch):
    rows = [
        {'text':'Perenggan kedua menerangkan penggunaan tanda baca.', 'paragraph_id':'p2', 'document_id':'doc-a', 'paragraph_order':2, 'source_id':'contract-a', 'silver_run_id':'silver-2026'},
        {'text':'Perenggan pertama memperkenalkan bahasa yang jelas.', 'paragraph_id':'p1', 'document_id':'doc-a', 'paragraph_order':1, 'source_id':'contract-a', 'silver_run_id':'silver-2026'},
        *dialogues(1),
        {'turns':[{'role':'user','content':'Pilih ejaan bagi gabungan kerja dan sama.'}], 'reference_answer':'kerjasama', 'choices':['kerjasama','kerja-sama'], 'scoring_method':'MCQ', 'category':'DBP_Grammar', 'author_role':'Linguist'},
    ]
    files=release(monkeypatch,rows,gold_version='v2.1')
    for tier,name in [('GOLD_1','gold_1_canonical'),('GOLD_2','gold_2_rag'),('GOLD_3','gold_3_sft'),('GOLD_4','gold_4_eval')]:
        table=pq.read_table(io.BytesIO(files[name+'.parquet']))
        assert set(table.column_names)==set(w.CANONICAL_SCHEMAS[tier]['required'])
        for row in table.to_pylist():w.validate_gold_row(row,tier,canonical=True)
    canonical=parquet(files,'gold_1_canonical.parquet')
    lean=jsonl(files,'gold_1_canonical_cpt.jsonl')
    assert [r['paragraph_id'] for r in canonical]==['p1','p2']
    assert lean==[{'id':r['paragraph_id'], **{k:r[k] for k in ('text','source_id','document_id','paragraph_order','script','dialect_flag','allowed_uses')}} for r in canonical]
    assert {r['silver_run_id'] for r in canonical}=={'silver-2026'}
    sft=parquet(files,'gold_3_sft.parquet')
    assert jsonl(files,'gold_3_sft_messages.jsonl')==[{'messages':r['messages']} for r in sft]
    assert not any('conversations' in n for n in files)
    assert len([n for n in files if n.startswith('gold_3') and n.endswith('.parquet')])==1
    evaluation=jsonl(files,'gold_4_eval_benchmark.jsonl')
    assert evaluation==parquet(files,'gold_4_eval.parquet')
    assert evaluation[0]['choices']==['kerjasama','kerja-sama'] and evaluation[0]['contamination_checked']
    manifest=json.loads(files['manifest.json'])
    assert manifest['gold_version']=='v2.1' and manifest['silver_run_ids']==['silver-2026']


def test_optional_sharegpt_projection_and_system_turn(monkeypatch):
    row={'messages':[{'role':'system','content':'Jawab dalam Bahasa Melayu.'},{'role':'user','content':'Apakah maksud kata nama?'},{'role':'assistant','content':'Kata nama menamakan orang, tempat atau benda.'}]}
    files=release(monkeypatch,[row],include_conversations=True)
    assert jsonl(files,'gold_3_sft_conversations.jsonl')==[{'conversations':[{'from':role,'value':t['content']} for role,t in zip(['system','human','gpt'],row['messages'])]}]
    assert len([n for n in files if n.startswith('gold_3') and n.endswith('.parquet')])==1


@pytest.mark.parametrize('metadata,lang', [({'dialect_flag':True},'ms'),({'old_spelling':True},'ms'),({'script':'jawi'},'ms'),({'lang':'id'},'lang_near_malay')])
def test_language_variants_are_retained_with_role_review(monkeypatch,metadata,lang):
    batch=approve_batch(monkeypatch,[{'text':'Tulisan bahasa serantau ini dikekalkan sebagai sumber.',**metadata}])
    record=batch['records'][0]
    assert record['status']=='PTSL_REVIEW'
    assert not w.bulk_eligible(record,w.profile_hash(CONFIG,POLICY))
    w.human_decision(record,'APPROVE','Linguist',w.profile_hash(CONFIG,POLICY))
    row=parquet(w.build_exports(batch,CONFIG,POLICY),'gold_1_canonical.parquet')[0]
    assert row['lang']==lang and row['ptsl_status']=='APPROVED'
    if 'script' in metadata:assert row['script']=='jawi'
    if 'dialect_flag' in metadata:assert row['dialect_flag']


def test_dedicated_evaluation_default_and_group_isolation_in_parquet(monkeypatch):
    batch=approve_batch(monkeypatch,dialogues(20))
    default=w.build_exports(batch,CONFIG,POLICY)
    assert len(parquet(default,'gold_3_sft.parquet'))==20
    assert parquet(default,'gold_4_eval.parquet')==[]
    for i,record in enumerate(batch['records']):
        record['meta']['intent_group']=f'intent-{i//2}'
    held=w.build_exports(batch,CONFIG,POLICY,holdout_enabled=True)
    training=parquet(held,'gold_3_sft.parquet');evaluation=parquet(held,'gold_4_eval.parquet')
    assert evaluation
    assert {r['intent_group'] for r in training}.isdisjoint(r['intent_group'] for r in evaluation)
    assert {w.normalized_match(r['messages'][-1]['content'])[:60] for r in training}.isdisjoint(w.normalized_match(r['reference_answer'])[:60] for r in evaluation)
    assert len(training)+len(evaluation)==20


def test_connected_answer_stems_with_dedicated_eval_never_enter_canonical_sft(monkeypatch):
    rows=[{'prompt':'Apakah nama istilah khusus zeta?', 'response':'Istilah zeta menamakan objek rekaan bagi ujian ini.', 'tags':['eval'], 'intent_group':'eval-zeta'},
          {'prompt':'Bagaimanakah istilah omega dihuraikan?', 'response':'Istilah zeta menamakan objek rekaan bagi ujian ini.', 'intent_group':'other-intent'},
          *dialogues(1)]
    files=release(monkeypatch,rows)
    assert len(parquet(files,'gold_3_sft.parquet'))==1
    assert json.loads(files['manifest.json'])['leakage_guard']['excluded_records'][0]['reason']=='held_out_intent_or_answer_stem'


@pytest.mark.parametrize('uses,licence,expected', [([], 'approved',0),(['train'],'UNKNOWN',0),(['eval'],'approved',0),(['train'],'approved',1)])
def test_licence_permissions_filter_projection_not_canonical(monkeypatch,uses,licence,expected):
    files=release(monkeypatch,[dict(dialogues(1)[0],allowed_uses=uses,licence_status=licence)])
    assert len(parquet(files,'gold_3_sft.parquet'))==1
    assert len(jsonl(files,'gold_3_sft_messages.jsonl'))==expected
    if not expected:assert json.loads(files['manifest.json'])['warnings']


def test_rag_canonical_lineage_and_permission_by_product(monkeypatch):
    files=release(monkeypatch,[{'text':' '.join('istilah'+str(i) for i in range(901)), 'source':'circular', 'paragraph_id':'para-a', 'document_id':'doc-a', 'page_number':7, 'allowed_uses':['rag_display'], 'embedding_model':'approved-embed'}],tokenizer=WordTokenizer())
    rows=parquet(files,'gold_2_rag.parquet')
    assert rows==jsonl(files,'gold_2_rag_chunks.jsonl') and len(rows)>1
    assert all(300<=r['token_count']<=600 and r['paragraph_ids']==['para-a'] and r['page_number']==7 for r in rows)
    assert all(r['embedding_model']=='approved-embed' for r in rows)
    assert parquet(files,'gold_1_canonical.parquet') and not files['gold_1_canonical_cpt.jsonl']


def test_narrow_synthetic_pii_exception_and_excel_is_always_masked(monkeypatch):
    row={'prompt':'Latihan samaran: demo@example.com, +1-202-555-0101, 000000-00-0000; rahsia real@ukm.edu.my dan 012-3456789.', 'response':'Gantikan maklumat dengan <EMAIL>, <PHONE> dan <IC>.', 'origin_type':'synthetic', 'allowlist_synthetic_pii':True}
    batch=ingest([row]);r=batch['records'][0]
    text=r['original'][0]['content']
    for fixture in ['demo@example.com','+1-202-555-0101','000000-00-0000']:assert fixture in text
    assert 'real@ukm.edu.my' not in text and '012-3456789' not in text
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*args:response(record,confidence=.9))
    w.verify_record(r,CONFIG,POLICY)
    pack=w.export_review_pack(batch,w.profile_hash(CONFIG,POLICY))
    from openpyxl import load_workbook
    cells=' '.join(str(c.value) for row in load_workbook(io.BytesIO(pack))['PTSL Review'] for c in row)
    assert 'demo@example.com' not in cells and '+1-202-555-0101' not in cells
    w.human_decision(r,'APPROVE','AI_Engineer',w.profile_hash(CONFIG,POLICY))
    files=w.build_exports(batch,CONFIG,POLICY)
    assert 'demo@example.com' in files['gold_3_sft_synthetic.jsonl'].decode()
    assert not files['gold_3_sft_messages.jsonl']
    with pytest.raises(ValueError,match='Unapproved'):w.exempted_text('real@ukm.edu.my',['real@ukm.edu.my'])
    human=ingest([dict(row,origin_type='human')])['records'][0]
    assert 'demo@example.com' not in human['original'][0]['content']
    edited=copy.deepcopy(r['candidate']);edited[-1]['content']='Hubungi real@ukm.edu.my.'
    with pytest.raises(ValueError):w.validate_candidate(r['original'],edited,r['synthetic_pii_values'])


@pytest.mark.parametrize('text,flag', [('Saya menggunakan Gemma4-31B.','legacy_base_model_name'),('I cannot confirm; refer to project records.','hedging_template'),('DBP dan RTM ialah rakan strategik projek ini.','mutable_project_facts')])
def test_seed_hygiene_flags_and_role_permissions(monkeypatch,text,flag):
    batch=approve_batch(monkeypatch,[{'prompt':'Terangkan maklumat projek ini.', 'response':text}]);r=batch['records'][0]
    assert flag in r['seed_hygiene_flags'] and r['status']=='PTSL_REVIEW'
    profile=w.profile_hash(CONFIG,POLICY)
    with pytest.raises(ValueError,match='Personal usernames'):w.human_decision(r,'APPROVE','akramtaha',profile)
    if flag=='mutable_project_facts':
        with pytest.raises(ValueError,match='Project_Lead'):w.human_decision(r,'APPROVE','AI_Engineer',profile)
    w.human_decision(r,'APPROVE','Project_Lead',profile)
    r['history'].append({'action':'UNSURE','reviewer_id':'old-personal-name','reviewer':'akramtaha','reviewer_notes':'private staff name','at':w.now()})
    files=w.build_exports(batch,CONFIG,POLICY)
    assert parquet(files,'gold_3_sft.parquet')[0]['reviewer_role']=='Project_Lead'
    assert b'akramtaha' not in files['manifest.json'] and b'old-personal-name' not in files['manifest.json'] and b'private staff name' not in files['manifest.json']


def test_version_checksums_and_duplicate_links(monkeypatch):
    row=dialogues(1)[0]
    files=release(monkeypatch,[dict(row,silver_run_id='run-approved'),row],gold_version='v3.0.1')
    manifest=json.loads(files['manifest.json'])
    assert manifest['silver_run_ids']==['run-approved']
    assert manifest['duplicate_links'] and manifest['source_code_sha256']==w.sha256(w.Path(w.__file__).read_bytes())
    for name,info in manifest['files'].items():assert info['sha256']==w.sha256(files[name])
    for line in files['checksums.sha256'].decode().splitlines():
        digest,name=line.split('  ',1);assert digest==w.sha256(files[name])
    assert b'v3.0.1' in files['dataset_card.md']
    with pytest.raises(ValueError):w.valid_gold_version('staff-name')


def test_table_metadata_parsing_and_invalid_lineage():
    row=w.normalize_input({'text':'Ayat contoh.', 'dialect_flag':'false','paragraph_order':'2','lang_confidence':'0.91','script':'JAWI','allowed_uses':'["train"]'})
    assert row['metadata']['dialect_flag'] is False and row['metadata']['paragraph_order']==2 and row['metadata']['script']=='jawi'
    with pytest.raises(ValueError,match='derived_from'):w.normalize_input({'prompt':'Soalan','response':'Jawapan','origin':'from_gold_1'})
    with pytest.raises(ValueError):w.normalize_input({'text':'Ayat', 'lang_confidence':'NaN'})


def test_minimal_sidebar_and_disabled_holdout_default():
    app=AppTest.from_file(str(w.Path(w.__file__))).run(timeout=15)
    assert not app.exception
    assert [x.label for x in app.sidebar.text_input][:3]==['API Base URL','Served Model ID','Gold Version Tag']
    role=next(x for x in app.sidebar.selectbox if x.label=='Reviewer Role')
    assert role.options==list(w.REVIEWER_ROLES)
    assert not any(x.label=='Enable SFT holdout (advanced)' for x in app.toggle)  # History remains available.
    from test_streamlit import review_app, find_button
    app=review_app()
    next(x for x in app.radio if x.label=='Workspace Mode').set_value(w.WORKSPACE_MODES[0]).run(timeout=15)
    find_button(app,'Approve').click().run(timeout=15)
    assert app.session_state['active_step']==3
    holdout=next(x for x in app.toggle if 'holdout' in x.label)
    assert holdout.value is False
    assert next(x for x in app.slider if 'holdout' in x.label.lower()).disabled
    assert not next(x for x in app.checkbox if 'ShareGPT' in x.label).value


def test_verifier_and_review_edits_cannot_bypass_seed_fact_flags(monkeypatch):
    batch=ingest(dialogues(1));record=batch['records'][0]
    edited=copy.deepcopy(record['original']);edited[-1]['content']='Pembiayaan KPT membiayai projek ini.'
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*args:response(record,messages=edited))
    w.verify_record(record,CONFIG,POLICY)
    assert 'mutable_project_facts' in record['seed_hygiene_flags'] and record['status']=='PTSL_REVIEW'
    with pytest.raises(ValueError,match='Project_Lead'):w.human_decision(record,'APPROVE','Linguist',w.profile_hash(CONFIG,POLICY))
    batch=ingest(dialogues(1));record=batch['records'][0]
    monkeypatch.setattr(w,'verify_with_qwen',lambda record,*args:response(record,confidence=.9))
    w.verify_record(record,CONFIG,POLICY)
    with pytest.raises(ValueError,match='Project_Lead'):w.human_decision(record,'APPROVE','AI_Engineer',w.profile_hash(CONFIG,POLICY),candidate=edited)


def test_conflicting_origin_and_repeated_canonical_ids_are_rejected(monkeypatch):
    with pytest.raises(ValueError,match='Conflicting origin'):w.normalize_input(dict(dialogues(1)[0],origin='synthetic',origin_type='human'))
    batch=approve_batch(monkeypatch,[{'text':'Sumber pertama menerangkan kaedah penulisan.', 'paragraph_id':'same-id'}, {'text':'Sumber kedua menghuraikan cara membaca.', 'paragraph_id':'same-id'}])
    with pytest.raises(ValueError,match='Repeated canonical'):w.build_exports(batch,CONFIG,POLICY)


def test_transitive_group_selection_includes_explicit_component(monkeypatch):
    rows=[{'prompt':'Definisikan istilah rekaan alpha.', 'response':'Huraian khusus bagi konsep alpha.', 'intent_group':'family-a','tags':['eval']},
          {'prompt':'Jelaskan konteks beta dalam contoh.', 'response':'Huraian khusus bagi konsep beta.', 'intent_group':'family-a'},
          {'prompt':'Huraikan konsep gamma yang berlainan.', 'response':'Huraian khusus bagi konsep beta.', 'intent_group':'family-b'}, *dialogues(20)]
    batch=approve_batch(monkeypatch,rows)
    plan=w.tier_plan(batch['records'],holdout_enabled=True)
    assert {r['id'] for r in batch['records'][:3]} <= {r['id'] for r in plan['by_stage']['GOLD_4']}
    files=w.build_exports(batch,CONFIG,POLICY,holdout_enabled=True)
    assert {r['intent_group'] for r in parquet(files,'gold_3_sft.parquet')}.isdisjoint(r['intent_group'] for r in parquet(files,'gold_4_eval.parquet'))
