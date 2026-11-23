import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon, fmtTime } from "./ui.jsx";

const n = (v) => (v || 0).toLocaleString();
const pct = (a, b) => (b ? Math.min(100, Math.round((a / b) * 100)) : 0);

/** One stacked bar for a folder: green = finished, orange = waiting for a person, red = blocked, grey = not checked yet. */
function Bar({ g, tall }) {
  const total = Math.max(g.records, 1);
  const parts = [
    { v: g.gold, cls: "pass", tip: "ready for Gold" },
    { v: g.ptsl.waiting, cls: "review", tip: "waiting for a reviewer" },
    { v: g.gate.BLOCK, cls: "block", tip: "blocked" },
    { v: g.gate.held, cls: "held", tip: "held back" },
  ];
  const sum = parts.reduce((s, p) => s + p.v, 0);
  const running = g.job?.state === "running";
  return (
    <div className={`seg ${tall ? "tall" : ""} ${running ? "live" : ""}`} title={`${n(g.gold)} ready · ${n(g.ptsl.waiting)} to review · ${n(g.gate.BLOCK)} blocked · ${n(g.gate.waiting)} not checked yet`}>
      {parts.filter((p) => p.v > 0).map((p) => <i key={p.cls} className={p.cls} style={{ width: `${pct(p.v, total)}%` }} />)}
      {total > sum && <i className="wait" style={{ width: `${pct(g.records - sum, total)}%` }} />}
    </div>
  );
}

const STATE_LABEL = { waiting: "Not started", queued: "Queued", running: "Running", review: "Needs review", done: "Done", empty: "Empty" };
const Chip = ({ state }) => <span className={`chip ${state}`}>{STATE_LABEL[state] || state}</span>;

/** The big "what should I do now?" card. */
function NextStep({ nxt, parallel, setParallel, onRunAll, onStopAll, onReview, onRelease, busy }) {
  const stepper = (
    <label className="par" title="How many folders are checked by Qwen at the same time">
      <span>Run</span>
      <button className="step-btn" onClick={() => setParallel(Math.max(1, parallel - 1))} aria-label="Fewer">−</button>
      <b className="num">{parallel}</b>
      <button className="step-btn" onClick={() => setParallel(Math.min(16, parallel + 1))} aria-label="More">+</button>
      <span>folders at a time</span>
    </label>
  );
  const cases = {
    run: { tone: "run", icon: "play", title: `${n(nxt.records)} records in ${n(nxt.folders)} folder${nxt.folders === 1 ? "" : "s"} are waiting for the Quality Gate`, sub: "Qwen checks each folder on its own, so they can run side by side.",
      main: <button className="btn primary big" disabled={busy} onClick={onRunAll}><Icon name="play" size={18} /> Run all folders</button>, extra: stepper },
    running: { tone: "running", icon: "grid", title: `Running… ${n(nxt.folders)} folder${nxt.folders === 1 ? "" : "s"} in progress`, sub: `${n(nxt.records)} records left${nxt.eta ? ` · about ${fmtTime(nxt.eta)} to go` : ""}. You can leave this page; it keeps going.`,
      main: <button className="btn danger big" onClick={onStopAll}><Icon name="stop" size={18} /> Stop all</button> },
    review: { tone: "review", icon: "check", title: `${n(nxt.records)} records need a human decision in ${n(nxt.folders)} folder${nxt.folders === 1 ? "" : "s"}`, sub: `Start with ${nxt.folder_label}, it has the most waiting. Approve, reject or skip each record.`,
      main: <button className="btn primary big" onClick={() => onReview(nxt.folder)}>Start reviewing <Icon name="arrow" size={18} /></button> },
    release: { tone: "done", icon: "check", title: `All done. ${n(nxt.records)} records are ready for Gold`, sub: "Every folder has been checked and reviewed.",
      main: <button className="btn primary big" onClick={onRelease}>Go to release <Icon name="arrow" size={18} /></button> },
    idle: { tone: "idle", icon: "grid", title: "Nothing to do right now", sub: "Upload more data, or look at a folder below.", main: null },
  };
  const c = cases[nxt.action] || cases.idle;
  return (
    <div className={`next ${c.tone}`}>
      <div className="next-icon"><Icon name={c.icon} size={26} /></div>
      <div className="next-text">
        <small>NEXT STEP</small>
        <h2>{c.title}</h2>
        <p>{c.sub}</p>
      </div>
      <div className="next-actions">
        {c.main}
        {c.extra}
      </div>
    </div>
  );
}

/** Four numbers that say where the whole batch stands. */
function Summary({ t }) {
  const items = [
    { label: "Uploaded", value: n(t.records), sub: `${n(t.files)} files`, cls: "" },
    { label: "Checked by Qwen", value: `${pct(t.gate.checked, t.records)}%`, sub: `${n(t.gate.waiting)} not checked yet`, cls: "" },
    { label: "Waiting for a person", value: n(t.ptsl.waiting), sub: `${n(t.ptsl.done)} already decided`, cls: t.ptsl.waiting ? "review" : "" },
    { label: "Ready for Gold", value: n(t.gold), sub: `${t.percent}% of everything finished`, cls: t.gold ? "pass" : "" },
  ];
  return (
    <div className="sum">
      {items.map((i) => (
        <div key={i.label} className={`sum-item ${i.cls}`}>
          <b className="num">{i.value}</b>
          <span>{i.label}</span>
          <small>{i.sub}</small>
        </div>
      ))}
    </div>
  );
}

/** Files inside an opened folder. */
function Files({ folder, tick }) {
  const [rows, setRows] = useState(null);
  useEffect(() => {
    let live = true;
    api.pipeline({ by: "file", folder, sort: "name", limit: 200 }).then((d) => live && setRows(d.groups)).catch(() => {});
    return () => { live = false; };
  }, [folder, tick]);
  if (!rows) return <div className="files"><span className="mono">Loading files…</span></div>;
  return (
    <div className="files">
      {rows.map((f) => (
        <div className="file" key={f.key}>
          <span className="fname">{f.label}</span>
          <span className="mono num">{n(f.records)} records</span>
          <Bar g={f} />
          <span className="num fmeta">{f.percent}%</span>
          <Chip state={f.state} />
        </div>
      ))}
    </div>
  );
}

const FILTERS = [["", "All"], ["review", "Needs review"], ["running", "Running"], ["waiting", "Not started"], ["done", "Done"]];

export function PipelineView({ st, refresh, notify, onReview, goRelease }) {
  const [q, setQ] = useState("");
  const [state, setState] = useState("");
  const [sort, setSort] = useState("remaining");
  const [page, setPage] = useState(0);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [parallel, setParallel] = useState(() => Number(localStorage.getItem("wgs_parallel")) || 3);
  const [busyKey, setBusyKey] = useState("");
  const [open, setOpen] = useState({});
  const [picked, setPicked] = useState({});
  const [tick, setTick] = useState(0);
  const limit = 50;

  const load = useCallback(async () => {
    try {
      setData(await api.pipeline({ by: "folder", q, state, sort, offset: page * limit, limit }));
      setErr("");
      setTick((t) => t + 1);
    } catch (e) {
      setErr(e.message);
    }
  }, [q, state, sort, page]);

  const running = st.job?.state === "running";
  useEffect(() => {
    load();
    const t = setInterval(() => !document.hidden && load(), running ? 2000 : 5000);
    return () => clearInterval(t);
  }, [load, running]);
  useEffect(() => setPage(0), [q, state, sort]);
  useEffect(() => { try { localStorage.setItem("wgs_parallel", String(parallel)); } catch {} }, [parallel]);

  const run = async (groups) => {
    setBusyKey(groups ? groups[0] : "*");
    try {
      const r = await api.verifyGroups(groups, parallel);
      notify?.(`Started ${r.started.length} folder${r.started.length === 1 ? "" : "s"}, ${r.parallel} at a time.`);
      setPicked({});
      await load();
      refresh?.();
    } catch (e) {
      notify?.(e.message);
    } finally {
      setBusyKey("");
    }
  };
  const halt = async (groups) => {
    try {
      await api.stop(groups);
      await load();
      refresh?.();
    } catch (e) {
      notify?.(e.message);
    }
  };

  if (!data) return <div className="card"><p className="sub">{err || "Loading pipeline…"}</p></div>;
  const t = data.total;
  const pages = Math.max(1, Math.ceil(data.count / limit));
  const chosen = Object.keys(picked).filter((k) => picked[k]);
  const allShown = data.groups.length > 0 && data.groups.every((g) => picked[g.key]);

  const action = (g) => {
    const on = g.job && ["running", "queued"].includes(g.job.state);
    if (on) return <button className="btn danger sm" onClick={() => halt([g.key])} disabled={g.job.stopping}><Icon name="stop" size={13} /> {g.job.state === "queued" ? "Cancel" : g.job.stopping ? "Stopping…" : "Stop"}</button>;
    if (g.remaining.quality_gate > 0) return <button className="btn primary sm" disabled={busyKey === g.key} onClick={() => run([g.key])}><Icon name="play" size={13} /> Run</button>;
    if (g.ptsl.waiting > 0) return <button className="btn sm review" onClick={() => onReview?.(g.key)}>Review {n(g.ptsl.waiting)}</button>;
    return <span className="ok"><Icon name="check" size={15} stroke={3} /> Done</span>;
  };

  return (
    <div className="pipe">
      <div className="card">
        <NextStep nxt={data.next} parallel={parallel} setParallel={setParallel} busy={busyKey === "*"} onRunAll={() => run(null)} onStopAll={() => halt(null)} onReview={onReview} onRelease={goRelease} />
        <Summary t={t} />
      </div>

      <div className="card">
        <div className="row spread" style={{ marginBottom: 12 }}>
          <div>
            <h2 style={{ margin: 0 }}>Folders</h2>
            <p className="sub" style={{ margin: 0 }}>{n(data.folders)} folder{data.folders === 1 ? "" : "s"} · {n(data.files)} files. Each folder is its own group and runs on its own.</p>
          </div>
          <div className="row">
            {chosen.length > 0 && <button className="btn primary" onClick={() => run(chosen)}><Icon name="play" size={15} /> Run {chosen.length} selected</button>}
            <input className="input" placeholder="Search folders…" value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="input" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
              <option value="remaining">Most work left</option>
              <option value="percent">Least finished</option>
              <option value="records">Biggest</option>
              <option value="name">Name</option>
            </select>
          </div>
        </div>
        <div className="filters">
          {FILTERS.map(([v, label]) => (
            <button key={v} className={`fchip ${state === v ? "on" : ""}`} onClick={() => setState(v)}>
              {label}{v && data.states[v] ? <em>{n(data.states[v])}</em> : null}
            </button>
          ))}
        </div>

        <div className="flist">
          <div className="frow head">
            <input type="checkbox" checked={allShown} onChange={(e) => setPicked(Object.fromEntries(data.groups.map((g) => [g.key, e.target.checked])))} aria-label="Select all shown" />
            <span>Folder</span>
            <span>Progress</span>
            <span>State</span>
            <span>Action</span>
          </div>
          {data.groups.map((g) => {
            const isOpen = !!open[g.key];
            const live = g.job?.state === "running" ? g.job : null;
            return (
              <div key={g.key} className={`fgroup ${g.state}`}>
                <div className="frow">
                  <input type="checkbox" checked={!!picked[g.key]} onChange={(e) => setPicked({ ...picked, [g.key]: e.target.checked })} aria-label={`Select ${g.label}`} />
                  <button className="fname-btn" onClick={() => setOpen({ ...open, [g.key]: !isOpen })} aria-expanded={isOpen}>
                    <i className={`chev ${isOpen ? "open" : ""}`} />
                    <span>
                      <b>{g.label}</b>
                      <small>{n(g.files)} file{g.files === 1 ? "" : "s"} · {n(g.records)} records</small>
                    </span>
                  </button>
                  <div className="fprog">
                    <Bar g={g} tall />
                    <div className="fnums">
                      <span><b className="num">{g.percent}%</b> finished</span>
                      {live ? <span className="live">checking {n(live.done)}/{n(live.total)} · {fmtTime(live.eta)} left</span> : (
                        <>
                          {g.remaining.quality_gate > 0 && <span>{n(g.remaining.quality_gate)} to check</span>}
                          {g.ptsl.waiting > 0 && <span className="rv">{n(g.ptsl.waiting)} to review</span>}
                          {g.gate.BLOCK > 0 && <span className="bk">{n(g.gate.BLOCK)} blocked</span>}
                          {g.gold > 0 && <span className="gd">{n(g.gold)} ready</span>}
                        </>
                      )}
                    </div>
                  </div>
                  <Chip state={g.state} />
                  <div className="fact">{action(g)}</div>
                </div>
                {isOpen && <Files folder={g.key} tick={tick} />}
              </div>
            );
          })}
          {!data.groups.length && <div className="empty-row">Nothing matches this filter.</div>}
        </div>

        {pages > 1 && (
          <div className="row spread" style={{ marginTop: 12 }}>
            <span className="mono">{n(data.count)} folders · page {page + 1} of {pages}</span>
            <div className="row">
              <button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
              <button className="btn" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </div>
        )}
        <div className="legend">
          <span><i className="pass" /> ready for Gold</span><span><i className="review" /> waiting for a reviewer</span><span><i className="block" /> blocked</span><span><i className="held" /> held back</span><span><i className="wait" /> not checked yet</span>
        </div>
      </div>
    </div>
  );
}
