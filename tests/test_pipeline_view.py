import json

from fastapi.testclient import TestClient

import studio_api as api
import watan_gold_studio as w


def jsonl(n, tag):
    return "\n".join(json.dumps({"text": f"Ayat {tag} nombor {i} dalam bahasa Melayu yang cukup panjang untuk ujian."}) for i in range(n)).encode()


def upload(client, files):
    return client.post("/api/upload", files=[("files", (name, data, "application/octet-stream")) for name, data in files])


def test_source_folder_is_kept_and_sanitised():
    assert w.source_folder("a/b/c.txt") == "a/b"
    assert w.source_folder("c.txt") == ""
    assert w.source_folder("../../etc/x.txt") == "etc"
    assert api.upload_path("../../a/./b.txt") == "a/b.txt"
    assert api.upload_path("C:\\x\\y.txt") == "x/y.txt"


def test_pipeline_groups_by_folder_and_file():
    c = TestClient(api.app)
    c.post("/api/reset")
    res = upload(c, [("kampung/a.jsonl", jsonl(5, "a")), ("kampung/b.jsonl", jsonl(3, "b")), ("bandar/c.jsonl", jsonl(4, "c"))])
    assert res.status_code == 200, res.text
    data = c.get("/api/pipeline").json()
    assert data["files"] == 3 and data["folders"] == 2
    by_folder = {g["key"]: g for g in data["groups"]}
    assert by_folder["kampung"]["files"] == 2 and by_folder["kampung"]["records"] == 8
    assert by_folder["bandar"]["records"] == 4
    assert data["total"]["records"] == 12
    assert data["total"]["remaining"]["quality_gate"] == data["total"]["gate"]["waiting"]
    files = c.get("/api/pipeline", params={"by": "file", "folder": "kampung"}).json()
    assert sorted(g["label"] for g in files["groups"]) == ["a.jsonl", "b.jsonl"]
    assert c.get("/api/pipeline", params={"q": "band"}).json()["count"] == 1
    assert c.get("/api/pipeline", params={"sort": "name"}).json()["groups"][0]["key"] == "bandar"


def test_pipeline_counts_follow_record_status():
    c = TestClient(api.app)
    c.post("/api/reset")
    upload(c, [("x/a.jsonl", jsonl(4, "a"))])
    rows = api.ws.batch["records"]
    rows[0]["status"] = "AUTO_PASS"
    rows[1]["status"] = "PTSL_REVIEW"
    rows[2]["status"] = "HUMAN_APPROVED"
    rows[3]["status"] = "QUARANTINE"
    t = c.get("/api/pipeline").json()["total"]
    assert t["gate"]["waiting"] == 0 and t["gate"]["checked"] == 4
    assert t["ptsl"] == {"needed": 2, "done": 1, "waiting": 1}
    assert t["gold"] == 2
    assert t["remaining"]["total"] == 1 and t["percent"] == 75 and t["state"] == "review"


def test_pipeline_needs_a_batch():
    c = TestClient(api.app)
    c.post("/api/reset")
    assert c.get("/api/pipeline").status_code == 409
