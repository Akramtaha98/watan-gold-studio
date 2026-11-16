"""Isolated PTSL reviewer portal API (served under /api/ptsl, UI at /review).

Designed for external human evaluators, so it is deliberately narrow:

* its own credential (PTSL_REVIEW_TOKEN); that token is never accepted by the admin API;
* it reads and writes the shared SQLite database only, never the admin workspace objects;
* responses carry masked text, tags and flag reasons only - no Bronze files or hashes,
  no Qwen server settings, no Silver internals, no release or export data;
* every verdict is applied and logged to SQLite in one transaction, with a UTC timestamp,
  so it survives restarts and shows up on the main dashboard immediately.

Run it beside the dashboard (same process) or on its own with STUDIO_PORTAL_ONLY=1,
which makes the process answer /review and /api/ptsl/* and nothing else.
"""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

import studio_db
import watan_gold_studio as w

ROLES = ("Linguist", "Domain_SME", "Project_Lead")
# portal verdict -> (core human_decision action, whether the reviewer supplies edited text)
VERDICTS = {"approve": ("APPROVE", False), "edit_approve": ("APPROVE", True), "reject": ("QUARANTINE", False), "unsure": ("UNSURE", False)}
MAX_NOTES, MAX_EDIT, PAGE = 1000, 20000, 10


# -------------------------------------------------------------------- auth
def portal_only():
    return os.getenv("STUDIO_PORTAL_ONLY", "") == "1"


def accepted_tokens():
    """Reviewer token first; the admin token also opens the portal (for the project team) unless portal-only."""
    review, admin = os.getenv("PTSL_REVIEW_TOKEN", ""), os.getenv("STUDIO_ACCESS_TOKEN", "")
    tokens = [review] if review else []
    if admin and not portal_only():
        tokens.append(admin)
    return tokens


def given_token(request):
    return request.headers.get("x-review-token", "") or request.headers.get("x-access-token", "")


def token_ok(request):
    tokens = accepted_tokens()
    return not tokens or any(secrets.compare_digest(given_token(request).encode(), t.encode()) for t in tokens)


def portal_guard(request: Request, production: bool):
    """Middleware hook for /api/ptsl/*: returns an error response, or None to continue."""
    need = 32 if production else (1 if portal_only() else 0)
    if len(os.getenv("PTSL_REVIEW_TOKEN", "")) < need:
        return JSONResponse({"detail": "The reviewer portal requires PTSL_REVIEW_TOKEN" + (" with at least 32 characters." if production else ".")}, status_code=503)
    if request.url.path != "/api/ptsl/auth" and not token_ok(request):
        return JSONResponse({"detail": "Reviewer access token required."}, status_code=401)
    return None


# ------------------------------------------------------------ presentation
def safe(text):
    """Defence in depth: ingest already masks PII; re-mask anything that would still trip the detector."""
    text = text or ""
    return w.mask_pii(text)[0] if w.pii_leaks(text) else text


def safe_turns(turns):
    return [{"role": t["role"], "content": safe(t["content"])} for t in turns]


def flag_labels(rec, result):
    out = []
    for key, label in (("unsafe", "Possibly unsafe"), ("corrupted", "Text may be corrupted"), ("damaged_chars", "Damaged characters"), ("explicit_content", "Explicit content"),
                       ("lang_near_malay", "Indonesian / near-Malay wording"), ("dialect_flag", "Dialect or regional variant")):
        if result.get(key):
            out.append(label)
    if result.get("script") == "jawi":
        out.append("Jawi script")
    if result.get("three_r"):
        out.append("Sensitive 3R topic: " + ", ".join(str(x) for x in result["three_r"][:3]))
    for key, label in (("dbp_errors", "DBP spelling or grammar"), ("policy_errors", "Project policy"), ("factual_errors", "Possible factual error")):
        if result.get(key):
            out.append(label)
    pretty = {"near_duplicate": "Near-duplicate of another record", "agent1_rejected": "Messy-document fix was rejected", "agent1_error": "Messy-document fix failed",
              "seed_hygiene": "Seed hygiene check", "variant": "Spelling or script variant"}
    out += [pretty.get(r, r.replace("_", " ")) for r in w.gate_reasons(rec)]
    if "mutable_project_facts" in rec.get("seed_hygiene_flags", []):
        out.append("Project facts: Project Lead approval required")
    elif result.get("dbp_errors"):
        out.append("DBP corrections: Linguist or Project Lead approval required")
    return list(dict.fromkeys(out))


def present(rec):
    """One record as a reviewer may see it: masked text, Qwen's suggestion, flags and tags."""
    result = (rec.get("verification") or {}).get("result") or {}
    dialogue = rec.get("kind", "dialogue") == "dialogue"
    original = safe_turns(rec["original"]) if dialogue else safe(rec.get("original_text"))
    suggested = safe_turns(rec["candidate"]) if dialogue else safe(rec.get("text"))
    issues = [safe(str(x))[:200] for key in ("dbp_errors", "policy_errors", "factual_errors") for x in (result.get(key) or [])][:12]
    conf = result.get("confidence")
    return {"id": rec["id"], "kind": "dialogue" if dialogue else "prose", "gate": w.gate_outcome(rec), "category": rec.get("category"), "language": result.get("language"),
            "stage": rec.get("stage"), "confidence": round(conf, 3) if isinstance(conf, (int, float)) else None, "flags": flag_labels(rec, result), "issues": issues,
            "reason": safe(result.get("reason") or rec.get("reason") or "")[:600], "original": original, "suggested": suggested, "changed": original != suggested,
            "unsure": bool(rec.get("secondary_review"))}


def parse_edit(rec, edited):
    """Validate a reviewer's edit and return it in the shape human_decision expects."""
    if rec.get("kind", "dialogue") == "dialogue":
        if not isinstance(edited, list) or len(edited) != len(rec["candidate"]):
            raise ValueError("The edited conversation must keep every turn.")
        out = []
        for old, new in zip(rec["candidate"], edited):
            text = new.get("content") if isinstance(new, dict) else None
            if not isinstance(new, dict) or new.get("role") != old["role"] or not isinstance(text, str) or not text.strip() or len(text) > MAX_EDIT:
                raise ValueError("Each turn must keep its role and have non-empty text.")
            out.append({"role": old["role"], "content": text})
        return out
    if not isinstance(edited, str) or not edited.strip() or len(edited) > MAX_EDIT:
        raise ValueError("The edited text must not be empty.")
    return edited


def clean_notes(value):
    notes = str(value or "").strip()
    if len(notes) > MAX_NOTES:
        raise ValueError(f"Notes are limited to {MAX_NOTES} characters.")
    if w.pii_leaks(notes):
        raise ValueError("Notes must not contain an email address, IC number or phone number.")
    return notes


# ------------------------------------------------------------------ router
def create_router(get_db, get_profile, sync):
    router = APIRouter(prefix="/api/ptsl")

    def stats(db, batch_id):
        gates, done = db.gate_counts(batch_id), db.decision_summary(batch_id)
        return {"waiting": sum(gates.values()), "warn": gates.get("WARN", 0), "review": gates.get("REVIEW", 0), "decided": done["total"],
                "by_verdict": done["by_verdict"], "last_at": done["last_at"]}

    @router.get("/auth")
    def auth(request: Request):
        return {"required": bool(accepted_tokens()), "ok": token_ok(request), "roles": list(ROLES)}

    @router.get("/stats")
    def get_stats():
        db = get_db()
        batch_id = db.active_batch_id()
        if not batch_id:
            return {"active": False, "waiting": 0, "warn": 0, "review": 0, "decided": 0, "by_verdict": {}, "last_at": None}
        return {"active": True, **stats(db, batch_id)}

    @router.get("/queue")
    def queue(offset: int = 0, limit: int = PAGE):
        db = get_db()
        batch_id = db.active_batch_id()
        if not batch_id:
            return {"active": False, "roles": list(ROLES), "waiting": 0, "warn": 0, "review": 0, "decided": 0, "by_verdict": {}, "last_at": None, "items": [], "recent": []}
        items = [present(r) for r in db.queue(batch_id, max(0, offset), max(1, min(50, limit)))]
        return {"active": True, "roles": list(ROLES), **stats(db, batch_id), "items": items, "recent": db.recent_decisions(batch_id, 8, channel="ptsl_portal")}

    @router.post("/records/{record_id}/decision")
    def decide(record_id: str, body: dict = Body(...)):
        db = get_db()
        batch_id = db.active_batch_id()
        if not batch_id:
            raise HTTPException(409, "No batch is open for review.")
        role = body.get("role")
        verdict = str(body.get("verdict", "")).lower()
        if role not in ROLES:
            raise HTTPException(422, "Choose your reviewer role first.")
        if verdict not in VERDICTS:
            raise HTTPException(422, "Choose Approve, Edit & Approve, Reject / Block or Unsure.")
        action, edit = VERDICTS[verdict]
        try:
            notes = clean_notes(body.get("notes"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        profile = get_profile()

        def mutate(rec):
            candidate = parse_edit(rec, body.get("edited")) if edit else None
            w.human_decision(rec, action, role, profile, candidate=candidate)
            return action

        try:
            rec, seq, decided_at = db.apply_decision(batch_id, record_id, mutate, verdict=verdict, role=role, channel="ptsl_portal", edited=edit, notes=notes)
        except studio_db.DecisionConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except KeyError:
            raise HTTPException(404, "Record not found.") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        sync()  # in-process dashboard sees the verdict at once; other processes poll decisions.seq
        return {"ok": True, "record_id": record_id, "verdict": verdict, "status": rec["status"], "decided_at": decided_at, "seq": seq, **stats(db, batch_id)}

    return router
