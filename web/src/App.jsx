import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, setToken } from "./api.js";
import { Pipeline } from "./shell.jsx";
import { Icon } from "./ui.jsx";

function Login({ onDone }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    setToken(value);
    const r = await api.auth().catch(() => ({ ok: false }));
    r.ok ? onDone() : setError("That token is not correct.");
  };
  return (
    <form className="card login" onSubmit={submit}>
      <div className="logo" style={{ marginBottom: 14 }}><Icon name="logo" size={22} /></div>
      <h2>WaRiSAN Gold Studio</h2>
      <p className="sub">Enter the workspace access token to continue.</p>
      <input className="input" type="password" autoFocus placeholder="Access token" value={value} onChange={(e) => setValue(e.target.value)} style={{ width: "100%", marginBottom: 12 }} />
      {error && <div className="err" style={{ marginBottom: 12 }}>{error}</div>}
      <button className="btn primary big" style={{ width: "100%", justifyContent: "center" }}>Open workspace</button>
    </form>
  );
}

export default function App() {
  const [st, setSt] = useState(null);
  const [health, setHealth] = useState(null);
  const [view, setView] = useState(1);
  const [toast, setToast] = useState("");
  const [locked, setLocked] = useState(false);
  const prev = useRef(null);

  const notify = useCallback((m) => {
    setToast(m);
    setTimeout(() => setToast(""), 4000);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await api.state();
      setSt(next);
      return next;
    } catch (e) {
      if (e.status === 401) setLocked(true);
    }
  }, []);

  useEffect(() => {
    api.auth().then((a) => (a.ok ? refresh() : setLocked(true))).catch(() => notify("Cannot reach the Studio server."));
  }, [refresh, notify, locked]);

  useEffect(() => {
    if (locked) return;
    const beat = () => api.health().then(setHealth).catch(() => setHealth({ ok: false }));
    beat();
    const t = setInterval(beat, 15000);
    return () => clearInterval(t);
  }, [locked]);

  // Poll quickly while something is processing; stay quiet otherwise.
  const busy = st?.job?.state === "running" || (st?.release && ["pending", "building"].includes(st.release.status));
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(refresh, 700);
    return () => clearInterval(t);
  }, [busy, refresh]);

  // Idle sync: reviewer verdicts submitted in /review land in the shared database; pick them up here.
  useEffect(() => {
    if (locked || busy) return;
    const t = setInterval(() => !document.hidden && refresh(), 4000);
    return () => clearInterval(t);
  }, [locked, busy, refresh]);

  // Follow the pipeline: jump to the step that just finished its work.
  useEffect(() => {
    if (!st) return;
    const p = prev.current;
    if (p?.job?.state === "running" && st.job?.state !== "running" && st.job?.kind === "verify") notify(st.job.error || (st.job.state === "stopped" ? "Quality Gate stopped. Finished records were kept." : "Quality Gate finished."));
    if (!p && st.has_batch) setView(5);
    if (p && !p.has_batch && st.has_batch) setView(5);
    if (p?.has_batch && !st.has_batch) setView(1);
    prev.current = st;
  }, [st, notify]);

  if (locked) return <div className="app"><Login onDone={() => { setLocked(false); refresh(); }} /></div>;
  if (!st) return <div className="app"><div className="empty">Loading…</div></div>;

  const start = async () => {
    try {
      setSt(await api.reset());
      setView(1);
    } catch (e) {
      notify(e.message);
    }
  };

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <div className="logo"><Icon name="logo" size={22} /></div>
          <div>
            <h1>WaRiSAN Gold Studio</h1>
            <p>From raw files to release-ready Malay data</p>
          </div>
        </div>
        <div className="top-actions">
          <span className="pill" title={health?.available?.join(", ")}>
            <i className={`dot ${health?.ok ? "on" : health ? "off" : ""}`} />
            {health ? (health.ok ? `${health.model} connected` : "Qwen offline") : "Checking Qwen…"}
          </span>
          {st.has_batch && <button className="btn ghost" disabled={st.job?.state === "running"} onClick={start}><Icon name="reset" size={16} /> Start over</button>}
        </div>
      </header>
      <Pipeline st={st} view={view} setView={setView} refresh={refresh} notify={notify} />
      {toast && <div className="toast" role="alert">{toast}</div>}
    </div>
  );
}
