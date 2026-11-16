import React, { useCallback, useEffect, useRef, useState } from "react";
import { portal, getReviewToken, setReviewToken } from "./portalApi.js";

const ROLE_KEY = "warisan_review_role";
const VERDICT_LABEL = { approve: "Approved", edit_approve: "Edited & approved", reject: "Rejected / blocked", unsure: "Unsure" };
const when = (iso) => (iso ? new Date(iso).toISOString().replace("T", " ").slice(0, 19) + " UTC" : "—");

function Turns({ turns, editable, draft, onChange }) {
  return (
    <div className="turns">
      {turns.map((t, i) => (
        <div key={i} className={"turn " + t.role}>
          <div className="turn-role">{t.role}</div>
          {editable && t.role === "assistant" ? (
            <textarea className="input turn-edit" value={draft[i]} onChange={(e) => onChange(i, e.target.value)} rows={Math.min(10, Math.max(3, Math.ceil(draft[i].length / 70)))} />
          ) : (
            <div className="turn-text">{t.content}</div>
          )}
        </div>
      ))}
    </div>
  );
}

function Login({ onDone }) {
  const [value, setValue] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    setReviewToken(value.trim());
    try {
      const r = await portal.auth();
      if (r.ok) onDone(r);
      else setErr("That access token was not accepted.");
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return (
    <div className="app portal">
      <form className="card login" onSubmit={submit}>
        <h2>PTSL reviewer access</h2>
        <p className="muted">Enter the access token you were given for the review portal.</p>
        <input className="input" type="password" autoFocus value={value} onChange={(e) => setValue(e.target.value)} placeholder="Reviewer access token" />
        {err && <div className="err">{err}</div>}
        <button className="btn primary" type="submit" disabled={!value.trim()}>Open portal</button>
      </form>
    </div>
  );
}

export default function Portal() {
  const [auth, setAuth] = useState(null);
  const [data, setData] = useState(null);
  const [role, setRole] = useState(() => sessionStorage.getItem(ROLE_KEY) || "");
  const [notes, setNotes] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [flash, setFlash] = useState("");
  const hold = useRef(false);
  hold.current = editing || busy || notes.length > 0;

  const refresh = useCallback(async () => {
    try {
      setData(await portal.queue(0, 10));
    } catch (ex) {
      if (ex.status === 401) setAuth({ required: true, ok: false });
      else setErr(ex.message);
    }
  }, []);

  useEffect(() => {
    portal.auth().then(setAuth).catch((ex) => setErr(ex.message));
  }, []);

  useEffect(() => {
    if (!auth || (auth.required && !auth.ok)) return undefined;
    refresh();
    const timer = setInterval(() => !hold.current && refresh(), 5000);
    return () => clearInterval(timer);
  }, [auth, refresh]);

  const item = data && data.items && data.items[0];
  const itemId = item && item.id;
  useEffect(() => {
    setEditing(false);
    setNotes("");
    setErr("");
    if (item) setDraft(item.kind === "dialogue" ? item.suggested.map((t) => t.content) : [item.suggested]);
  }, [itemId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!auth) return <div className="app portal"><div className="empty">{err || "Loading…"}</div></div>;
  if (auth.required && !auth.ok) return <Login onDone={setAuth} />;

  const pickRole = (r) => {
    setRole(r);
    sessionStorage.setItem(ROLE_KEY, r);
  };

  const submit = async (verdict) => {
    if (!role) return setErr("Choose your reviewer role first.");
    setBusy(true);
    setErr("");
    try {
      const body = { role, verdict, notes };
      if (verdict === "edit_approve") body.edited = item.kind === "dialogue" ? item.suggested.map((t, i) => ({ role: t.role, content: draft[i] })) : draft[0];
      await portal.decide(item.id, body);
      setFlash(VERDICT_LABEL[verdict] + " — saved " + when(new Date().toISOString()));
      setNotes("");
      setEditing(false);
      hold.current = false;
      await refresh();
    } catch (ex) {
      setErr(ex.message);
      if (ex.status === 409) refresh();
    } finally {
      setBusy(false);
    }
  };

  const roles = (data && data.roles) || auth.roles || ["Linguist", "Domain_SME", "Project_Lead"];
  const tally = data || { waiting: 0, warn: 0, review: 0, decided: 0 };

  return (
    <div className="app portal">
      <header className="portal-head">
        <div>
          <h1>PTSL review portal</h1>
          <p className="muted">Masked text only. Your verdicts are saved immediately.</p>
        </div>
        <label className="field">
          <span>Your role</span>
          <select className="input" value={role} onChange={(e) => pickRole(e.target.value)}>
            <option value="">Select role…</option>
            {roles.map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}
          </select>
        </label>
      </header>

      <div className="tally-row">
        <div className="stat"><b>{tally.waiting}</b><span>Waiting</span></div>
        <div className="stat"><b>{tally.warn}</b><span>WARN</span></div>
        <div className="stat"><b>{tally.review}</b><span>REVIEW</span></div>
        <div className="stat"><b>{tally.decided}</b><span>Decided</span></div>
      </div>

      {flash && <div className="note">{flash}</div>}
      {err && <div className="err">{err}</div>}

      {!data ? (
        <div className="empty">Loading…</div>
      ) : !data.active ? (
        <div className="card empty">No batch is open for review yet.</div>
      ) : !item ? (
        <div className="card empty">All caught up — no records are waiting for review.</div>
      ) : (
        <div className="card review-card">
          <div className="row">
            <span className={"chip gate-" + item.gate}>{item.gate}</span>
            {item.category && <span className="tag">{item.category}</span>}
            {item.language && <span className="tag">{item.language}</span>}
            {item.stage && <span className="tag">{item.stage}</span>}
            {item.unsure && <span className="tag">Marked unsure before</span>}
            <span className="muted right">Record {item.id}{item.confidence != null ? ` · Qwen confidence ${item.confidence}` : ""}</span>
          </div>

          {item.flags.length > 0 && (
            <ul className="flags">{item.flags.map((f) => <li key={f}>{f}</li>)}</ul>
          )}
          {item.reason && <p className="reason">{item.reason}</p>}
          {item.issues.length > 0 && <ul className="issues">{item.issues.map((x, i) => <li key={i}>{x}</li>)}</ul>}

          <div className="cmp">
            <div className="box">
              <h3>Original (masked)</h3>
              {item.kind === "dialogue" ? <Turns turns={item.original} /> : <div className="turn-text">{item.original}</div>}
            </div>
            <div className="box">
              <h3>{item.changed ? "Qwen's suggested edit" : "Qwen suggests no change"}</h3>
              {item.kind === "dialogue" ? (
                <Turns turns={item.suggested} editable={editing} draft={draft} onChange={(i, v) => setDraft((d) => d.map((x, j) => (j === i ? v : x)))} />
              ) : editing ? (
                <textarea className="input" rows={10} value={draft[0] || ""} onChange={(e) => setDraft([e.target.value])} />
              ) : (
                <div className="turn-text">{item.suggested}</div>
              )}
              {editing && item.kind === "dialogue" && <p className="muted">User turns are read-only; edit the assistant replies only.</p>}
            </div>
          </div>

          <label className="field">
            <span>Notes (optional, no personal data)</span>
            <textarea className="input" rows={2} maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>

          <div className="actions">
            {editing ? (
              <>
                <button className="btn primary" disabled={busy || !role} onClick={() => submit("edit_approve")}>Save edit &amp; approve</button>
                <button className="btn" disabled={busy} onClick={() => { setEditing(false); setDraft(item.kind === "dialogue" ? item.suggested.map((t) => t.content) : [item.suggested]); }}>Cancel edit</button>
              </>
            ) : (
              <>
                <button className="btn primary" disabled={busy || !role} onClick={() => submit("approve")}>Approve</button>
                <button className="btn" disabled={busy || !role} onClick={() => setEditing(true)}>Edit &amp; Approve</button>
                <button className="btn danger" disabled={busy || !role} onClick={() => submit("reject")}>Reject / Block</button>
                <button className="btn" disabled={busy || !role} onClick={() => submit("unsure")}>Unsure</button>
              </>
            )}
          </div>
          {!role && <p className="muted">Select your role above to enable the buttons.</p>}
        </div>
      )}

      {data && data.recent && data.recent.length > 0 && (
        <div className="card">
          <h3>Recent decisions</h3>
          <ul className="recent">
            {data.recent.map((d, i) => (
              <li key={d.seq || i}>
                <b>{VERDICT_LABEL[d.verdict] || d.verdict}</b> · record {d.record_id} · {(d.reviewer_role || "").replace("_", " ")} · {when(d.decided_at)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
