"""WaRiSAN Gold Studio web API: a thin HTTP layer over the existing pipeline functions.

Serves the React app from web/dist and exposes the four pipeline steps
(Bronze & Silver -> Quality Gate -> PTSL review -> Gold release) plus an isolated
PTSL reviewer portal (/review, see studio_portal.py). All pipeline logic stays in
watan_gold_studio.py / silver.py; nothing is re-implemented here.

Run:  python studio_api.py            (or: uvicorn studio_api:app --host 127.0.0.1 --port 8503)
Env:  QWEN_BASE_URL, QWEN_MODEL, QWEN_API_KEY, QWEN_WORKERS, STUDIO_ACCESS_TOKEN,
      PTSL_REVIEW_TOKEN (reviewer portal), STUDIO_DB (default ./studio_state.db),
      STUDIO_PORTAL_ONLY=1 (serve only the reviewer portal in this process)
State is persisted to SQLite (studio_db.py), so a restart keeps batches, hashes, Silver
records, Agent 2 results and every human decision.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import secrets
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

import requests
from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import silver
import studio_db
import studio_portal
import watan_gold_studio as w

log = logging.getLogger("studio_api")
ROOT = Path(__file__).parent
DIST = ROOT / "web" / "dist"
EXAMPLES = ROOT / "examples"
MAX_UPLOAD = 50 * 1024 * 1024
PREVIEW = 220
ALLOWED_USES = ("train", "rag_display", "eval")
# Which allowed uses each Gold stage needs before its rows may enter a trainer/index/eval JSONL file.
STAGE_USES = {"GOLD_1": ("train",), "GOLD_2": ("train", "rag_display"), "GOLD_3": ("train",), "GOLD_4": ("eval",)}
PRODUCT_FILES = {"GOLD_1": "gold_1_canonical_cpt.jsonl", "GOLD_2": "gold_2_rag_chunks.jsonl", "GOLD_3": "gold_3_sft_messages.jsonl", "GOLD_4": "gold_4_eval_benchmark.jsonl"}
REASONS = {"licence_or_allowed_use_not_approved": "licence is not approved or the required use is not in allowed_uses",
           "held_out_intent_or_answer_stem": "held out of training to protect the evaluation set (same intent or answer as a benchmark item)",
           "held_out_source_lineage": "held out of training to protect the evaluation set (same source document as a benchmark item)"}

app = FastAPI(title="WaRiSAN Gold Studio", version="2.1.0", docs_url=None if os.getenv("STUDIO_ENV") == "production" else "/docs", redoc_url=None)


# ---------------------------------------------------------------- workspace
def default_settings():
    return {
        "base_url": os.getenv("QWEN_BASE_URL", "http://127.0.0.1:8000/v1"),
        "model": os.getenv("QWEN_MODEL", "qwen3-14b"),
        "workers": max(1, min(64, int(os.getenv("QWEN_WORKERS", "12") or 12))),
        "timeout": 120, "max_tokens": 1536, "target": "AUTO", "gold_version": "v0.1",
        "silver_enabled": True, "near_dup_enabled": True, "near_dup_threshold": 0.85,
        "agent1_enabled": False, "sampling_enabled": False, "sample_pct": 10,
        "reviewer": "AI_Engineer", "holdout_enabled": False, "include_conversations": False,
        # Rights defaults: files that declare no licence_status / allowed_uses are treated as cleared by the uploader.
        "assume_licence": True, "default_allowed_uses": list(ALLOWED_USES),
    }


def make_config(settings):
    s = settings
    return w.QwenConfig(s["base_url"], s["model"].strip(), os.getenv("QWEN_API_KEY", ""), "structured_outputs", int(s["timeout"]), int(s["max_tokens"]), s["target"])


class Workspace:
    """The working copy of the active batch. SQLite is the source of truth; memory is a fast cache."""

    def __init__(self, db_path=None):
        self.lock = threading.RLock()
        self.db = studio_db.StudioDB(db_path)
        self.fresh()
        self.load()

    def fresh(self):
        self.batch, self.batch_id = None, None
        self.settings = default_settings()
        self.job = None
        self.release = None
        self.seen_seq = 0
        self.policy = w.strict_json(w.POLICY_PATH.read_text(encoding="utf-8"))

    def load(self):
        """Restore settings and the active batch after a restart."""
        defaults = default_settings()
        stored = self.db.load_settings() or {}
        self.settings = {**defaults, **{k: v for k, v in stored.items() if k in defaults}}
        for key, env in (("base_url", "QWEN_BASE_URL"), ("model", "QWEN_MODEL")):
            if os.getenv(env):  # an explicitly configured server always wins over a stale saved value
                self.settings[key] = os.getenv(env)
        self.db.save_settings(self.settings)
        self.batch_id = self.db.active_batch_id()
        self.batch = self.db.load_batch(self.batch_id) if self.batch_id else None
        self.seen_seq = self.db.max_seq()

    def reset(self):
        """'Start over': a new empty workspace. Earlier batches stay archived in the database."""
        with self.lock:
            self.fresh()
            self.db.deactivate()
            self.db.save_settings(self.settings)

    def config(self):
        return make_config(self.settings)

    def profile(self):
        return w.profile_hash(self.config(), self.policy)

    def profile_from_db(self):
        """Profile hash from the *persisted* settings; what the portal must match to record a verdict."""
        s = {**default_settings(), **(self.db.load_settings() or {})}
        return w.profile_hash(make_config(s), self.policy)

    def sync(self):
        """Pull changes written through the database (PTSL portal, other processes) into memory."""
        with self.lock:
            active = self.db.active_batch_id()
            busy = bool(self.job and self.job.state == "running")
            if active != self.batch_id:
                if not busy:
                    self.batch_id = active
                    self.batch = self.db.load_batch(active) if active else None
                    self.release, self.seen_seq = None, self.db.max_seq()
                return
            if not self.batch:
                return
            top = self.db.max_seq()
            if top <= self.seen_seq:
                return
            ids = {c["record_id"] for c in self.db.decisions_after(self.seen_seq, self.batch_id)}
            fresh = {r["id"]: r for r in self.db.load_records(self.batch_id, ids)}
            records = self.batch["records"]
            for i, rec in enumerate(records):
                if rec["id"] in fresh:
                    records[i] = fresh[rec["id"]]
            self.seen_seq = top
            if ids:
                self.release = None


ws = Workspace()


class Job:
    """Progress of one background step, polled by the UI."""

    def __init__(self, kind, total):
        self.kind, self.total, self.done = kind, total, 0
        self.state, self.error = "running", None
        self.stop = threading.Event()
        self.started, self.finished = time.time(), None
        self.counts, self.recent = Counter(), []
        self.lock = threading.Lock()

    def tick(self, label, detail=None):
        with self.lock:
            self.done += 1
            self.counts[label] += 1
            self.recent = ([{"n": self.done, "label": label, **(detail or {})}] + self.recent)[:8]

    def finish(self, error=None):
        with self.lock:
            state = "error" if error else ("stopped" if self.stop.is_set() else "done")
            self.state, self.error, self.finished = state, error, time.time()

    def view(self):
        with self.lock:
            elapsed = (self.finished or time.time()) - self.started
            rate = self.done / elapsed if elapsed > 0 else 0
            eta = (self.total - self.done) / rate if rate and self.state == "running" else 0
            return {"kind": self.kind, "state": self.state, "error": self.error, "stopping": self.stop.is_set() and self.state == "running", "done": self.done, "total": self.total,
                    "elapsed": round(elapsed, 1), "rate": round(rate, 2), "eta": round(eta, 1), "counts": dict(self.counts), "recent": list(self.recent)}


def running():
    return bool(ws.job and ws.job.state == "running")


# ------------------------------------------------------------------ helpers
def need_batch():
    ws.sync()
    if not ws.batch:
        raise HTTPException(409, "Upload data first (step 1).")
    return ws.batch


def need_idle():
    if running():
        raise HTTPException(423, "A processing step is still running.")


def confidence(r):
    v = r.get("verification")
    return round(v["result"]["confidence"], 3) if v else None


def row(r, detail=False):
    gate = w.gate_outcome(r)
    meta = r.get("meta", {})
    out = {"id": r["id"], "gate": gate, "status": r["status"], "kind": r.get("kind", "dialogue"), "source": meta.get("source") or "upload",
           "preview": (r.get("original_text") or w.record_text(r) or r.get("reason") or "(no text)")[:PREVIEW], "confidence": confidence(r), "stage": r.get("stage"),
           "category": r.get("category"), "reason": r.get("reason", ""), "warnings": w.gate_reasons(r), "held": r.get("sampling") == "held",
           "silver": {"messy": bool(r.get("silver", {}).get("messy")), "agent1": r.get("silver", {}).get("agent1", {}).get("status"), "rules": r.get("silver", {}).get("applied", []),
                      "near_duplicate_of": r.get("near_duplicate_of")}}
    if detail:
        v = (r.get("verification") or {}).get("result") or {}
        out.update(original=r.get("original"), original_text=r.get("original_text", ""), candidate=r.get("candidate"), text=r.get("text", ""),
                   verification={k: v.get(k) for k in ("confidence", "language", "category", "stage", "reason", "dbp_errors", "three_r", "damaged_chars", "explicit_content") if k in v},
                   history=[{"action": h.get("action"), "at": h.get("at"), "role": h.get("reviewer_role")} for h in r.get("history", [])][-12:],
                   bulk_ok=w.bulk_eligible(r, ws.profile()))
    return out


# -------------------------------------------------------- licence / rights
def apply_rights_defaults(records, uses, force_status=True):
    """Give records that declare no rights a valid default (`licence_status="approved"` + allowed uses).

    A value declared in the source file always wins (e.g. a row marked "restricted" stays excluded).
    Defaulted records are tagged so the release manifest and UI can say so. Returns the count changed.
    """
    uses = [u for u in uses if u in ALLOWED_USES]
    changed = 0
    for r in records:
        meta = r.setdefault("meta", {})
        touched = False
        if force_status and not str(meta.get("licence_status") or "").strip():
            meta["licence_status"], meta["licence_defaulted"], touched = "approved", True, True
        if not meta.get("allowed_uses") and uses:
            meta["allowed_uses"], meta["allowed_uses_defaulted"], touched = list(uses), True, True
        changed += touched
    return changed


def release_preview(rows):
    """What will reach Gold and how many records the licence gate would drop (shown before building)."""
    gold = [r for r in rows if r["status"] in w.GOLD_STATES]
    stages, blocked = Counter(r.get("stage") or "UNROUTED" for r in gold), Counter()
    for r in gold:
        meta = r.get("meta", {})
        if str(meta.get("licence_status") or "UNKNOWN").casefold() not in {"approved", "permitted"}:
            blocked["licence_status"] += 1
        elif any(u not in meta.get("allowed_uses", []) for u in STAGE_USES.get(r.get("stage"), ())):
            blocked["allowed_uses"] += 1
    defaulted = sum(1 for r in gold if r.get("meta", {}).get("licence_defaulted") or r.get("meta", {}).get("allowed_uses_defaulted"))
    return {"records": len(gold), "by_stage": dict(stages), "blocked": sum(blocked.values()), "blocked_reasons": dict(blocked), "defaulted": defaulted}


def overview():
    ws.sync()
    b = ws.batch
    if not b:
        return {"has_batch": False, "step": 1, "settings": public_settings(), "job": ws.job.view() if ws.job else None}
    rows = b["records"]
    gates = w.gate_counts(rows)
    pending = sum(1 for r in rows if r["status"] == "PENDING" and r.get("sampling") != "held")
    review = sum(1 for r in rows if r["status"] == "PTSL_REVIEW")
    verified = any(r.get("verification") for r in rows)
    gold = sum(1 for r in rows if r["status"] in w.GOLD_STATES)
    step = 1 if not verified and pending else (3 if review else (4 if gold else 2))
    silver_meta = b.get("silver", {})
    decisions = ws.db.decision_summary(ws.batch_id)
    return {"has_batch": True, "step": step, "verified": verified, "records": len(rows), "gates": gates, "pending": pending, "review": review, "gold_ready": gold,
            "bronze": [{"name": f.get("filename", f.get("name")), "sha256": f.get("sha256"), "bytes": f.get("bytes", f.get("size")), "at": f.get("ingested_at", f.get("at"))} for f in b.get("bronze", [])],
            "silver": {"cleaning": silver_meta.get("cleaning_enabled", False), "near_duplicate": silver_meta.get("near_duplicate", {}),
                       "agent1": silver_meta.get("agent1", {}), "messy": sum(1 for r in rows if r.get("silver", {}).get("messy")),
                       "duplicates": sum(1 for r in rows if r["status"] == "DUPLICATE"), "held": sum(1 for r in rows if r.get("sampling") == "held")},
            "issues": [str(i)[:200] for i in b.get("issues", [])][:10], "settings": public_settings(), "job": ws.job.view() if ws.job else None,
            "release": release_view(), "preview": release_preview(rows),
            "ptsl": {"decisions": decisions["total"], "portal": decisions["by_channel"].get("ptsl_portal", 0), "dashboard": decisions["by_channel"].get("dashboard", 0),
                     "by_verdict": decisions["by_verdict"], "last_at": decisions["last_at"]},
            "persisted": True}


def public_settings():
    s = dict(ws.settings)
    s["roles"] = list(w.REVIEWER_ROLES)
    return s


# ------------------------------------------------------------------ release
class StudioRelease:
    """One immutable snapshot -> Gold files. Only JSONL files with at least one row are packaged."""

    def __init__(self, batch, config, policy, settings):
        self.batch, self.config, self.policy = copy.deepcopy(batch), config, copy.deepcopy(policy)
        self.settings, self.version = dict(settings), settings["gold_version"]
        self.status, self.error, self.files, self.archive, self.report = "pending", None, None, None, None
        self.lock = threading.Lock()

    def build(self):
        with self.lock:
            self.status, self.error = "building", None
            try:
                s = self.settings
                files = w.build_exports(self.batch, self.config, self.policy, 0.12, None, 64, s["gold_version"], s["holdout_enabled"], s["include_conversations"])
                manifest = json.loads(files["manifest.json"])
                jsonl = {n: d for n, d in files.items() if n.endswith(".jsonl")}
                counts = {n: len(d.decode("utf-8").splitlines()) for n, d in jsonl.items()}
                keep = {n: d for n, d in jsonl.items() if counts[n] > 0}
                self.report = self.make_report(manifest, counts, keep, files)
                if not keep:
                    raise ValueError(self.nothing_message())
                self.files, self.archive, self.status = files, w.bundle_zip(keep), "ready"
            except (ValueError, w.VerificationError) as exc:
                self.status, self.error = "failed", str(exc)
            except Exception:  # never leak a stack trace to the browser
                log.exception("release build failed")
                self.status, self.error = "failed", "The release could not be built; see the server log."

    def make_report(self, manifest, counts, keep, files):
        groups = Counter((e["product"], e["required_use"], e["reason"]) for e in manifest.get("governance_exclusions", []))
        excluded = [{"file": PRODUCT_FILES.get(p, p), "product": p, "use": use, "reason": REASONS.get(reason, reason), "count": n} for (p, use, reason), n in sorted(groups.items())]
        held = Counter(e["reason"] for e in manifest.get("leakage_guard", {}).get("excluded_records", []))
        excluded += [{"file": "training files", "product": "-", "use": "-", "reason": REASONS.get(reason, reason), "count": n} for reason, n in sorted(held.items())]
        sample = sorted({e["record_id"] for e in manifest.get("governance_exclusions", [])})[:10]
        preview = release_preview(self.batch["records"])
        warnings = list(manifest.get("warnings", []))
        if preview["defaulted"]:
            warnings.append(f"{preview['defaulted']} record(s) had no licence declared in their file, so the Studio default (licence approved for the selected uses) was applied.")
        return {"files": [{"name": n, "rows": counts[n], "bytes": len(keep[n]), "sha256": w.sha256(keep[n])} for n in sorted(keep)],
                "omitted_empty": sorted(n for n in counts if counts[n] == 0), "rows_total": sum(counts[n] for n in keep),
                "records_in_gold": preview["records"], "excluded": excluded, "excluded_sample": sample, "warnings": warnings, "release_id": manifest.get("release_id")}

    def nothing_message(self):
        why = "; ".join(f"{e['count']} record(s) skipped from {e['file']}: {e['reason']}" for e in self.report["excluded"]) or "no record produced a row for any output file"
        return (f"Nothing could be exported: {self.report['records_in_gold']} record(s) reached Gold but every output file is empty ({why}). "
                "Set the licence and allowed uses under 'Add your data → Rights', then rebuild.")

    def view(self):
        return {"status": self.status, "error": self.error, "version": self.version, "report": self.report}


def release_view():
    return ws.release.view() if ws.release else None


# ----------------------------------------------------------------- security
@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    production = os.getenv("STUDIO_ENV") == "production"
    if path.startswith("/api/ptsl/"):
        failure = studio_portal.portal_guard(request, production)
        if failure:
            return failure
    else:
        required = os.getenv("STUDIO_ACCESS_TOKEN", "")
        if studio_portal.portal_only() and path.startswith("/api/"):
            return JSONResponse({"detail": "Not found."}, status_code=404)
        if production and len(required) < 32 and not studio_portal.portal_only():
            return JSONResponse({"detail": "Production startup requires STUDIO_ACCESS_TOKEN with at least 32 characters."}, status_code=503)
        if required and path.startswith("/api/") and path != "/api/auth":
            given = request.headers.get("x-access-token", "") or request.query_params.get("token", "")
            if not secrets.compare_digest(given.encode(), required.encode()):
                return JSONResponse({"detail": "Access token required."}, status_code=401)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store" if path.startswith("/api/") else response.headers.get("Cache-Control", "")
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/api/auth")
def auth(request: Request):
    required = os.getenv("STUDIO_ACCESS_TOKEN", "")
    given = request.headers.get("x-access-token", "")
    return {"required": bool(required), "ok": (not required) or secrets.compare_digest(given.encode(), required.encode())}


app.include_router(studio_portal.create_router(lambda: ws.db, lambda: ws.profile_from_db(), lambda: ws.sync()))


# ---------------------------------------------------------------- endpoints
@app.get("/api/health")
def health():
    s = ws.settings
    try:
        r = requests.get(w.validated_base_url(s["base_url"]) + "/models", timeout=3, headers={"Authorization": f"Bearer {os.getenv('QWEN_API_KEY', '')}"} if os.getenv("QWEN_API_KEY") else None)
        names = [m.get("id") for m in r.json().get("data", [])]
        return {"ok": s["model"] in names, "model": s["model"], "available": names[:6]}
    except Exception:
        return {"ok": False, "model": s["model"], "available": []}


@app.get("/api/state")
def state():
    return overview()


@app.post("/api/settings")
def settings(patch: dict = Body(...)):
    need_idle()
    defaults, new = default_settings(), dict(ws.settings)
    for key, value in patch.items():
        if key not in defaults:
            continue
        try:
            if isinstance(defaults[key], bool):
                new[key] = bool(value)
            elif isinstance(defaults[key], list):
                if not isinstance(value, list):
                    raise ValueError
                new[key] = [u for u in ALLOWED_USES if u in value]
            elif isinstance(defaults[key], int):
                new[key] = int(value)
            elif isinstance(defaults[key], float):
                new[key] = float(value)
            else:
                new[key] = str(value)
        except (TypeError, ValueError):
            raise HTTPException(422, f"Invalid value for {key}.") from None
    new["workers"] = max(1, min(64, new["workers"]))
    new["sample_pct"] = max(1, min(100, new["sample_pct"]))
    new["near_dup_threshold"] = max(0.5, min(1.0, new["near_dup_threshold"]))
    try:
        w.validated_base_url(new["base_url"])
        w.valid_gold_version(new["gold_version"])
        if not new["model"].strip():
            raise ValueError("Model is required.")
        if new["reviewer"] not in w.REVIEWER_ROLES:
            raise ValueError("Unknown reviewer role.")
        if new["assume_licence"] and not new["default_allowed_uses"]:
            raise ValueError("Choose at least one allowed use for files that do not declare their licence.")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    ws.settings = new  # only valid settings are ever stored
    ws.db.save_settings(new)
    return public_settings()


def start_agent1(batch, batch_id):
    eligible = [r for r in batch["records"] if w.agent1_eligible(r)]
    job = Job("agent1", len(eligible))
    ws.job = job
    config = ws.config()

    def work():
        try:
            w.run_agent1(batch, config, workers=ws.settings["workers"], on_progress=lambda done, total: setattr(job, "done", done), stop=job.stop)
            if not job.stop.is_set():
                job.done = job.total
            job.counts.update(batch.get("silver", {}).get("agent1", {}))
            ws.db.save_records(batch_id, batch["records"])
            ws.db.save_batch_meta(batch_id, batch)
            job.finish()
        except Exception as exc:  # surfaced to the UI, never a stack trace
            job.finish(str(exc)[:200] or "Agent 1 failed.")

    threading.Thread(target=work, daemon=True).start()


def ingest(files):
    s = ws.settings
    try:
        rules = silver.parse_rules(silver.DEFAULT_SILVER_YAML) if s["silver_enabled"] else None
        batch = w.ingest_files(files, silver_rules=rules, near_dup_threshold=s["near_dup_threshold"] if s["near_dup_enabled"] else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if not batch["records"]:
        raise HTTPException(422, "No usable rows found. " + "; ".join(str(i) for i in batch["issues"][:2]))
    if s["assume_licence"]:
        apply_rights_defaults(batch["records"], s["default_allowed_uses"])
    batch_id = uuid.uuid4().hex
    ws.db.save_batch(batch_id, batch)  # persisted before anything else can fail
    ws.batch, ws.batch_id, ws.release, ws.job, ws.seen_seq = batch, batch_id, None, None, ws.db.max_seq()
    if s["agent1_enabled"] and any(w.agent1_eligible(r) for r in batch["records"]):
        start_agent1(batch, batch_id)
    return overview()


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    need_idle()
    data, total = [], 0
    for f in files:
        blob = await f.read()
        total += len(blob)
        if total > MAX_UPLOAD:
            raise HTTPException(413, "Upload exceeds 50 MB.")
        data.append((Path(f.filename or "upload").name, blob))
    with ws.lock:
        return ingest(data)


@app.post("/api/sample")
def sample():
    need_idle()
    with ws.lock:
        return ingest([(p.name, p.read_bytes()) for p in sorted(EXAMPLES.glob("sample_*.jsonl"))])


@app.post("/api/rights/apply")
def apply_rights():
    """Give already-uploaded records without a declared licence the Studio default (explicit, one click)."""
    need_idle()
    batch = need_batch()
    with ws.lock:
        s = ws.settings
        if not s["default_allowed_uses"]:
            raise HTTPException(422, "Choose at least one allowed use first.")
        todo = [r for r in batch["records"] if r["status"] != "DUPLICATE"]
        before = {r["id"]: json.dumps(r.get("meta", {}), sort_keys=True) for r in todo}
        apply_rights_defaults(todo, s["default_allowed_uses"])
        changed = [r for r in todo if json.dumps(r.get("meta", {}), sort_keys=True) != before[r["id"]]]
        ws.db.save_records(ws.batch_id, changed)
        ws.release = None
    return overview()


@app.post("/api/verify")
def verify():
    """Step 2: Silver-to-PTSL sampling, then Qwen Agent 2 over the selected records."""
    need_idle()
    batch = need_batch()
    with ws.lock:
        s, config, batch_id = ws.settings, ws.config(), ws.batch_id
        w.apply_sampling(batch["records"], s["sampling_enabled"], s["sample_pct"])
        targets = w.ui_verification_targets(batch["records"], ws.profile())
        if not targets:
            raise HTTPException(409, "Nothing to verify. All eligible records are already verified.")
        ws.db.save_records(batch_id, batch["records"])  # sampling decisions
        job = Job("verify", len(targets))
        ws.job, ws.release = job, None
        policy = copy.deepcopy(ws.policy)

    pending, plock = [], threading.Lock()

    def flush(force=False):
        with plock:
            if not pending or not (force or len(pending) >= 20):
                return
            chunk = pending[:]
            pending.clear()
        ws.db.save_records(batch_id, chunk)

    def progress(done, total, rec):
        try:
            job.tick(w.gate_outcome(rec), {"id": rec["id"], "confidence": confidence(rec)})
        except Exception:  # a display problem must never abort the verification run
            job.tick("BLOCK" if rec.get("status") in {"ERROR", "QUARANTINE"} else "REVIEW")
        with plock:
            pending.append(rec)
        try:
            flush()  # Agent 2 results reach SQLite while the run is still going
        except Exception:
            log.exception("could not persist Agent 2 results")

    def work():
        try:
            w.verify_records(targets, config, policy, workers=s["workers"], on_progress=progress, stop=job.stop)
            flush(force=True)
            job.finish()
        except Exception as exc:
            try:
                flush(force=True)
            except Exception:
                log.exception("could not persist Agent 2 results")
            job.finish(str(exc)[:200] or "Verification failed.")

    threading.Thread(target=work, daemon=True).start()
    return job.view()


@app.post("/api/stop")
def stop():
    """Ask the running step (Agent 1 or Quality Gate) to stop. Work already finished is kept."""
    current = ws.job
    if not current or current.state != "running":
        raise HTTPException(409, "Nothing is running.")
    current.stop.set()
    return current.view()


@app.get("/api/job")
def job():
    return ws.job.view() if ws.job else None


@app.get("/api/records")
def records(gate: str = "", status: str = "", q: str = "", offset: int = 0, limit: int = 50):
    batch = need_batch()
    if running():
        raise HTTPException(423, "Processing in progress.")
    q = q.strip().lower()
    rows = [r for r in batch["records"] if (not gate or w.gate_outcome(r) == gate) and (not status or r["status"] == status) and (not q or q in (r.get("original_text") or w.record_text(r) or "").lower() or q in r["id"].lower())]
    limit = max(1, min(200, limit))
    return {"total": len(rows), "items": [row(r) for r in rows[max(0, offset):max(0, offset) + limit]]}


def find(record_id):
    for r in need_batch()["records"]:
        if r["id"] == record_id:
            return r
    raise HTTPException(404, "Record not found.")


@app.get("/api/records/{record_id}")
def record_detail(record_id: str):
    if running():
        raise HTTPException(423, "Processing in progress.")
    return row(find(record_id), detail=True)


def swap_in(record):
    for i, r in enumerate(ws.batch["records"]):
        if r["id"] == record["id"]:
            ws.batch["records"][i] = record
            return


VERDICT = {"APPROVE": "approve", "QUARANTINE": "reject", "UNSURE": "unsure"}


@app.post("/api/records/{record_id}/review")
def review(record_id: str, body: dict = Body(...)):
    need_idle()
    need_batch()
    action = str(body.get("action", "")).upper()
    with ws.lock:
        role, profile = ws.settings["reviewer"], ws.profile()

        def mutate(rec):
            w.human_decision(rec, action, role, profile)
            return action

        try:
            rec, _, _ = ws.db.apply_decision(ws.batch_id, record_id, mutate, verdict=VERDICT.get(action, action.lower()), role=role, channel="dashboard")
        except studio_db.DecisionConflict as exc:
            ws.sync()
            raise HTTPException(409, str(exc)) from None
        except KeyError:
            raise HTTPException(404, "Record not found.") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        swap_in(rec)
        ws.release = None
        return row(rec, detail=True)


@app.post("/api/review/approve-confident")
def approve_confident():
    """One click: approve every PTSL item the verifier is confident about (existing bulk_eligible rules)."""
    need_idle()
    batch, approved, skipped = need_batch(), 0, 0
    with ws.lock:
        profile, role = ws.profile(), ws.settings["reviewer"]

        def mutate(rec):
            if not w.bulk_eligible(rec, profile):
                raise ValueError("not confident enough")
            w.human_decision(rec, "APPROVE", role, profile)
            return "APPROVE"

        for rec in [r for r in batch["records"] if w.bulk_eligible(r, profile)]:
            try:
                fresh, _, _ = ws.db.apply_decision(ws.batch_id, rec["id"], mutate, verdict="approve", role=role, channel="dashboard")
                swap_in(fresh)
                approved += 1
            except (ValueError, studio_db.DecisionConflict, KeyError):
                skipped += 1
        ws.release = None
        ws.sync()
    return {"approved": approved, "skipped": skipped}


@app.post("/api/release")
def release():
    need_idle()
    batch = need_batch()
    with ws.lock:
        rel = StudioRelease(batch, ws.config(), ws.policy, ws.settings)
        ws.release = rel
    threading.Thread(target=rel.build, daemon=True).start()
    return rel.view()


@app.get("/api/release/download")
def download():
    r = ws.release
    if not r or r.status != "ready":
        raise HTTPException(409, (r.error if r else None) or "Release is not ready.")
    return Response(r.archive, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="warisan-gold-{r.version}.zip"'})


@app.get("/api/release/manifest")
def manifest():
    """The full governance manifest (provenance, exclusions, hashes) as a separate download."""
    r = ws.release
    if not r or r.status != "ready":
        raise HTTPException(409, "Release is not ready.")
    return Response(r.files["manifest.json"], media_type="application/json", headers={"Content-Disposition": f'attachment; filename="warisan-gold-{r.version}-manifest.json"'})


@app.post("/api/reset")
def reset():
    need_idle()
    ws.reset()
    return overview()


# ------------------------------------------------------------- static React
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if studio_portal.portal_only() and path.strip("/") != "review":
            return RedirectResponse("/review")  # a portal-only process never serves the admin app
        target = (DIST / path).resolve()
        if path and DIST in target.parents and target.is_file():
            return FileResponse(target)
        return FileResponse(DIST / "index.html")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("STUDIO_HOST", "127.0.0.1"), port=int(os.getenv("STUDIO_PORT", "8503")))
