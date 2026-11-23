import threading
import time

import watan_gold_studio as w
from test_four_tiers import CONFIG, POLICY, dialogues, ingest, response


def test_stop_keeps_finished_records_and_skips_the_rest(monkeypatch):
    batch = ingest(dialogues(12))
    stop = threading.Event()
    started = []

    def slow(record, *a):
        started.append(record["id"])
        if len(started) == 3:
            stop.set()  # the user presses Stop while three records have started
        time.sleep(0.05)
        return response(record, confidence=0.9)

    monkeypatch.setattr(w, "verify_with_qwen", slow)
    w.verify_records(batch["records"], CONFIG, POLICY, workers=2, stop=stop)
    verified = [r for r in batch["records"] if r.get("verification")]
    assert 1 <= len(verified) < 12
    assert len(started) < 12  # records that had not started were never sent to Qwen


def test_stopped_run_can_be_resumed(monkeypatch):
    batch = ingest(dialogues(8))
    stop = threading.Event()
    calls = []

    def fn(record, *a):
        calls.append(record["id"])
        if len(calls) == 2:
            stop.set()
        return response(record, confidence=0.9)

    monkeypatch.setattr(w, "verify_with_qwen", fn)
    w.verify_records(batch["records"], CONFIG, POLICY, workers=1, stop=stop)
    first = sum(1 for r in batch["records"] if r.get("verification"))
    assert 0 < first < 8
    monkeypatch.setattr(w, "verify_with_qwen", lambda record, *a: response(record, confidence=0.9))
    todo = [r for r in batch["records"] if not r.get("verification")]
    w.verify_records(todo, CONFIG, POLICY, workers=1)
    assert all(r.get("verification") for r in batch["records"])


def test_job_reports_stopped_state():
    import studio_api as api

    job = api.Job("verify", 5)
    assert job.view()["state"] == "running" and job.view()["stopping"] is False
    job.stop.set()
    assert job.view()["stopping"] is True
    job.finish()
    view = job.view()
    assert view["state"] == "stopped" and view["stopping"] is False


def test_stop_endpoint_needs_a_running_job():
    from fastapi.testclient import TestClient

    import studio_api as api

    c = TestClient(api.app)
    api.ws.job = None
    assert c.post("/api/stop").status_code == 409
    api.ws.job = api.Job("verify", 3)
    res = c.post("/api/stop")
    assert res.status_code == 200 and res.json()["stopping"] is True
    api.ws.job.finish()
    api.ws.job = None
