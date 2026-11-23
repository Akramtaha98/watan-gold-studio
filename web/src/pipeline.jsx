import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon, fmtTime } from "./ui.jsx";

const n = (v) => (v || 0).toLocaleString();
const pct = (a, b) => (b ? Math.min(100, Math.round((a / b) * 100)) : 0);

/** Segmented bar: each segment is {v, cls}; grey remainder = work still waiting. */
function Seg({ parts, total, title }) {
  const sum = parts.reduce((s, p) => s + p.v, 0);
  return (
    <div className="seg" title={title}>
      {parts.filter((p) => p.v > 0).map((p, i) => <i key={i} className={p.cls} style={{ width: `${pct(p.v, total)}%` }} />)}
      {total > sum && <i className="wait" style={{ width: `${pct(total - sum, total)}%` }} />}
    </div>
  );
}

function Stage({ name, state, main, sub, bar }) {
  return (
    <div className={`stage ${state}`}>
      <small>{name}</small>
      <b className="num">{main}</b>
      <span>{sub}</span>
      {bar}
    </div>
  );
}

/** The DAG strip: Bronze -> Silver -> Quality Gate -> PTSL review -> Gold, using whole-batch totals. */
function Flow({ t }) {
  const g = t.gate;
  const gateTotal = t.records;
  const gateState = g.waiting ? (g.checked ? "running" : "waiting") : "done";
  const ptslState = !t.ptsl.needed && g.waiting ? "waiting" : t.ptsl.waiting ? "review" : g.waiting ? "waiting" : "done";
  const a1 = t.agent1;
  const a1Done = a1.applied + a1.rejected + a1.error;
  return (
    <div className="flow">
      <Stage name="Bronze · raw files" state="done" main={`${n(t.files)} files`} sub={`${n(t.records)} records ingested`} bar={<Seg parts={[{ v: 1, cls: "ok" }]} total={1} />} />
      <i className="arrow" />
      <Stage name="Silver · cleaned" state={a1.waiting ? "running" : "done"} main={`${n(t.messy)} messy`} sub={`Agent 1: ${n(a1Done)} done${a1.waiting ? ` · ${n(a1.waiting)} waiting` : ""}`}
        bar={<Seg parts={[{ v: a1.applied, cls: "ok" }, { v: a1.rejected + a1.error, cls: "bad" }]} total={Math.max(t.messy, 1)} />} />
      <i className="arrow" />
      <Stage name="Quality Gate · Qwen" state={gateState} main={`${n(g.checked)} / ${n(gateTotal)}`} sub={`${n(g.waiting)} waiting${g.held ? ` · ${n(g.held)} held` : ""}`}
        bar={<Seg parts={[{ v: g.PASS, cls: "pass" }, { v: g.WARN, cls: "warn" }, { v: g.REVIEW, cls: "review" }, { v: g.BLOCK, cls: "block" }, { v: g.held, cls: "held" }]} total={gateTotal} title={`PASS ${g.PASS} · WARN ${g.WARN} · REVIEW ${g.REVIEW} · BLOCK ${g.BLOCK}`} />} />
      <i className="arrow" />
      <Stage name="PTSL · human review" state={ptslState} main={`${n(t.ptsl.done)} / ${n(t.ptsl.needed)}`} sub={`${n(t.ptsl.waiting)} waiting for a reviewer`}
        bar={<Seg parts={[{ v: t.ptsl.done, cls: "ok" }]} total={Math.max(t.ptsl.needed, 1)} />} />
      <i className="arrow" />
      <Stage name="Gold · ready" state={t.gold && !g.waiting && !t.ptsl.waiting ? "done" : t.gold ? "running" : "waiting"} main={n(t.gold)} sub="records ready to release" bar={<Seg parts={[{ v: t.gold, cls: "pass" }]} total={Math.max(t.records, 1)} />} />
    </div>
  );
}

const STATE_LABEL = { waiting: "Waiting", queued: "Queued", running: "Running", review: "Needs review", done: "Done", empty: "Empty" };
const Chip = ({ state }) => <span className={`chip ${state}`}>{STATE_LABEL[state] || state}</span>;

function Cells({ g, actions }) {
  const gt = g.gate;
  const live = g.job && ["running", "queued"].includes(g.job.state) ? g.job : null;
  return (
    <>
      <td className="num" title={`${g.messy} messy · Agent 1 applied ${g.agent1.applied}`}>
        <b>{n(g.records)}</b>
        <Seg parts={[{ v: g.records - g.agent1.waiting, cls: "ok" }]} total={Math.max(g.records, 1)} />
      </td>
      <td className="num">
        <b>{n(gt.checked)}</b> / {n(g.records)}{live && live.state === "running" && <small className="live"> · {n(live.done)}/{n(live.total)} now · {fmtTime(live.eta)} left</small>}
        <Seg parts={[{ v: gt.PASS, cls: "pass" }, { v: gt.WARN, cls: "warn" }, { v: gt.REVIEW, cls: "review" }, { v: gt.BLOCK, cls: "block" }, { v: gt.held, cls: "held" }]} total={g.records}
          title={`PASS ${gt.PASS} · WARN ${gt.WARN} · REVIEW ${gt.REVIEW} · BLOCK ${gt.BLOCK} · held ${gt.held}`} />
      </td>
      <td className="num">
        <b>{n(g.ptsl.done)}</b> / {n(g.ptsl.needed)}
        <Seg parts={[{ v: g.ptsl.done, cls: "ok" }]} total={Math.max(g.ptsl.needed, 1)} />
      </td>
      <td className="num"><b>{n(g.gold)}</b></td>
      <td className="num">
        <b>{g.percent}%</b>
        <Seg parts={[{ v: g.records - g.remaining.total, cls: "ok" }]} total={Math.max(g.records, 1)} />
      </td>
      <td className="num remain">
        {g.remaining.total ? (
          <>
            <b>{n(g.remaining.total)}</b>
            <small>{g.remaining.quality_gate ? `${n(g.remaining.quality_gate)} gate` : ""}{g.remaining.quality_gate && g.remaining.ptsl ? " · " : ""}{g.remaining.ptsl ? `${n(g.remaining.ptsl)} PTSL` : ""}</small>
          </>
        ) : "–"}
      </td>
      <td><Chip state={g.state} /></td>
      {actions && <td className="acts" onClick={(e) => e.stopPropagation()}>{actions(g)}</td>}
    </>
  );
}

const FILTERS = [["", "All"], ["waiting", "Waiting"], ["running", "Running"], ["review", "Needs review"], ["done", "Done"]];

export function PipelineView({ st, refresh, notify, onReview }) {
  const [by, setBy] = useState("folder");
  const [folder, setFolder] = useState(null);
  const [q, setQ] = useState("");
  const [state, setState] = useState("");
  const [sort, setSort] = useState("remaining");
  const [page, setPage] = useState(0);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [parallel, setParallel] = useState(3);
  const [busyKey, setBusyKey] = useState("");
  const limit = 50;

  const load = useCallback(async () => {
    try {
      const params = { by: folder !== null ? "file" : by, q, state, sort, offset: page * limit, limit };
      if (folder !== null) params.folder = folder;
      setData(await api.pipeline(params));
      setErr("");
    } catch (e) {
      setErr(e.message);
    }
  }, [by, folder, q, state, sort, page]);

  const running = st.job?.state === "running";
  useEffect(() => {
    load();
    const t = setInterval(() => !document.hidden && load(), running ? 2000 : 5000);
    return () => clearInterval(t);
  }, [load, running]);
  useEffect(() => setPage(0), [by, folder, q, state, sort]);

  const run = async (groups) => {
    setBusyKey(groups ? groups[0] : "*");
    try {
      const r = await api.verifyGroups(groups, parallel);
      notify?.(`Started ${r.started.length} folder${r.started.length === 1 ? "" : "s"}, ${r.parallel} at a time.`);
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
  const active = (data?.active_groups?.running || 0) + (data?.active_groups?.queued || 0);

  if (!data) return <div className="card"><p className="sub">{err || "Loading pipeline…"}</p></div>;
  const t = data.total;
  const asFiles = folder !== null || by === "file";
  const pages = Math.max(1, Math.ceil(data.count / limit));
  return (
    <div className="pipe">
      <div className="card">
        <div className="row spread" style={{ marginBottom: 14 }}>
          <div>
            <h2 style={{ margin: 0 }}>Pipeline overview</h2>
            <p className="sub" style={{ margin: 0 }}>{n(data.files)} files in {n(data.folders)} folder{data.folders === 1 ? "" : "s"} · {n(t.records)} records</p>
          </div>
          <div className="kpis">
            <div><b className="num">{t.percent}%</b><span>complete</span></div>
            <div><b className="num">{n(t.remaining.total)}</b><span>left to do</span></div>
            <div><b className="num">{n(t.remaining.ptsl)}</b><span>waiting for PTSL</span></div>
            <div><b className="num">{data.eta != null ? fmtTime(data.eta) : "–"}</b><span>{running ? "step time left" : "no step running"}</span></div>
          </div>
        </div>
        <Flow t={t} />
        <div className="runbar">
          <b>Run folders in parallel</b>
          <label>
            at a time
            <select className="input" value={parallel} onChange={(e) => setParallel(Number(e.target.value))} aria-label="Folders at a time">
              {[1, 2, 3, 4, 6, 8, 12].map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <button className="btn primary" disabled={busyKey === "*" || (st.job?.state === "running" && !active)} onClick={() => run(null)}>
            <Icon name="play" size={16} /> Run all folders
          </button>
          {active > 0 && (
            <button className="btn danger" onClick={() => halt(null)}>
              <Icon name="stop" size={16} /> Stop all ({data.active_groups.running} running{data.active_groups.queued ? `, ${data.active_groups.queued} queued` : ""})
            </button>
          )}
          <span className="mono">Each folder is its own group with its own progress, Stop and review queue.</span>
        </div>
        <div className="legend">
          <span><i className="pass" /> PASS</span><span><i className="warn" /> WARN</span><span><i className="review" /> REVIEW</span><span><i className="block" /> BLOCK</span><span><i className="held" /> held</span><span><i className="wait" /> waiting</span>
        </div>
      </div>

      <div className="card">
        <div className="row spread" style={{ marginBottom: 12 }}>
          <div className="row">
            {folder !== null ? (
              <button className="btn" onClick={() => setFolder(null)}><Icon name="arrow" size={14} /> All folders</button>
            ) : (
              <div className="seg-toggle">
                <button className={by === "folder" ? "on" : ""} onClick={() => setBy("folder")}>By folder</button>
                <button className={by === "file" ? "on" : ""} onClick={() => setBy("file")}>By file</button>
              </div>
            )}
            {folder !== null && <b>{folder || "(top level)"}</b>}
          </div>
          <div className="row">
            <input className="input" placeholder="Search folder or file…" value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="input" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
              <option value="remaining">Most work left</option>
              <option value="percent">Least complete</option>
              <option value="records">Most records</option>
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
        <div className="table-wrap">
          <table className="ptable">
            <thead>
              <tr>
                <th>{asFiles ? "File" : "Folder"}</th>
                {!asFiles && <th>Files</th>}
                <th>Silver · records</th>
                <th>Quality Gate</th>
                <th>PTSL review</th>
                <th>Gold</th>
                <th>Progress</th>
                <th>Left</th>
                <th>State</th>
                {!asFiles && <th>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {data.groups.map((g) => (
                <tr key={g.key} className={!asFiles ? "click" : ""} onClick={() => !asFiles && setFolder(g.key)}>
                  <td className="name">
                    <b>{g.label}</b>
                    {asFiles && g.folder && folder === null && <small className="mono">{g.folder}</small>}
                  </td>
                  {!asFiles && <td className="num">{n(g.files)}</td>}
                  <Cells g={g} actions={!asFiles ? (row) => {
                    const on = row.job && ["running", "queued"].includes(row.job.state);
                    return (
                      <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                        {on ? (
                          <button className="btn danger sm" onClick={() => halt([row.key])} disabled={row.job.stopping}><Icon name="stop" size={13} /> Stop</button>
                        ) : (
                          <button className="btn sm" disabled={!row.remaining.quality_gate || busyKey === row.key} onClick={() => run([row.key])}><Icon name="play" size={13} /> Run</button>
                        )}
                        {row.ptsl.waiting > 0 && !on && <button className="btn sm" onClick={() => onReview?.(row.key)}>Review {n(row.ptsl.waiting)}</button>}
                      </div>
                    );
                  } : null} />
                </tr>
              ))}
              {!data.groups.length && <tr><td colSpan={10} className="empty-row">Nothing matches this filter.</td></tr>}
            </tbody>
          </table>
        </div>
        {pages > 1 && (
          <div className="row spread" style={{ marginTop: 12 }}>
            <span className="mono">{n(data.count)} rows · page {page + 1} of {pages}</span>
            <div className="row">
              <button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
              <button className="btn" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
