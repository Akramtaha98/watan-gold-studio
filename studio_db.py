"""SQLite persistence for the Gold Studio web UI (studio_state.db).

Holds everything a restart must not lose: the active batch, Bronze file hashes,
Silver records, Qwen Agent 2 results (inside each record), PTSL human decisions
(an append-only log with UTC timestamps) and the non-secret workspace settings.

* Only masked text is stored (records are masked at ingest); API keys and raw
  uploaded bytes never reach this database.
* Reviews are applied to the stored record inside a BEGIN IMMEDIATE transaction,
  so the dashboard and the PTSL portal (even in two processes) cannot overwrite
  each other. `decisions.seq` is the change counter other processes poll to sync.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bronze (
    batch_id TEXT NOT NULL REFERENCES batches(id), sha256 TEXT NOT NULL, filename TEXT NOT NULL,
    bytes INTEGER NOT NULL, ingested_at TEXT NOT NULL, data TEXT NOT NULL, pos INTEGER NOT NULL,
    PRIMARY KEY (batch_id, sha256));
CREATE TABLE IF NOT EXISTS records (
    batch_id TEXT NOT NULL REFERENCES batches(id), id TEXT NOT NULL, pos INTEGER NOT NULL,
    status TEXT NOT NULL, gate TEXT NOT NULL, confidence REAL, unsure INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (batch_id, id));
CREATE INDEX IF NOT EXISTS records_queue ON records (batch_id, status, unsure, pos);
CREATE TABLE IF NOT EXISTS decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, record_id TEXT NOT NULL,
    verdict TEXT NOT NULL, action TEXT NOT NULL, reviewer_role TEXT NOT NULL, channel TEXT NOT NULL,
    edited INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '',
    prior_status TEXT NOT NULL, new_status TEXT NOT NULL, decided_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS decisions_batch ON decisions (batch_id, seq);
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


class DecisionConflict(ValueError):
    """The record is no longer waiting for review (someone else decided first)."""


class StudioDB:
    def __init__(self, path=None):
        self.path = str(path or os.getenv("STUDIO_DB") or ROOT / "studio_state.db")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=15)
        self.conn.row_factory = sqlite3.Row
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "busy_timeout=15000", "foreign_keys=ON"):
            self.conn.execute(f"PRAGMA {pragma}")
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def close(self):
        with self.lock:
            self.conn.close()

    @contextmanager
    def tx(self):
        """Serialised write transaction (also across processes)."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _row_values(record):
        import watan_gold_studio as w
        result = ((record.get("verification") or {}).get("result")) or {}
        confidence = result.get("confidence")
        return (record["status"], w.gate_outcome(record), float(confidence) if isinstance(confidence, (int, float)) else None, 1 if record.get("secondary_review") else 0)

    def _upsert_record(self, conn, batch_id, record, pos):
        status, gate, confidence, unsure = self._row_values(record)
        conn.execute(
            "INSERT INTO records(batch_id, id, pos, status, gate, confidence, unsure, updated_at, data) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(batch_id, id) DO UPDATE SET status=excluded.status, gate=excluded.gate, confidence=excluded.confidence, "
            "unsure=excluded.unsure, updated_at=excluded.updated_at, data=excluded.data",
            (batch_id, record["id"], pos, status, gate, confidence, unsure, utcnow(), dumps(record)))

    @staticmethod
    def _batch_meta(batch):
        return {k: v for k, v in batch.items() if k not in ("records", "bronze")}

    # ------------------------------------------------------------ settings
    def save_settings(self, settings):
        with self.tx() as c:
            c.execute("INSERT INTO meta(key, value) VALUES ('settings', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (dumps(settings),))

    def load_settings(self):
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
        return json.loads(row["value"]) if row else None

    # -------------------------------------------------------------- batches
    def save_batch(self, batch_id, batch):
        """Persist a freshly ingested batch and make it the active one (older batches stay archived)."""
        stamp = utcnow()
        with self.tx() as c:
            c.execute("UPDATE batches SET active=0 WHERE active=1")
            c.execute("INSERT INTO batches(id, active, created_at, updated_at, data) VALUES (?,1,?,?,?)", (batch_id, stamp, stamp, dumps(self._batch_meta(batch))))
            for pos, entry in enumerate(batch.get("bronze", [])):
                c.execute("INSERT OR IGNORE INTO bronze(batch_id, sha256, filename, bytes, ingested_at, data, pos) VALUES (?,?,?,?,?,?,?)",
                          (batch_id, entry["sha256"], str(entry.get("filename", "")), int(entry.get("bytes", 0)), str(entry.get("ingested_at", stamp)), dumps(entry), pos))
            for pos, record in enumerate(batch["records"]):
                self._upsert_record(c, batch_id, record, pos)

    def save_batch_meta(self, batch_id, batch):
        """Silver/Agent 1 summaries change after ingest; keep them in step."""
        with self.tx() as c:
            c.execute("UPDATE batches SET data=?, updated_at=? WHERE id=?", (dumps(self._batch_meta(batch)), utcnow(), batch_id))

    def save_records(self, batch_id, records):
        """Upsert records (new positions are not needed: batches are fixed at ingest)."""
        if not records:
            return
        with self.tx() as c:
            positions = {r["id"]: r["pos"] for r in c.execute("SELECT id, pos FROM records WHERE batch_id=?", (batch_id,))}
            for record in records:
                if record["id"] in positions:
                    self._upsert_record(c, batch_id, record, positions[record["id"]])

    def deactivate(self):
        with self.tx() as c:
            c.execute("UPDATE batches SET active=0 WHERE active=1")

    def active_batch_id(self):
        with self.lock:
            row = self.conn.execute("SELECT id FROM batches WHERE active=1").fetchone()
        return row["id"] if row else None

    def load_batch(self, batch_id):
        with self.lock:
            head = self.conn.execute("SELECT data FROM batches WHERE id=?", (batch_id,)).fetchone()
            if not head:
                return None
            batch = json.loads(head["data"])
            batch["bronze"] = [json.loads(r["data"]) for r in self.conn.execute("SELECT data FROM bronze WHERE batch_id=? ORDER BY pos", (batch_id,))]
            batch["records"] = [json.loads(r["data"]) for r in self.conn.execute("SELECT data FROM records WHERE batch_id=? ORDER BY pos", (batch_id,))]
        return batch

    def load_records(self, batch_id, ids):
        ids = list(ids)
        out = []
        with self.lock:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                marks = ",".join("?" * len(chunk))
                out += [json.loads(r["data"]) for r in self.conn.execute(f"SELECT data FROM records WHERE batch_id=? AND id IN ({marks})", (batch_id, *chunk))]
        return out

    def load_record(self, batch_id, record_id):
        found = self.load_records(batch_id, [record_id])
        return found[0] if found else None

    # ------------------------------------------------------------ decisions
    def apply_decision(self, batch_id, record_id, mutate, *, verdict, role, channel, edited=False, notes=""):
        """Run `mutate(record)` on the stored record and log the decision, atomically.

        `mutate` raises ValueError to reject (nothing is written). Returns (record, seq, decided_at).
        """
        with self.tx() as c:
            row = c.execute("SELECT pos, data FROM records WHERE batch_id=? AND id=?", (batch_id, record_id)).fetchone()
            if not row:
                raise KeyError(record_id)
            record = json.loads(row["data"])
            if record["status"] != "PTSL_REVIEW":
                raise DecisionConflict("This record is no longer waiting for review.")
            prior = record["status"]
            action = mutate(record)
            self._upsert_record(c, batch_id, record, row["pos"])
            decided_at = utcnow()
            cur = c.execute(
                "INSERT INTO decisions(batch_id, record_id, verdict, action, reviewer_role, channel, edited, notes, prior_status, new_status, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (batch_id, record_id, verdict, action, role, channel, 1 if edited else 0, notes, prior, record["status"], decided_at))
            return record, cur.lastrowid, decided_at

    def max_seq(self):
        with self.lock:
            return self.conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM decisions").fetchone()["m"]

    def decisions_after(self, seq, batch_id):
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM decisions WHERE seq>? AND batch_id=? ORDER BY seq", (seq, batch_id))]

    def decision_summary(self, batch_id):
        with self.lock:
            rows = self.conn.execute("SELECT channel, verdict, COUNT(*) AS n, MAX(decided_at) AS last FROM decisions WHERE batch_id=? GROUP BY channel, verdict", (batch_id,)).fetchall()
        by_channel, by_verdict, last = {}, {}, None
        for r in rows:
            by_channel[r["channel"]] = by_channel.get(r["channel"], 0) + r["n"]
            by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + r["n"]
            last = max(filter(None, [last, r["last"]]), default=None)
        return {"total": sum(by_channel.values()), "by_channel": by_channel, "by_verdict": by_verdict, "last_at": last}

    def recent_decisions(self, batch_id, limit=10, channel=None):
        sql = "SELECT record_id, verdict, reviewer_role, channel, edited, decided_at FROM decisions WHERE batch_id=?" + (" AND channel=?" if channel else "") + " ORDER BY seq DESC LIMIT ?"
        args = (batch_id, channel, limit) if channel else (batch_id, limit)
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args)]

    # ---------------------------------------------------------- review queue
    def gate_counts(self, batch_id, status="PTSL_REVIEW"):
        with self.lock:
            return {r["gate"]: r["n"] for r in self.conn.execute("SELECT gate, COUNT(*) AS n FROM records WHERE batch_id=? AND status=? GROUP BY gate", (batch_id, status))}

    def queue(self, batch_id, offset=0, limit=25):
        """Records waiting for a person; those already marked 'unsure' sink to the end."""
        with self.lock:
            rows = self.conn.execute("SELECT data FROM records WHERE batch_id=? AND status='PTSL_REVIEW' ORDER BY unsure, pos LIMIT ? OFFSET ?", (batch_id, limit, offset)).fetchall()
        return [json.loads(r["data"]) for r in rows]
