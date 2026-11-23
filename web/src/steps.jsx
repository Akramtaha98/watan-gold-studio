import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, filesFromDrop } from "./api.js";
import { GATES, GateBadge, Icon, Processing, Toggle, asText } from "./ui.jsx";

const short = (h) => (h ? `${h.slice(0, 10)}…${h.slice(-6)}` : "");
const USES = [["train", "Training"], ["rag_display", "RAG display"], ["eval", "Evaluation"]];
const utc = (iso) => (iso ? iso.replace("T", " ").slice(0, 19) + " UTC" : "");
const kb = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

/* ------------------------------------------------------------------ 1 */
export function UploadStep({ st, refresh, notify, go }) {
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef();
  const folderInput = useRef();
  const s = st.settings;
  const job = st.job?.kind === "agent1" ? st.job : null;

  const set = async (patch) => {
    try {
      await api.settings(patch);
      refresh();
    } catch (e) {
      notify(e.message);
    }
  };
  const send = async (fn) => {
    setBusy(true);
    try {
      await fn();
      await refresh();
    } catch (e) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="card">
        <h2>Add your data</h2>
        <p className="sub">Drop JSONL, JSON, CSV, TXT, PDF or DOCX files. Every file is fingerprinted before anything changes it.</p>
        <div
          className={`drop ${over ? "over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); send(async () => api.upload(await filesFromDrop(e.dataTransfer))); }}
        >
          <div className="icon"><Icon name="upload" size={26} /></div>
          <h3>{busy ? "Reading files…" : "Drag files here"}</h3>
          <p>or choose them from your computer</p>
          <div className="row" style={{ justifyContent: "center" }}>
            <button className="btn primary" disabled={busy} onClick={() => input.current.click()}>Choose files</button>
            <button className="btn" disabled={busy} onClick={() => folderInput.current.click()}>Choose folder</button>
            <button className="btn" disabled={busy} onClick={() => send(api.sample)}>Try sample data</button>
          </div>
          <input ref={input} type="file" multiple hidden onChange={(e) => e.target.files.length && send(() => api.upload(e.target.files))} />
          <input ref={folderInput} type="file" multiple hidden webkitdirectory="" directory="" onChange={(e) => e.target.files.length && send(() => api.upload(e.target.files))} />
        </div>

        <div className="options mt">
          <Toggle on={s.silver_enabled} onChange={(v) => set({ silver_enabled: v })} title="Clean text with Silver rules" hint="Fix spacing, broken lines and page numbers using the rules file." />
          <Toggle on={s.near_dup_enabled} onChange={(v) => set({ near_dup_enabled: v })} title={`Find near-duplicates (${Math.round(s.near_dup_threshold * 100)}% similar)`} hint="MinHash compares 5-word shingles and flags look-alikes." />
          <Toggle on={s.agent1_enabled} onChange={(v) => set({ agent1_enabled: v })} title="AI Agent 1 for messy PDF/DOCX" hint="Qwen restructures messy text. It never rewrites or adds words." />
        </div>

        <div className="tile mt">
          <h4>Rights · what the export gate needs</h4>
          <Toggle on={s.assume_licence} onChange={(v) => set({ assume_licence: v })} title="Treat files with no licence as approved" hint="Plain PDF, TXT and CSV files carry no licence field. Without this they would be dropped at release. A licence written inside a JSONL row always wins." />
          <div className="row mt">
            {USES.map(([k, label]) => (
              <label key={k} className="check-inline">
                <input type="checkbox" checked={s.default_allowed_uses.includes(k)} onChange={(e) => set({ default_allowed_uses: e.target.checked ? [...s.default_allowed_uses, k] : s.default_allowed_uses.filter((x) => x !== k) })} /> {label}
              </label>
            ))}
          </div>
          <p className="mono" style={{ margin: "10px 0 0" }}>Applies to files uploaded after you change it. For a batch already loaded, use "Apply default rights" on the Gold release step.</p>
        </div>
      </div>

      {job && (
        <div className="card">
          <Processing job={job} />
        </div>
      )}

      {st.has_batch && (
        <div className="card">
          <div className="row spread" style={{ marginBottom: 14 }}>
            <div>
              <h2 style={{ margin: 0 }}>What happened to your data</h2>
              <p className="sub" style={{ margin: 0 }}>{st.records} records ready for the Quality Gate</p>
            </div>
            <button className="btn primary big" disabled={st.job?.state === "running"} onClick={() => go(2)}>
              Continue <Icon name="arrow" />
            </button>
          </div>
          <div className="flow">
            <div className="tile">
              <h4>Bronze · raw files</h4>
              {st.bronze.map((f) => (
                <div className="kv" key={f.sha256}>
                  <span title={f.sha256}>{f.name}<div className="mono">{short(f.sha256)}</div></span>
                  <span className="num">{kb(f.bytes)}</span>
                </div>
              ))}
              <p className="mono" style={{ margin: "10px 0 0" }}>Fingerprint (SHA-256), size and UTC time are saved and cannot be edited.</p>
            </div>
            <div className="arrow"><Icon name="arrow" size={24} /></div>
            <div className="tile">
              <h4>Silver · cleaned</h4>
              <div className="kv"><span>Records</span><b className="num">{st.records}</b></div>
              <div className="kv"><span>Messy documents</span><b className="num">{st.silver.messy}</b></div>
              <div className="kv"><span>Exact duplicates</span><b className="num">{st.silver.duplicates}</b></div>
              <div className="kv"><span>Near-duplicate groups</span><b className="num">{st.silver.near_duplicate?.enabled ? st.silver.near_duplicate.groups : "off"}</b></div>
              <div className="kv"><span>Agent 1</span><b className="num">{st.silver.agent1?.enabled ? `${st.silver.agent1.applied ?? 0} fixed` : "off"}</b></div>
            </div>
          </div>
          {st.issues.length > 0 && <div className="note mt">{st.issues.length} row(s) were skipped: {st.issues[0]}</div>}
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ 2 */
export function RecordTable({ st, onOpen, status }) {
  const [gate, setGate] = useState("");
  const [q, setQ] = useState("");
  const [data, setData] = useState({ total: 0, items: [] });
  const [limit, setLimit] = useState(25);
  useEffect(() => {
    const t = setTimeout(() => api.records({ gate, q, limit, ...(status ? { status } : {}) }).then(setData).catch(() => {}), 200);
    return () => clearTimeout(t);
  }, [gate, q, limit, st.gates, status]);
  const chips = [["", "All", st.records], ...GATES.map((g) => [g, g, st.gates[g]]), ["PENDING", "Not checked", st.gates.PENDING], ["DUPLICATE", "Duplicate", st.gates.DUPLICATE]];
  return (
    <>
      <div className="row spread" style={{ margin: "6px 0 12px" }}>
        <div className="chips">
          {chips.filter(([k, , n]) => k === "" || n > 0).map(([k, label, n]) => (
            <button key={k} className={`chip ${gate === k ? "on" : ""}`} onClick={() => setGate(k)}>{label} <i className="num">{n}</i></button>
          ))}
        </div>
        <input className="input" placeholder="Search text or ID" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <table>
        <thead><tr><th>Result</th><th>Text</th><th>Sure</th></tr></thead>
        <tbody>
          {data.items.map((r) => (
            <tr key={r.id} className="click" onClick={() => onOpen?.(r.id)}>
              <td style={{ width: 120 }}><GateBadge gate={r.held ? "PENDING" : r.gate} /></td>
              <td>
                <div className="trunc">{r.preview}</div>
                <div className="tags">
                  <span className="tag">{r.source}</span>
                  {r.held && <span className="tag">held at Silver</span>}
                  {r.silver.near_duplicate_of && <span className="tag">near-duplicate</span>}
                  {r.silver.agent1 === "applied" && <span className="tag">Agent 1 cleaned</span>}
                  {r.warnings.map((w) => <span className="tag" key={w}>{w.replace(/_/g, " ")}</span>)}
                </div>
              </td>
              <td className="num" style={{ width: 70 }}>{r.confidence != null ? `${Math.round(r.confidence * 100)}%` : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.items.length === 0 && <div className="empty">No records match.</div>}
      {data.total > data.items.length && <div className="row" style={{ justifyContent: "center", marginTop: 12 }}><button className="btn" onClick={() => setLimit(limit + 50)}>Show more ({data.total - data.items.length} left)</button></div>}
    </>
  );
}

export function GateStep({ st, refresh, notify, go }) {
  const s = st.settings;
  const job = st.job?.kind === "verify" ? st.job : null;
  const running = st.job?.state === "running";
  const [open, setOpen] = useState(null);
  const [detail, setDetail] = useState(null);
  const [adv, setAdv] = useState(false);
  useEffect(() => { open ? api.record(open).then(setDetail).catch(() => setDetail(null)) : setDetail(null); }, [open]);

  const set = async (patch) => { try { await api.settings(patch); refresh(); } catch (e) { notify(e.message); } };
  const run = async () => {
    try { await api.verify(); await refresh(); } catch (e) { notify(e.message); }
  };
  const toCheck = st.pending;

  return (
    <>
      <div className="card">
        <div className="row spread">
          <div>
            <h2>Quality Gate · Qwen Agent 2</h2>
            <p className="sub" style={{ margin: 0 }}>Qwen reads every record and gives it a clear result: PASS, WARN, REVIEW or BLOCK.</p>
          </div>
          {!running && (
            <button className="btn primary big" disabled={toCheck === 0} onClick={run}>
              <Icon name="play" size={18} /> {toCheck === 0 ? "All records checked" : st.verified ? `Check ${toCheck} more` : `Check ${toCheck} records`}
            </button>
          )}
        </div>
        {!running && (
          <>
            <div className="options mt">
              <Toggle on={s.sampling_enabled} onChange={(v) => set({ sampling_enabled: v })} title="Only send a sample to Qwen" hint={`Sends records with warnings plus ${s.sample_pct}% of clean ones. The rest stay held at Silver and are not released.`} />
              <div className="tile" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <div><b>Speed</b><div className="sub" style={{ margin: 0, fontSize: 12.5 }}>{s.workers} records checked at once on {s.model}</div></div>
                <button className="btn ghost" onClick={() => setAdv(!adv)}>{adv ? "Hide" : "Adjust"}</button>
              </div>
            </div>
            {adv && (
              <div className="row mt">
                <label className="field">Parallel workers<input className="input num" type="number" min="1" max="64" value={s.workers} onChange={(e) => set({ workers: e.target.value })} /></label>
                <label className="field">Sample %<input className="input num" type="number" min="1" max="100" value={s.sample_pct} onChange={(e) => set({ sample_pct: e.target.value })} /></label>
                <label className="field">Model<input className="input" value={s.model} onChange={(e) => set({ model: e.target.value })} /></label>
              </div>
            )}
          </>
        )}
      </div>

      {job && running && <div className="card"><Processing job={job} /></div>}

      {!running && st.verified && (
        <div className="card">
          <div className="row spread" style={{ marginBottom: 6 }}>
            <h2 style={{ margin: 0 }}>Results</h2>
            <button className="btn primary" onClick={() => go(st.review > 0 ? 3 : 4)}>{st.review > 0 ? `Review ${st.review} flagged` : "Go to release"} <Icon name="arrow" /></button>
          </div>
          <div className="tally">
            {GATES.map((g) => <div key={g} className={`t ${g}`}><small>{g}</small><b className="num">{st.gates[g]}</b></div>)}
          </div>
          <RecordTable st={st} onOpen={(id) => setOpen(open === id ? null : id)} />
          {detail && (
            <div className="note mt">
              <b>{detail.verification.reason || detail.reason}</b>
              <div className="cmp">
                <div className="box"><h5>Original</h5>{asText(detail.original, detail.original_text)}</div>
                <div className="box new"><h5>After Qwen</h5>{asText(detail.candidate, detail.text)}</div>
              </div>
            </div>
          )}
        </div>
      )}
      {!running && !st.verified && toCheck === 0 && <div className="card empty">All records were held or are duplicates. Turn off sampling to check them.</div>}
    </>
  );
}

/* ------------------------------------------------------------------ 3 */
export function ReviewStep({ st, refresh, notify, go, group = null, clearGroup }) {
  const [queue, setQueue] = useState(null);
  const [seen, setSeen] = useState([]);
  const [item, setItem] = useState(null);
  const [busy, setBusy] = useState(false);
  const s = st.settings;

  const load = useCallback(async () => {
    const d = await api.records(group === null ? { status: "PTSL_REVIEW", limit: 200 } : { status: "PTSL_REVIEW", limit: 200, group });
    setQueue(d.items);
  }, [group]);
  useEffect(() => { load().catch(() => {}); }, [load, st.review]);
  const next = queue?.find((r) => !seen.includes(r.id));
  useEffect(() => { next ? api.record(next.id).then(setItem).catch(() => setItem(null)) : setItem(null); }, [next?.id]);

  const act = useCallback(async (action) => {
    if (!next || busy) return;
    setBusy(true);
    try {
      await api.review(next.id, action);
      if (action === "UNSURE") setSeen((x) => [...x, next.id]);
      await refresh();
      await load();
    } catch (e) { notify(e.message); } finally { setBusy(false); }
  }, [next, busy, refresh, load, notify]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.metaKey || e.ctrlKey) return;
      const k = e.key.toLowerCase();
      if (k === "a") act("APPROVE"); else if (k === "q") act("QUARANTINE"); else if (k === "u") act("UNSURE");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [act]);

  const bulk = async () => {
    try { const r = await api.approveConfident(); notify(`Approved ${r.approved} confident record${r.approved === 1 ? "" : "s"}.`); await refresh(); await load(); } catch (e) { notify(e.message); }
  };

  if (st.review === 0) {
    return (
      <div className="card done-hero">
        <div className="check"><Icon name="check" size={34} stroke={3} /></div>
        <h2>Nothing left to review</h2>
        <p className="sub">Every flagged record has a human decision.</p>
        <button className="btn primary big" onClick={() => go(4)}>Go to release <Icon name="arrow" /></button>
      </div>
    );
  }
  const remaining = (queue || []).filter((r) => !seen.includes(r.id)).length;
  if (group !== null && queue && queue.length === 0) {
    return (
      <div className="card done-hero">
        <div className="check"><Icon name="check" size={34} stroke={3} /></div>
        <h2>Nothing left to review in {group || "(top level)"}</h2>
        <p className="sub">Every flagged record in this folder has a human decision.</p>
        <div className="row" style={{ justifyContent: "center" }}>
          <button className="btn primary big" onClick={() => go(5)}>Back to pipeline <Icon name="arrow" /></button>
          <button className="btn big" onClick={clearGroup}>Review all folders</button>
        </div>
      </div>
    );
  }
  return (
    <div className="card">
      <div className="rev-head">
        <div>
          <h2 style={{ margin: 0 }}>Human review{group !== null && <span className="chip review" style={{ marginLeft: 10, verticalAlign: "middle" }}>Folder: {group || "(top level)"}</span>}</h2>
          <p className="sub" style={{ margin: 0 }}>{group !== null ? `${queue ? queue.length : "…"} waiting in this folder` : `${st.review} waiting`} · {remaining} in your queue{group !== null && <> · <button className="link" onClick={() => go(5)}>back to pipeline</button> · <button className="link" onClick={clearGroup}>all folders</button></>}</p>
        </div>
        <div className="row">
          <label className="field" style={{ gridAutoFlow: "column", alignItems: "center", gap: 8 }}>
            Reviewing as
            <select className="input" value={s.reviewer} onChange={(e) => api.settings({ reviewer: e.target.value }).then(refresh)}>
              {s.roles.map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}
            </select>
          </label>
          <button className="btn" onClick={bulk}>Approve all confident</button>
        </div>
      </div>
      {st.ptsl?.portal > 0 && <div className="note mt">{st.ptsl.portal} decision{st.ptsl.portal === 1 ? "" : "s"} came in from the PTSL review portal{st.ptsl.last_at ? ` (latest ${utc(st.ptsl.last_at)})` : ""}. This page updates by itself.</div>}
      {!next && queue && <div className="note mt">You marked the rest as unsure. <button className="btn ghost" onClick={() => setSeen([])}>Review them again</button></div>}
      {next && item && (
        <>
          <div className="row mt">
            <GateBadge gate={item.gate} />
            <span className="mono">{item.id}</span>
            <span className="tag">{item.category}</span>
            <span className="tag">{item.stage}</span>
            <span className="num" style={{ color: "var(--muted)" }}>{item.confidence != null ? `${Math.round(item.confidence * 100)}% sure` : ""}</span>
          </div>
          <div className="note mt"><b>Why it was flagged:</b> {item.verification?.reason || item.reason}</div>
          <div className="cmp">
            <div className="box"><h5>Original</h5>{asText(item.original, item.original_text)}</div>
            <div className="box new"><h5>Proposed by Qwen</h5>{asText(item.candidate, item.text)}</div>
          </div>
          <div className="row">
            <button className="btn good big" disabled={busy} onClick={() => act("APPROVE")}><Icon name="check" size={18} /> Approve <span className="mono" style={{ color: "#fff" }}>A</span></button>
            <button className="btn bad big" disabled={busy} onClick={() => act("QUARANTINE")}>Quarantine <span className="mono">Q</span></button>
            <button className="btn big" disabled={busy} onClick={() => act("UNSURE")}>Not sure <span className="mono">U</span></button>
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ 4 */
export function ReleaseStep({ st, refresh, notify, go }) {
  const s = st.settings;
  const rel = st.release;
  const building = rel && ["pending", "building"].includes(rel.status) && !rel.error;
  const ready = rel?.status === "ready";
  const start = async () => { try { await api.release(); await refresh(); } catch (e) { notify(e.message); } };
  const waiting = st.review + st.pending;
  const pv = st.preview || { records: st.gold_ready, blocked: 0, blocked_reasons: {}, defaulted: 0, by_stage: {} };
  const rep = rel?.report;
  const applyRights = async () => {
    try { await api.applyRights(); notify("Default rights applied to records that declared none."); await refresh(); } catch (e) { notify(e.message); }
  };
  return (
    <div className="card">
      {ready && rep ? (
        <div>
          <div className="done-hero">
            <div className="check"><Icon name="check" size={34} stroke={3} /></div>
            <h2>Gold {rel.version} is ready</h2>
            <p className="sub">{rep.rows_total} rows in {rep.files.length} file{rep.files.length === 1 ? "" : "s"} · {rep.records_in_gold} record{rep.records_in_gold === 1 ? "" : "s"} reached Gold. Only files with at least one row are in the download.</p>
          </div>
          <table>
            <thead><tr><th>File in the .zip</th><th className="num">Rows</th><th className="num">Size</th><th>SHA-256</th></tr></thead>
            <tbody>
              {rep.files.map((f) => <tr key={f.name}><td><b>{f.name}</b></td><td className="num">{f.rows}</td><td className="num">{kb(f.bytes)}</td><td className="mono">{short(f.sha256)}</td></tr>)}
              {rep.omitted_empty.map((n) => <tr key={n} className="muted-row"><td>{n}</td><td className="num">0</td><td colSpan={2}>left out (no rows)</td></tr>)}
            </tbody>
          </table>
          {rep.excluded.length > 0 && (
            <div className="note mt">
              <b>Records not exported</b>
              <ul className="plain">{rep.excluded.map((e, i) => <li key={i}>{e.count} from {e.file}: {e.reason}</li>)}</ul>
              {rep.excluded_sample.length > 0 && <p className="mono" style={{ margin: 0 }}>e.g. {rep.excluded_sample.join(", ")}</p>}
            </div>
          )}
          {rep.warnings.length > 0 && <div className="note mt">{rep.warnings.map((x, i) => <div key={i}>{x}</div>)}</div>}
          <div className="row mt" style={{ justifyContent: "center" }}>
            <a className="btn" href={api.manifestUrl()} target="_blank" rel="noreferrer">Manifest (JSON)</a>
            <a className="btn primary big" href={api.downloadUrl()}><Icon name="download" /> Download release</a>
          </div>
        </div>
      ) : (
        <>
          <h2>Gold release</h2>
          <p className="sub">Only records that passed or were approved by a person are released.</p>
          {st.ptsl?.portal > 0 && <div className="note">Includes {st.ptsl.portal} decision{st.ptsl.portal === 1 ? "" : "s"} from the PTSL review portal{st.ptsl.last_at ? ` (latest ${utc(st.ptsl.last_at)})` : ""}.</div>}
          <div className="tally">
            <div className="t PASS"><small>READY FOR GOLD</small><b className="num">{st.gold_ready}</b></div>
            <div className="t REVIEW"><small>STILL TO REVIEW</small><b className="num">{st.review}</b></div>
            <div className="t BLOCK"><small>BLOCKED</small><b className="num">{st.gates.BLOCK}</b></div>
            <div className="t"><small style={{ color: "var(--muted)" }}>HELD / DUPLICATE</small><b className="num" style={{ color: "var(--muted)" }}>{st.silver.held + st.silver.duplicates}</b></div>
          </div>
          {pv.blocked > 0 && (
            <div className="note mt">
              <b>{pv.blocked} of {pv.records} Gold record{pv.records === 1 ? "" : "s"} would be left out of the files</b> because the licence is not approved or the required use is missing ({Object.entries(pv.blocked_reasons).map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(", ")}).
              <div className="row mt"><button className="btn" onClick={applyRights}>Apply default rights to records with none</button></div>
            </div>
          )}
          {pv.records > 0 && (
            <p className="sub" style={{ margin: "10px 0 0" }}>By stage: {Object.entries(pv.by_stage).map(([k, n]) => `${k.replace("_", " ")} ${n}`).join(" · ")}. Exact per-file row counts appear after the build, before you download.</p>
          )}
          {building && <div className="bar-wrap mt"><div className="bar-fill shimmer" style={{ width: "100%" }} /></div>}
          {rel?.error && <div className="err mt">{rel.error}</div>}
          <div className="row mt">
            <label className="field">Version<input className="input" value={s.gold_version} onChange={(e) => api.settings({ gold_version: e.target.value }).then(refresh).catch((x) => notify(x.message))} style={{ width: 120 }} /></label>
            <div className="grow" />
            {waiting > 0 && <button className="btn" onClick={() => go(st.review > 0 ? 3 : 2)}>{waiting} still waiting</button>}
            <button className="btn primary big" disabled={building || st.gold_ready === 0} onClick={start}>{building ? "Building…" : "Build Gold release"}</button>
          </div>
        </>
      )}
    </div>
  );
}
