"""Release export fix, SQLite persistence and the isolated PTSL reviewer portal."""
import time

import pytest
from fastapi.testclient import TestClient

import studio_api
import studio_portal
import watan_gold_studio as w

PROSE = ("Pelajar perlu menghantar tugasan sebelum tarikh akhir yang ditetapkan oleh pensyarah. " * 6).strip()
ROWS = b'{"text": "Pelajar perlu menghantar tugasan sebelum tarikh akhir."}\n{"text": "Silakan hubungi kami karena layanan nggak tersedia."}\n'
TOKEN = "r" * 40


class WordTokenizer:
    """Test double for the vLLM tokenizer endpoint (production never uses this)."""

    def __init__(self, config=None):
        self.ids, self.words = {}, {}

    def encode(self, text):
        out = []
        for word in text.split():
            if word not in self.ids:
                self.ids[word] = len(self.ids) + 1
                self.words[self.ids[word]] = word
            out.append(self.ids[word])
        return out

    def decode(self, tokens):
        return " ".join(self.words[t] for t in tokens)


def fake_qwen(record, config, policy):
    """Schema-valid Qwen answer; the real verify_record/classify/export gates run on top of it."""
    dialogue = record.get("kind", "dialogue") == "dialogue"
    clean = "Pelajar" in (record.get("original_text") or "")
    return {"explicit_content": False, "dialect_flag": False, "script": "rumi", "spelling_variant": "original", "language": "ms" if clean else "id", "lang_near_malay": not clean,
            "three_r": [], "damaged_chars": False, "confidence": 0.99 if clean else 0.8, "dbp_score": 0.95, "dbp_errors": [], "policy_errors": [], "factual_errors": [],
            "unsafe": False, "corrupted": False, "reason": "Semakan selesai.", "messages": record["original"] if dialogue else [], "text": "" if dialogue else record["original_text"],
            "stage": "GOLD_3" if dialogue else "GOLD_1", "category": "General_BM_QA", "domain": "Bahasa Melayu"}


@pytest.fixture()
def client(monkeypatch):
    studio_api.ws.reset()
    monkeypatch.setattr(w, "verify_with_qwen", fake_qwen)
    monkeypatch.setattr(w, "VLLMTokenizer", WordTokenizer)
    yield TestClient(studio_api.app)
    studio_api.ws.reset()


def wait(client):
    for _ in range(200):
        job = client.get("/api/job").json()
        if not job or job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def verified(client, files):
    client.post("/api/upload", files=files)
    client.post("/api/verify")
    wait(client)


def build_release(client):
    client.post("/api/release")
    for _ in range(200):
        rel = client.get("/api/state").json()["release"]
        if rel and rel["status"] in ("ready", "failed"):
            return rel
        time.sleep(0.05)
    raise AssertionError("release did not finish")


# ------------------------------------------------------------ export fix
def test_plain_text_upload_exports_only_non_empty_files(client):
    verified(client, [("files", ("notes.txt", PROSE.encode()))])
    state = client.get("/api/state").json()
    assert state["gold_ready"] >= 1 and state["preview"]["blocked"] == 0 and state["preview"]["defaulted"] >= 1
    rel = build_release(client)
    assert rel["status"] == "ready", rel["error"]
    files = rel["report"]["files"]
    assert files and all(f["rows"] > 0 and f["bytes"] > 0 for f in files)
    names = {f["name"] for f in files}
    assert set(rel["report"]["omitted_empty"]).isdisjoint(names)
    assert rel["report"]["omitted_empty"], "empty files must be listed as omitted, not packaged"
    import io
    import zipfile
    archive = zipfile.ZipFile(io.BytesIO(client.get("/api/release/download").content))
    jsonl = [n for n in archive.namelist() if n.endswith(".jsonl")]
    assert set(jsonl) == names and all(archive.getinfo(n).file_size > 0 for n in jsonl)


def test_declared_restricted_licence_stays_excluded_with_reason(client):
    row = b'{"text": "' + PROSE.encode() + b'", "licence_status": "restricted"}\n'
    verified(client, [("files", ("r.jsonl", row))])
    state = client.get("/api/state").json()
    assert state["preview"]["blocked"] >= 1
    rel = build_release(client)
    assert rel["status"] == "failed" and "Nothing could be exported" in rel["error"] and "licence" in rel["error"]
    assert rel["report"]["excluded"] and rel["report"]["excluded"][0]["count"] >= 1


def test_assume_licence_off_then_apply_defaults(client):
    client.post("/api/settings", json={"assume_licence": False})
    verified(client, [("files", ("notes.txt", PROSE.encode()))])
    assert client.get("/api/state").json()["preview"]["blocked"] >= 1
    assert build_release(client)["status"] == "failed"
    client.post("/api/settings", json={"assume_licence": True})
    assert client.post("/api/rights/apply").status_code == 200
    state = client.get("/api/state").json()
    assert state["preview"]["blocked"] == 0
    assert build_release(client)["status"] == "ready"


def test_settings_reject_empty_default_uses(client):
    assert client.post("/api/settings", json={"default_allowed_uses": []}).status_code == 422


# ----------------------------------------------------------- persistence
def test_state_survives_restart_including_reviews(client):
    verified(client, [("files", ("a.jsonl", ROWS))])
    queue = client.get("/api/records", params={"status": "PTSL_REVIEW"}).json()["items"]
    assert len(queue) == 1
    client.post(f"/api/records/{queue[0]['id']}/review", json={"action": "QUARANTINE"})
    before = client.get("/api/state").json()

    reborn = studio_api.Workspace(studio_api.ws.db.path)  # same file, brand-new process state
    assert reborn.batch_id == studio_api.ws.batch_id
    again = {r["id"]: r["status"] for r in reborn.batch["records"]}
    assert again == {r["id"]: r["status"] for r in studio_api.ws.batch["records"]}
    assert reborn.batch["bronze"][0]["sha256"] == studio_api.ws.batch["bronze"][0]["sha256"]
    assert reborn.db.decision_summary(reborn.batch_id)["total"] == 1
    assert before["ptsl"]["decisions"] == 1 and before["review"] == 0
    assert any(r.get("verification") for r in reborn.batch["records"])  # Agent 2 results kept


def test_start_over_archives_instead_of_deleting(client):
    verified(client, [("files", ("a.jsonl", ROWS))])
    bid = studio_api.ws.batch_id
    client.post("/api/reset")
    assert studio_api.ws.db.active_batch_id() is None
    assert studio_api.ws.db.load_batch(bid) is not None


# ----------------------------------------------------------- PTSL portal
@pytest.fixture()
def portal(client, monkeypatch):
    monkeypatch.setenv("PTSL_REVIEW_TOKEN", TOKEN)
    verified(client, [("files", ("a.jsonl", ROWS))])
    return client, {"X-Review-Token": TOKEN}


def test_portal_requires_its_own_token_and_cannot_reach_admin(portal, monkeypatch):
    client, headers = portal
    assert client.get("/api/ptsl/queue").status_code == 401
    assert client.get("/api/ptsl/queue", headers=headers).status_code == 200
    monkeypatch.setenv("STUDIO_ACCESS_TOKEN", "a" * 40)
    assert client.get("/api/state", headers=headers).status_code == 401  # review token is not an admin token
    assert client.post("/api/release", headers=headers).status_code == 401
    assert client.get("/api/auth", headers=headers).json()["ok"] is False


def test_portal_shows_masked_review_items_only(portal):
    client, headers = portal
    data = client.get("/api/ptsl/queue", headers=headers).json()
    assert data["waiting"] == 1 and data["review"] + data["warn"] == 1 and len(data["items"]) == 1
    item = data["items"][0]
    assert set(item) >= {"id", "gate", "original", "suggested", "flags", "category", "language", "reason"}
    assert all(k not in item for k in ("source", "sha256", "meta", "bronze", "settings"))
    text = repr(data)
    assert "base_url" not in text and "sha256" not in text and "qwen3" not in text.lower()


def test_portal_masks_pii_even_if_a_record_leaks(portal):
    client, headers = portal
    rid = client.get("/api/ptsl/queue", headers=headers).json()["items"][0]["id"]
    rec = studio_api.ws.db.load_record(studio_api.ws.batch_id, rid)
    rec["original_text"] = rec["text"] = "Hubungi ali@contoh.com untuk maklumat."
    studio_api.ws.db.save_records(studio_api.ws.batch_id, [rec])
    shown = client.get("/api/ptsl/queue", headers=headers).json()["items"][0]
    assert "ali@contoh.com" not in repr(shown)


def test_portal_decision_persists_and_updates_dashboard(portal):
    client, headers = portal
    before = client.get("/api/state").json()
    assert before["review"] == 1 and before["ptsl"]["portal"] == 0
    rid = client.get("/api/ptsl/queue", headers=headers).json()["items"][0]["id"]
    bad = client.post(f"/api/ptsl/records/{rid}/decision", json={"role": "AI_Engineer", "verdict": "approve"}, headers=headers)
    assert bad.status_code == 422
    assert client.post(f"/api/ptsl/records/{rid}/decision", json={"role": "Linguist", "verdict": "maybe"}, headers=headers).status_code == 422
    ok = client.post(f"/api/ptsl/records/{rid}/decision", json={"role": "Linguist", "verdict": "approve", "notes": "Looks fine."}, headers=headers)
    assert ok.status_code == 200 and ok.json()["status"] == "HUMAN_APPROVED"
    after = client.get("/api/state").json()
    assert after["review"] == 0 and after["gold_ready"] == before["gold_ready"] + 1
    assert after["ptsl"]["portal"] == 1 and after["ptsl"]["by_verdict"]["approve"] == 1
    assert client.post(f"/api/ptsl/records/{rid}/decision", json={"role": "Linguist", "verdict": "approve"}, headers=headers).status_code == 409
    row = studio_api.ws.db.recent_decisions(studio_api.ws.batch_id, 5, channel="ptsl_portal")[0]
    assert row["reviewer_role"] == "Linguist" and row["decided_at"].endswith("Z") or "+00:00" in row["decided_at"]


def test_portal_decision_is_visible_after_restart(portal):
    client, headers = portal
    rid = client.get("/api/ptsl/queue", headers=headers).json()["items"][0]["id"]
    client.post(f"/api/ptsl/records/{rid}/decision", json={"role": "Project_Lead", "verdict": "reject"}, headers=headers)
    reborn = studio_api.Workspace(studio_api.ws.db.path)
    assert {r["id"]: r["status"] for r in reborn.batch["records"]}[rid] == "QUARANTINE"


def test_portal_unsure_keeps_record_waiting(portal):
    client, headers = portal
    rid = client.get("/api/ptsl/queue", headers=headers).json()["items"][0]["id"]
    done = client.post(f"/api/ptsl/records/{rid}/decision", json={"role": "Domain_SME", "verdict": "unsure"}, headers=headers).json()
    assert done["waiting"] == 1
    assert client.get("/api/ptsl/queue", headers=headers).json()["items"][0]["unsure"] is True


def test_portal_edit_and_approve_validates_dialogue_shape(portal):
    client, headers = portal
    item = client.get("/api/ptsl/queue", headers=headers).json()["items"][0]
    url = f"/api/ptsl/records/{item['id']}/decision"
    body = {"role": "Linguist", "verdict": "edit_approve"}
    assert client.post(url, json={**body, "edited": ""}, headers=headers).status_code == 422
    if item["kind"] == "prose":
        assert client.post(url, json={**body, "edited": "Pelanggan boleh menghubungi kami kerana perkhidmatan tidak tersedia."}, headers=headers).status_code in (200, 422)
    else:
        assert client.post(url, json={**body, "edited": [{"role": "user", "content": "x"}]}, headers=headers).status_code == 422


def test_portal_notes_must_not_contain_personal_data(portal):
    client, headers = portal
    rid = client.get("/api/ptsl/queue", headers=headers).json()["items"][0]["id"]
    r = client.post(f"/api/ptsl/records/{rid}/decision", json={"role": "Linguist", "verdict": "approve", "notes": "call me at ali@contoh.com"}, headers=headers)
    assert r.status_code == 422


def test_production_needs_strong_review_token(client, monkeypatch):
    monkeypatch.setenv("STUDIO_ENV", "production")
    monkeypatch.setenv("STUDIO_ACCESS_TOKEN", "a" * 40)
    monkeypatch.setenv("PTSL_REVIEW_TOKEN", "short")
    assert client.get("/api/ptsl/queue").status_code == 503
    monkeypatch.setenv("PTSL_REVIEW_TOKEN", TOKEN)
    assert client.get("/api/ptsl/queue", headers={"X-Review-Token": TOKEN}).status_code == 200


def test_portal_only_mode_hides_admin_api_and_redirects(client, monkeypatch):
    monkeypatch.setenv("STUDIO_PORTAL_ONLY", "1")
    monkeypatch.setenv("PTSL_REVIEW_TOKEN", TOKEN)
    monkeypatch.setenv("STUDIO_ACCESS_TOKEN", "a" * 40)
    assert client.get("/api/state").status_code == 404
    assert client.post("/api/release").status_code == 404
    assert client.get("/api/ptsl/queue", headers={"X-Access-Token": "a" * 40}).status_code == 401  # admin token not accepted here
    assert client.get("/api/ptsl/queue", headers={"X-Review-Token": TOKEN}).status_code == 200


def test_core_role_rules_still_apply_through_the_portal():
    assert studio_portal.ROLES == ("Linguist", "Domain_SME", "Project_Lead")
    assert set(studio_portal.ROLES) <= set(w.REVIEWER_ROLES)
