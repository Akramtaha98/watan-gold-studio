"""Bronze -> Silver (rules, Agent 1, MinHash) -> Quality Gate -> PTSL -> Gold alignment tests."""
import json

import pytest
from sqlalchemy.exc import IntegrityError
from streamlit.testing.v1 import AppTest

import silver
import watan_gold_studio as w
from storage import Storage
from test_four_tiers import CONFIG, POLICY, WordTokenizer, dialogues, response
from test_streamlit import APP, find_button

RULES = silver.parse_rules(silver.DEFAULT_SILVER_YAML)
LONG = ("Kerajaan telah mengumumkan dasar baharu untuk memperkukuh bahasa kebangsaan di seluruh negara pada tahun ini. "
        "Dasar itu merangkumi pendidikan, penerbitan dan penggunaan bahasa dalam sektor awam serta swasta.")


def prose_file(*texts, name="doc.txt"):
    return (name, "\n\n".join(texts).encode())


def ctx():
    return {"config": CONFIG.public(), "policy": POLICY}


# ---- Stage 0: Bronze
def test_bronze_entry_hashes_exact_bytes_without_mutation():
    data = "Kerja-\nsama".encode()
    batch = w.ingest_files([("raw.txt", data)])
    entry = batch["bronze"][0]
    assert entry["sha256"] == w.sha256(data) and entry["bytes"] == len(data) and entry["filename"] == "raw.txt"
    assert entry["ingested_at"].endswith("+00:00") and batch["ingested_at"] == entry["ingested_at"]
    assert data == "Kerja-\nsama".encode()


def test_identical_files_register_once():
    data = prose_file(LONG)[1]
    batch = w.ingest_files([("a.txt", data), ("b.txt", data)])
    assert len(batch["bronze"]) == 1


def test_bronze_registry_is_append_only_in_sqlite(tmp_path):
    store = Storage(f"sqlite:///{tmp_path/'t.db'}", release_root=tmp_path/"rel")
    batch = w.ingest_files([prose_file(LONG)])
    batch_id, _ = store.create_batch(batch, ctx())
    rows = store.list_bronze(batch_id)
    assert [r["sha256"] for r in rows] == [batch["bronze"][0]["sha256"]]
    for statement in ("UPDATE bronze_registry SET bytes = 1", "DELETE FROM bronze_registry"):
        with pytest.raises(IntegrityError, match="immutable"):
            with store.engine.begin() as conn:
                conn.exec_driver_sql(statement)
    assert len(store.list_bronze(batch_id)) == 1


def test_bronze_lands_in_manifest(monkeypatch):
    batch = w.ingest_files([prose_file(LONG)], silver_rules=RULES)
    monkeypatch.setattr(w, "verify_with_qwen", lambda record, *a: response(record, confidence=.99))
    for record in batch["records"]:
        w.verify_record(record, CONFIG, POLICY)
    manifest = json.loads(w.build_exports(batch, CONFIG, POLICY, tokenizer=WordTokenizer())["manifest.json"])
    assert manifest["manifest_version"] == 5
    assert manifest["pipeline"]["bronze"]["files"][0]["sha256"] == batch["bronze"][0]["sha256"]
    assert manifest["pipeline"]["quality_gate"]["counts"]["PASS"] == 1
    assert manifest["pipeline"]["silver"]["rules_sha256"] == silver.rules_sha256(RULES)


# ---- Stage 1: Silver rules
def test_silver_rules_clean_text_before_masking():
    raw = "Kerja-\nsama   penting\r\ndan lain\nlain.\x00​\n\n\n\nBaris baharu."
    batch = w.ingest_files([("x.txt", raw.encode())], silver_rules=RULES)
    text = batch["records"][0]["text"]
    assert "kerja-sama penting dan lain lain." in text.lower() and "\x00" not in text and "​" not in text
    assert batch["records"][0]["silver"]["applied"]
    assert batch["silver"]["cleaning_enabled"] and batch["silver"]["rules_sha256"]


def test_silver_off_keeps_text_and_rules_reject_unknown_keys():
    batch = w.ingest_files([("x.txt", b"a   b")])
    assert batch["silver"]["cleaning_enabled"] is False
    with pytest.raises(ValueError):
        silver.parse_rules("unknown_rule: true")


# ---- Stage 1: MinHash
def test_minhash_groups_near_duplicates_and_forces_review(monkeypatch):
    base = " ".join(f"Dasar nombor {i} memperkukuh bahasa kebangsaan melalui pendidikan penerbitan dan penyelidikan di seluruh negara." for i in range(1, 8))
    variant = base.replace("penerbitan", "percetakan", 1)
    batch = w.ingest_files([prose_file(base, name="a.txt"), prose_file(variant, name="b.txt"), prose_file("Kandungan lain sama sekali berbeza tentang pertanian dan perikanan di pantai timur.", name="c.txt")], near_dup_threshold=.8)
    assert batch["silver"]["near_duplicate"]["groups"] == 1
    first, second, third = batch["records"]
    assert second["near_duplicate_of"] == first["id"] and not first.get("near_duplicate_of") and not third.get("near_duplicate_group_id")
    monkeypatch.setattr(w, "verify_with_qwen", lambda record, *a: response(record, confidence=.99))
    for record in batch["records"]:
        w.verify_record(record, CONFIG, POLICY)
    assert [r["status"] for r in batch["records"]] == ["AUTO_PASS", "PTSL_REVIEW", "AUTO_PASS"]
    assert w.gate_outcome(second) == "WARN" and "near_duplicate" in w.gate_reasons(second)


# ---- Stage 1: Agent 1
def agent1_reply(paragraphs, qa=(), content_type="prose"):
    body = {"paragraphs": paragraphs, "qa_pairs": list(qa), "structure": {"content_type": content_type, "heading": None, "page_number": None}}
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body)}}]}


def messy_batch():
    messy = "Kerajaan telah\nmengumumkan dasar baharu\nuntuk bahasa kebangsaan\n12\nserta pendidikan negara."
    return w.ingest_files([("scan.txt", messy.encode())])


def test_agent1_applies_validated_restructure(monkeypatch):
    batch = messy_batch()
    assert batch["records"][0]["silver"]["messy"]
    monkeypatch.setattr(w, "request_json", lambda *a, **k: agent1_reply(["Kerajaan telah mengumumkan dasar baharu untuk bahasa kebangsaan serta pendidikan negara."]))
    summary = w.run_agent1(batch, CONFIG)
    record = batch["records"][0]
    assert summary["applied"] == 1 and record["silver"]["agent1"]["status"] == "applied"
    assert record["original_text"] == record["text"] == "Kerajaan telah mengumumkan dasar baharu untuk bahasa kebangsaan serta pendidikan negara."
    assert record["silver"]["agent1"]["before_sha256"] != record["silver"]["agent1"]["after_sha256"]


def test_agent1_rejects_invented_words_and_errors_become_warn_causes(monkeypatch):
    batch = messy_batch()
    original = batch["records"][0]["original_text"]
    monkeypatch.setattr(w, "request_json", lambda *a, **k: agent1_reply(["Kerajaan telah mengumumkan dasar baharu dan perjanjian rahsia antarabangsa yang besar."]))
    w.run_agent1(batch, CONFIG)
    record = batch["records"][0]
    assert record["silver"]["agent1"]["status"] == "rejected" and record["original_text"] == original
    assert "agent1_rejected" in w.silver_warnings(record)
    other = messy_batch()
    def boom(*a, **k):
        raise w.VerificationError("down")
    monkeypatch.setattr(w, "request_json", boom)
    assert w.run_agent1(other, CONFIG)["error"] == 1


def test_agent1_skips_clean_and_dialogue_records(monkeypatch):
    calls = []
    monkeypatch.setattr(w, "request_json", lambda *a, **k: calls.append(1))
    batch = w.ingest_files([prose_file(LONG), ("d.jsonl", json.dumps({"prompt": "Apa itu bahasa?", "response": "Bahasa ialah alat komunikasi."}).encode())])
    w.run_agent1(batch, CONFIG)
    assert not calls


# ---- Stage 2: Quality Gate
@pytest.mark.parametrize("status,expected", [("AUTO_PASS", "PASS"), ("HUMAN_APPROVED", "PASS"), ("QUARANTINE", "BLOCK"), ("ERROR", "BLOCK"), ("DUPLICATE", "DUPLICATE"), ("PENDING", "PENDING")])
def test_gate_maps_statuses(status, expected):
    assert w.gate_outcome({"status": status}) == expected


def test_review_versus_warn_split(monkeypatch):
    batch = w.ingest_files([("d.jsonl", "\n".join(json.dumps(x) for x in dialogues(3)).encode())])
    monkeypatch.setattr(w, "verify_with_qwen", lambda record, *a: response(record, confidence=.85, dbp_errors=["ejaan"]) if record["id"] == "R000001" else response(record, confidence=.97, dialect_flag=True) if record["id"] == "R000002" else response(record, confidence=.99))
    for record in batch["records"]:
        w.verify_record(record, CONFIG, POLICY)
    gates = {r["id"]: w.gate_outcome(r) for r in batch["records"]}
    assert gates == {"R000001": "REVIEW", "R000002": "WARN", "R000003": "PASS"}
    assert w.gate_counts(batch["records"]) == {"PASS": 1, "WARN": 1, "REVIEW": 1, "BLOCK": 0, "DUPLICATE": 0, "PENDING": 0}


# ---- Sampling filter
def test_sampling_filter_holds_clean_records_and_keeps_candidates():
    rows = [dict(prompt=f"Soalan unik nombor {i} tentang tajuk {i*7}.", response=f"Jawapan unik nombor {i} berkenaan tajuk {i*7}.") for i in range(60)]
    batch = w.ingest_files([("d.jsonl", "\n".join(json.dumps(x) for x in rows).encode())])
    batch["records"][0]["seed_hygiene_flags"] = ["x"]
    counts = w.apply_sampling(batch["records"], True, 10)
    held = [r for r in batch["records"] if r.get("sampling") == "held"]
    assert counts["candidates"] == 1 and counts["held"] == len(held) and 0 < len(held) < 60
    assert batch["records"][0]["sampling"] == "selected"
    assert not w.ui_verification_targets(held, "p")
    assert w.apply_sampling(batch["records"], True, 10) == counts
    off = w.apply_sampling(batch["records"], False, 10)
    assert off["held"] == 0 and not any(r.get("sampling") for r in batch["records"])
    assert w.apply_sampling(batch["records"], True, 100)["held"] == 0


# ---- UI
def test_stepper_matches_slide_stages():
    assert w.STEP_LABELS == ("Bronze & Silver (Extract + MinHash)", "Quality Gate (Qwen Agent 2)", "PTSL Human Review", "Gold Release")
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    labels = [b.label for b in app.button if b.label[:2] in {"1.", "2.", "3.", "4."}]
    assert labels == [f"{i}. {name}" for i, name in enumerate(w.STEP_LABELS, 1)]
    assert find_button(app, "2. Quality Gate (Qwen Agent 2)").disabled


def test_step1_exposes_silver_controls():
    app = AppTest.from_file(str(APP)).run(timeout=15)
    labels = {t.label for t in app.toggle}
    assert {"Enable AI Agent 1 for messy PDF/DOCX restructuring", "MinHash near-duplicate detection", "Silver-to-PTSL sampling filter", "Rule-based Silver cleaning"} <= labels
    assert next(t for t in app.toggle if t.label.startswith("Enable AI Agent 1")).value is False
