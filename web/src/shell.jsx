import React, { useState } from "react";
import { GateStep, ReleaseStep, ReviewStep, UploadStep } from "./steps.jsx";
import { PipelineView } from "./pipeline.jsx";
import { Icon } from "./ui.jsx";

/** Stepper + the active step. Steps unlock as the pipeline advances. */
export function Pipeline({ st, view, setView, refresh, notify }) {
  const [group, setGroup] = useState(null);
  const has = st.has_batch;
  const released = st.release?.status === "ready";
  const steps = [
    { n: 1, title: "Bronze & Silver", sub: has ? `${st.records} records` : "Upload & clean", done: has, open: true, pct: has ? 100 : 0 },
    { n: 2, title: "Quality Gate", sub: st.verified ? `${st.gates.PASS} pass · ${st.gates.BLOCK} blocked` : "Qwen Agent 2", done: has && st.verified && st.pending === 0, open: has,
      pct: has && st.records ? Math.round(((st.records - st.pending - st.gates.DUPLICATE) / Math.max(1, st.records - st.gates.DUPLICATE)) * 100) : 0 },
    { n: 3, title: "Human Review", sub: st.verified ? (st.review ? `${st.review} waiting` : "All reviewed") : "PTSL queue", done: has && st.verified && st.review === 0, open: has && st.verified,
      pct: st.verified && st.review === 0 ? 100 : 0 },
    { n: 4, title: "Gold Release", sub: released ? "Ready to download" : `${st.gold_ready || 0} ready`, done: released, open: has && st.gold_ready > 0, pct: released ? 100 : 0 },
  ];
  steps.push({ n: 5, title: "Pipeline", sub: has ? `${st.records} records · all files` : "Per file & folder", done: false, open: has, pct: 0, overview: true });
  const props = { st, refresh, notify, go: setView };
  return (
    <>
      <nav className="stepper five" aria-label="Pipeline steps">
        {steps.map((s) => (
          <button key={s.n} className={`step ${view === s.n ? "active" : ""} ${s.done ? "done" : ""}`} disabled={!s.open} onClick={() => { if (s.n === 3) setGroup(null); setView(s.n); }} aria-current={view === s.n ? "step" : undefined}>
            <span className="badge">{s.done ? <Icon name="check" size={16} stroke={3} /> : s.overview ? <Icon name="grid" size={16} /> : s.n}</span>
            <span>
              <b>{s.title}</b>
              <small>{s.sub}</small>
            </span>
            <i className="bar" style={{ width: `${s.pct}%` }} />
          </button>
        ))}
      </nav>
      {view === 1 && <UploadStep {...props} />}
      {view === 2 && has && <GateStep {...props} />}
      {view === 3 && has && <ReviewStep {...props} group={group} clearGroup={() => setGroup(null)} />}
      {view === 4 && has && <ReleaseStep {...props} />}
      {view === 5 && has && <PipelineView st={st} refresh={refresh} notify={notify} onReview={(key) => { setGroup(key); setView(3); }} />}
    </>
  );
}
