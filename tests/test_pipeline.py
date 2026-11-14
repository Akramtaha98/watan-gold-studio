import copy
import io
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import watan_gold_studio as w

POLICY = json.loads(w.POLICY_PATH.read_text())
CONFIG = w.QwenConfig()
PAIR = [{"role": "user", "content": "Apakah fungsi tanda soal?"}, {"role": "assistant", "content": "Tanda soal menandakan ayat tanya."}]


def answer(messages=None, confidence=.99, **changes):
    value = {"explicit_content": False, "dialect_flag": False, "script": "rumi", "spelling_variant": "original", "language": "ms", "lang_near_malay": False, "three_r": [], "damaged_chars": False, "confidence": confidence, "dbp_score": .99, "stage": "GOLD_3", "category": "General_BM_QA", "domain": "Malay", "text": "", "dbp_errors": [], "policy_errors": [], "factual_errors": [], "unsafe": False, "corrupted": False, "reason": "Semakan selesai.", "messages": copy.deepcopy((messages.get("original") if isinstance(messages, dict) else messages) or PAIR)}
    value.update(changes)
    return value


def batch(turns=None):
    return w.ingest_files([("input.jsonl", (json.dumps({"messages": turns or PAIR, "licence_status":"approved", "allowed_uses":["train","rag_display","eval"]}, ensure_ascii=False) + "\n").encode())])


def accepted(monkeypatch, data=None):
    b = data or batch()
    monkeypatch.setattr(w, "verify_with_qwen", lambda messages, c, p: answer(messages))
    for r in b["records"]:
        if r["status"] == "PENDING":
            w.verify_record(r, CONFIG, POLICY)
    return b


@pytest.mark.parametrize("suffix", [".jsonl", ".csv", ".xlsx", ".parquet", ".txt"])
def test_all_readers_mask_before_inference(suffix):
    prompt = "Hubungi saya di siti@example.my, IC 900101-14-5678 dan +60 12-345 6789."
    if suffix == ".jsonl":
        raw = json.dumps({"prompt": prompt, "response": "Maklumat peribadi tidak didedahkan."}).encode()
    elif suffix == ".txt":
        raw = f"user: {prompt}\nassistant: Maklumat peribadi tidak didedahkan.".encode()
    else:
        frame = pd.DataFrame([{"prompt": prompt, "response": "Maklumat peribadi tidak didedahkan."}])
        stream = io.BytesIO()
        if suffix == ".csv":
            raw = frame.to_csv(index=False).encode()
        elif suffix == ".xlsx":
            frame.to_excel(stream, index=False)
            raw = stream.getvalue()
        else:
            frame.to_parquet(stream, index=False)
            raw = stream.getvalue()
    b = w.ingest_files([("corpus" + suffix, raw)])
    r = b["records"][0]
    assert r["status"] == "PENDING", b["issues"]
    assert all(x in r["original"][0]["content"] for x in ["<IC>", "<EMAIL>", "<PHONE>"])
    assert "siti@example.my" not in json.dumps(b)
    assert b["sources"][0]["sha256"] == w.sha256(raw)


def test_multiturn_preserved_and_whole_prompt_dedup():
    turns = PAIR + [{"role": "user", "content": "Beri contoh."}, {"role": "assistant", "content": "Siapakah nama kamu?"}]
    lines = [json.dumps({"messages": turns}), json.dumps({"conversations": turns}), json.dumps({"messages": PAIR})]
    b = w.ingest_files([("a.jsonl", ("\n".join(lines)).encode())])
    assert [r["status"] for r in b["records"]] == ["PENDING", "DUPLICATE", "PENDING"]
    assert len(b["records"][0]["original"]) == 4


def test_identical_files_skipped_conflicting_answers_all_held():
    lines = json.dumps({"prompt": "Soalan sama", "response": "Jawapan A"}) + "\n" + json.dumps({"prompt": "Soalan sama", "response": "Jawapan B"})
    b = w.ingest_files([("a.jsonl", lines.encode()), ("b.jsonl", lines.encode())])
    assert len(b["sources"]) == 1
    assert len(b["records"]) == 2
    assert all(r["status"] == "QUARANTINE" for r in b["records"])
    assert "conflicting" in b["records"][0]["reason"]


@pytest.mark.parametrize("obj", [
    {"messages": [{"role": "assistant", "content": "Salah"}, {"role": "user", "content": "Salah"}]},
    {"messages": [{"role": "user", "content": "Tiada jawapan"}]},
    {"prompt": "Soalan", "response": " "},
    {"messages": PAIR, "conversations": PAIR},
    {"messages": [{"role": "user", "content": "A", "extra": 1}, PAIR[1]]},
    {"prompt": "A", "response": "Bad\ufffd"},
])
def test_bad_rows_quarantined(obj):
    b = w.ingest_files([("bad.jsonl", json.dumps(obj).encode())])
    assert b["records"][0]["status"] == "QUARANTINE"
    assert b["records"][0]["original"] == []


def test_bad_json_does_not_hide_later_valid_row():
    raw = b'{"prompt":"a", "prompt":"b"}\n' + json.dumps({"messages": PAIR}).encode()
    b = w.ingest_files([("bad.jsonl", raw)])
    assert [r["status"] for r in b["records"]] == ["QUARANTINE", "PENDING"]
    assert w.ingest_files([("plain.txt", b"Raw prose without a paired answer")])["records"][0]["status"] == "PENDING"


def test_mapping_instruction_context_and_xlsx_sheets():
    assert w.normalize_record({"instruction": "Baca", "input": "Petikan", "output": "Baik"})[0]["content"] == "Baca\n\nPetikan"
    assert w.normalize_record({"x": "S", "y": "J"}, "x", "y")[1]["content"] == "J"
    stream = io.BytesIO()
    with pd.ExcelWriter(stream) as writer:
        pd.DataFrame([{"prompt": "S1", "response": "J1"}]).to_excel(writer, sheet_name="One", index=False)
        pd.DataFrame([{"prompt": "S2", "response": "J2"}]).to_excel(writer, sheet_name="Two", index=False)
    b = w.ingest_files([("sheets.xlsx", stream.getvalue())])
    assert len(b["records"]) == 2


@pytest.mark.parametrize("text", ["nora+test@example.com", "900101145678", "900101-14-5678", "012-3456789", "+60 11 1234 5678", "+44 20 7946 0958", "siti@contoh.my", "siti@exam\u200bple.my", "０１２３４５６７８９"])
def test_masking_and_final_leak_detector(text):
    assert w.pii_leaks(text)
    masked, counts = w.mask_pii(text)
    assert not w.pii_leaks(masked)
    assert sum(counts.values()) >= 1
    assert masked.startswith("<")


@pytest.mark.parametrize("confidence,changes,state", [
    (.95, {}, "AUTO_PASS"), (.9499, {}, "PTSL_REVIEW"), (.75, {}, "PTSL_REVIEW"), (.7499, {}, "QUARANTINE"),
    (.99, {"dbp_errors": ["Ejaan"]}, "PTSL_REVIEW"), (.99, {"policy_errors": ["Identity"]}, "PTSL_REVIEW"),
    (.99, {"factual_errors": ["Unsupported reference"]}, "PTSL_REVIEW"), (.99, {"unsafe": True}, "QUARANTINE"), (.99, {"corrupted": True}, "QUARANTINE"),
])
def test_triage_boundaries(confidence, changes, state):
    assert w.classify(PAIR, answer(confidence=confidence, **changes)) == state


def test_even_unreported_edit_goes_to_review():
    edited = copy.deepcopy(PAIR)
    edited[1]["content"] += " Contoh baharu."
    assert w.classify(PAIR, answer(edited)) == "PTSL_REVIEW"


@pytest.mark.parametrize("mutator", [
    lambda m: m.append({"role": "user", "content": "Invented"}),
    lambda m: m[0].update(content="Changed prompt"),
    lambda m: m[1].update(content="Call 012-3456789"),
])
def test_candidate_integrity_rejects_edits(mutator):
    candidate = copy.deepcopy(PAIR)
    mutator(candidate)
    with pytest.raises(ValueError):
        w.validate_candidate(PAIR, candidate)


def test_placeholder_deletion_rejected():
    original = [{"role": "user", "content": "Hubungi <EMAIL>"}, {"role": "assistant", "content": "Saya tidak dedahkan <EMAIL>."}]
    candidate = copy.deepcopy(original)
    candidate[1]["content"] = "Saya tidak dedahkan."
    with pytest.raises(ValueError):
        w.validate_candidate(original, candidate)


def test_human_bulk_and_stale_verification(monkeypatch):
    b = batch()
    r = b["records"][0]
    monkeypatch.setattr(w, "verify_with_qwen", lambda m, c, p: answer(m, dbp_errors=["Semak"])); w.verify_record(r, CONFIG, POLICY)
    profile = w.profile_hash(CONFIG, POLICY)
    assert w.bulk_eligible(r, profile)
    with pytest.raises(ValueError):
        w.human_decision(r, "APPROVE", "", profile)
    with pytest.raises(ValueError):
        w.human_decision(r, "APPROVE", "Linguist", "stale")
    w.human_decision(r, "APPROVE", "Linguist", profile)
    assert r["status"] == "HUMAN_APPROVED"
    assert len(w.build_exports(b, CONFIG, POLICY)) == 12
    changed_policy = dict(POLICY, base_model="Another")
    with pytest.raises(ValueError, match="stale"):
        w.build_exports(b, CONFIG, changed_policy)


def test_bulk_excludes_factual_and_policy_flags(monkeypatch):
    b = batch(); r = b["records"][0]
    monkeypatch.setattr(w, "verify_with_qwen", lambda m, c, p: answer(m, factual_errors=["Unknown"])); w.verify_record(r, CONFIG, POLICY)
    assert r["status"] == "PTSL_REVIEW"
    assert not w.bulk_eligible(r, w.profile_hash(CONFIG, POLICY))
    w.human_decision(r, "QUARANTINE", "Linguist", w.profile_hash(CONFIG, POLICY))
    assert r["status"] == "QUARANTINE"


def test_verifier_failure_is_retryable_and_never_exported(monkeypatch):
    b = batch(); r = b["records"][0]
    def failed(*args):
        raise w.VerificationError("Offline")
    monkeypatch.setattr(w, "verify_with_qwen", failed)
    w.verify_record(r, CONFIG, POLICY)
    assert r["status"] == "ERROR" and r["verification"] is None
    with pytest.raises(ValueError, match="No eligible"):
        w.build_exports(b, CONFIG, POLICY)
    accepted(monkeypatch, b)
    assert r["status"] == "ERROR"  # helper intentionally processes pending only
    w.verify_record(r, CONFIG, POLICY)
    assert r["status"] == "AUTO_PASS"


def test_exact_exports_hashes_unicode_and_full_conversations(monkeypatch):
    turns = PAIR + [{"role": "user", "content": "Beri contoh éjaan."}, {"role": "assistant", "content": "Siapakah nama kamu?"}]
    b = accepted(monkeypatch, batch(turns))
    files = w.build_exports(b, CONFIG, POLICY)
    manifest = json.loads(files["manifest.json"])
    for key in ("conversations", "messages"):
        data = w.export_jsonl(b["records"], key)
        parsed = json.loads(data)
        assert set(parsed) == {key}
        assert len(parsed[key]) == 4
        assert b"\\u00e9" not in data and "é" in data.decode()
        assert w.validate_export(data, key) == 1
    for filename in w.FILENAMES:
        assert manifest["files"][filename]["sha256"] == w.sha256(files[filename])
        assert manifest["files"][filename]["bytes"] == len(files[filename])
    assert "api_key" not in json.dumps(manifest)


def test_export_mutation_and_invalid_json_blocked(monkeypatch):
    b = accepted(monkeypatch)
    b["records"][0]["candidate"][1]["content"] = "Contact siti@example.my"
    with pytest.raises(ValueError):
        w.build_exports(b, CONFIG, POLICY)
    for text in ('{"messages":NaN}', '{"messages":[],"extra":1}', '{"messages":[]}'):
        with pytest.raises(Exception):
            w.validate_export(text.encode(), "messages")


@pytest.mark.parametrize("url", ["file:///tmp/foo", "http://a:b@localhost/v1", "http://localhost/v1?api_key=x", "https://localhost/#fragment"])
def test_endpoint_rejects_credentials_and_invalid_urls(url):
    with pytest.raises(ValueError):
        w.validated_base_url(url)


def test_request_payload_and_response_validation(monkeypatch):
    captures = []
    response_data = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer())}}]}
    class Response:
        status_code = 200
        @property
        def text(self):
            return json.dumps(response_data)
    def transport(method, url, **kwargs):
        captures.append((method, url, kwargs))
        return Response()
    monkeypatch.setattr(w.requests, "request", transport)
    for mode in ["structured_outputs", "guided_json", "json_object"]:
        config = w.QwenConfig(mode=mode)
        result = w.verify_with_qwen(PAIR, config, POLICY)
        payload = captures[-1][2]["json"]
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert not captures[-1][2]["allow_redirects"]
        assert result["confidence"] == .99
        assert ("response_format" if mode == "json_object" else mode) in payload
    response_data["choices"][0]["finish_reason"] = "length"
    with pytest.raises(w.VerificationError):
        w.verify_with_qwen(PAIR, CONFIG, POLICY)
    response_data["choices"][0]["finish_reason"] = "stop"
    for malformed in ["```json\n{}\n```", '{"confidence":NaN}', json.dumps(answer(confidence=True)), json.dumps(answer(unsafe="false")), json.dumps(answer(extra="key"))]:
        response_data["choices"][0]["message"]["content"] = malformed
        with pytest.raises(w.VerificationError):
            w.verify_with_qwen(PAIR, CONFIG, POLICY)


def test_no_pii_transmission_on_direct_call(monkeypatch):
    called = []
    monkeypatch.setattr(w.requests, "request", lambda *a, **k: called.append(True))
    with pytest.raises(ValueError):
        w.verify_with_qwen([{"role": "user", "content": "mail siti@example.my"}, PAIR[1]], CONFIG, POLICY)
    assert not called
