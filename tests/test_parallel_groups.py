import json
import threading
import time

from fastapi.testclient import TestClient

import studio_api as api
import watan_gold_studio as w
from test_four_tiers import response


def jsonl(n, tag):
    return "\n".join(json.dumps({"text": f"Ayat {tag} nombor {i} dalam bahasa Melayu yang cukup panjang untuk ujian."}) for i in range(n)).encode()


def setup(client, layout):
    client.post("/api/reset")
    files = [("files", (f"{folder}/f{i}.jsonl", jsonl(n, f"{folder}{i}"), "x")) for folder, counts in layout.items() for i, n in enumerate(counts)]
    assert client.post("/api/upload", files=files).status_code == 200


def wait_idle(client, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if not api.running():
            return
        time.sleep(0.05)
    raise AssertionError("jobs did not finish")


def test_groups_run_in_parallel_and_are_isolated(monkeypatch):
    c = TestClient(api.app)
    setup(c, {"a": [6], "b": [6], "c": [6]})
    lock, live, peak = threading.Lock(), set(), [0]

    def slow(record, *args):
        gmap = api.group_map(api.ws.batch)
        group = gmap[record["source_id"]]
        with lock:
            live.add(group)
            peak[0] = max(peak[0], len(live))
        time.sleep(0.05)
        with lock:
            live.discard(group)
        return response(record, confidence=0.9)

    monkeypatch.setattr(w, "verify_with_qwen", slow)
    res = c.post("/api/groups/verify", json={"parallel": 3})
    assert res.status_code == 200, res.text
    assert sorted(res.json()["started"]) == ["a", "b", "c"]
    wait_idle(c)
    assert peak[0] >= 2  # more than one folder was in flight at the same moment
    for g in c.get("/api/pipeline").json()["groups"]:
        assert g["gate"]["checked"] == g["records"] and g["job"]["state"] == "done"


def test_parallel_limit_queues_the_rest(monkeypatch):
    c = TestClient(api.app)
    setup(c, {"a": [4], "b": [4], "c": [4]})
    gate = threading.Event()
    monkeypatch.setattr(w, "verify_with_qwen", lambda record, *a: (gate.wait(5), response(record, confidence=0.9))[1])
    c.post("/api/groups/verify", json={"parallel": 1})
    time.sleep(0.3)
    states = sorted(g["job"]["state"] for g in c.get("/api/pipeline").json()["groups"])
    assert states == ["queued", "queued", "running"]
    gate.set()
    wait_idle(c)


def test_stop_one_group_leaves_the_others_running(monkeypatch):
    c = TestClient(api.app)
    setup(c, {"a": [30], "b": [30]})
    monkeypatch.setitem(api.ws.settings, "workers", 2)  # slow enough that Stop lands mid-run
    monkeypatch.setattr(w, "verify_with_qwen", lambda record, *a: (time.sleep(0.03), response(record, confidence=0.9))[1])
    c.post("/api/groups/verify", json={"parallel": 2})
    time.sleep(0.15)
    assert c.post("/api/stop", json={"groups": ["a"]}).status_code == 200
    wait_idle(c)
    by = {g["key"]: g for g in c.get("/api/pipeline").json()["groups"]}
    assert by["a"]["job"]["state"] == "stopped" and by["a"]["gate"]["checked"] < by["a"]["records"]
    assert by["b"]["job"]["state"] == "done" and by["b"]["gate"]["checked"] == by["b"]["records"]


def test_stop_cancels_queued_groups(monkeypatch):
    c = TestClient(api.app)
    setup(c, {"a": [4], "b": [4]})
    gate = threading.Event()
    monkeypatch.setattr(w, "verify_with_qwen", lambda record, *a: (gate.wait(5), response(record, confidence=0.9))[1])
    c.post("/api/groups/verify", json={"parallel": 1})
    time.sleep(0.2)
    c.post("/api/stop", json={"groups": ["b"]})
    by = {g["key"]: g for g in c.get("/api/pipeline").json()["groups"]}
    assert by["b"]["job"]["state"] == "stopped" and by["b"]["gate"]["checked"] == 0
    gate.set()
    wait_idle(c)


def test_other_groups_can_be_browsed_while_one_runs(monkeypatch):
    c = TestClient(api.app)
    setup(c, {"a": [5], "b": [5]})
    gate = threading.Event()
    monkeypatch.setattr(w, "verify_with_qwen", lambda record, *a: (gate.wait(5), response(record, confidence=0.9))[1])
    c.post("/api/groups/verify", json={"groups": ["a"]})
    time.sleep(0.2)
    assert c.get("/api/records", params={"group": "a"}).status_code == 423
    assert c.get("/api/records", params={"group": "b"}).status_code == 200
    assert c.get("/api/records").status_code == 423  # the whole batch is not idle
    assert c.post("/api/reset").status_code == 423
    gate.set()
    wait_idle(c)
    assert c.get("/api/records", params={"group": "a"}).json()["total"] == 5


def test_combined_job_view_and_validation(monkeypatch):
    c = TestClient(api.app)
    setup(c, {"a": [3], "b": [3]})
    assert c.post("/api/groups/verify", json={"groups": ["nope"]}).status_code == 422
    monkeypatch.setattr(w, "verify_with_qwen", lambda record, *a: response(record, confidence=0.9))
    c.post("/api/groups/verify", json={})
    wait_idle(c)
    job = c.get("/api/job").json()
    assert job["state"] == "done" and job["done"] == job["total"] == 6
    assert c.post("/api/groups/verify", json={}).status_code == 409  # nothing left to verify
    st = c.get("/api/state").json()
    assert st["folders"] == 2 and st["files"] == 2
