import time

import pytest
from fastapi.testclient import TestClient

import studio_api
import watan_gold_studio as w

ROWS = b'{"text": "Pelajar perlu menghantar tugasan sebelum tarikh akhir."}\n{"text": "Silakan hubungi kami karena layanan nggak tersedia."}\n'


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("STUDIO_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("STUDIO_ENV", raising=False)
    studio_api.ws.reset()

    def fake_verify(record, config, policy):
        record["status"] = "AUTO_PASS" if "Pelajar" in record["original_text"] else "PTSL_REVIEW"
        record["verification"] = {"profile_sha256": w.profile_hash(config, policy), "result": {"confidence": 0.98 if record["status"] == "AUTO_PASS" else 0.85, "reason": "test", "unsafe": False, "corrupted": False, "damaged_chars": False, "explicit_content": False, "three_r": [], "dbp_errors": [], "policy_errors": [], "factual_errors": [], "dialect_flag": False, "script": "rumi", "spelling_variant": "none"}}
        record["history"].append({"action": "VERIFY"})

    monkeypatch.setattr(w, "verify_record", fake_verify)
    yield TestClient(studio_api.app)
    studio_api.ws.reset()


def wait(client):
    for _ in range(100):
        job = client.get("/api/job").json()
        if not job or job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_steps_require_data_first(client):
    assert client.get("/api/state").json()["has_batch"] is False
    assert client.post("/api/verify").status_code == 409
    assert client.get("/api/records").status_code == 409
    assert client.post("/api/release").status_code == 409


def test_upload_registers_bronze_and_silver(client):
    state = client.post("/api/upload", files=[("files", ("a.jsonl", ROWS))]).json()
    assert state["records"] == 2 and state["step"] == 1
    assert len(state["bronze"][0]["sha256"]) == 64 and state["bronze"][0]["name"] == "a.jsonl"
    assert state["silver"]["near_duplicate"]["enabled"] is True


def test_verify_shows_progress_then_gate_counts(client):
    client.post("/api/upload", files=[("files", ("a.jsonl", ROWS))])
    first = client.post("/api/verify").json()
    assert first["kind"] == "verify" and first["total"] == 2
    job = wait(client)
    assert job["done"] == 2 and job["state"] == "done" and sum(job["counts"].values()) == 2
    state = client.get("/api/state").json()
    assert state["verified"] and state["gates"]["PASS"] == 1 and state["review"] == 1
    items = client.get("/api/records", params={"gate": "PASS"}).json()["items"]
    assert len(items) == 1 and items[0]["confidence"] == 0.98


def test_review_then_release(client):
    client.post("/api/upload", files=[("files", ("a.jsonl", ROWS))])
    client.post("/api/verify")
    wait(client)
    queue = client.get("/api/records", params={"status": "PTSL_REVIEW"}).json()["items"]
    assert len(queue) == 1
    detail = client.get(f"/api/records/{queue[0]['id']}").json()
    assert detail["original_text"] and "candidate" in detail
    assert client.post(f"/api/records/{queue[0]['id']}/review", json={"action": "nonsense"}).status_code == 422
    done = client.post(f"/api/records/{queue[0]['id']}/review", json={"action": "QUARANTINE"}).json()
    assert done["gate"] == "BLOCK"
    state = client.get("/api/state").json()
    assert state["review"] == 0 and state["gold_ready"] == 1


def test_settings_are_validated(client):
    assert client.post("/api/settings", json={"gold_version": "bad version!"}).status_code == 422
    assert client.post("/api/settings", json={"workers": 500, "sample_pct": 0}).json()["workers"] == 64
    assert client.post("/api/settings", json={"reviewer": "alice"}).status_code == 422


def test_access_token_protects_api(client, monkeypatch):
    monkeypatch.setenv("STUDIO_ACCESS_TOKEN", "x" * 40)
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/auth").json() == {"required": True, "ok": False}
    assert client.get("/api/state", headers={"X-Access-Token": "x" * 40}).status_code == 200


def test_production_requires_strong_token(client, monkeypatch):
    monkeypatch.setenv("STUDIO_ENV", "production")
    monkeypatch.setenv("STUDIO_ACCESS_TOKEN", "short")
    assert client.get("/api/state").status_code == 503
