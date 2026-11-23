import React, { useState } from "react";
import { api } from "./api.js";

export const GATES = ["PASS", "WARN", "REVIEW", "BLOCK"];

const paths = {
  upload: "M12 16V4m0 0l-4 4m4-4l4 4M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3",
  check: "M5 12.5l4.5 4.5L19 7.5",
  arrow: "M5 12h14m0 0l-5-5m5 5l-5 5",
  play: "M8 5.5v13l11-6.5z",
  stop: "M7 7h10v10H7z",
  grid: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
  logo: "M9 10l3 12 4-9 4 9 3-12",
  download: "M12 4v12m0 0l-4-4m4 4l4-4M5 20h14",
  reset: "M4 12a8 8 0 108-8M4 4v5h5",
  key: "M15 7a4 4 0 11-3.9 4.9L4 19v-3h3v-3h3l1.1-1.1A4 4 0 0115 7z",
};

export function Icon({ name, size = 20, stroke = 2 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={name === "logo" ? "M5 8l3.2 11L12 11l3.8 8L19 8" : paths[name]} />
    </svg>
  );
}

export function GateBadge({ gate }) {
  return <span className={`badge-g g-${gate}`}>{gate}</span>;
}

export function Toggle({ on, onChange, title, hint, disabled }) {
  return (
    <label className={`toggle ${on ? "on" : ""}`} style={disabled ? { opacity: 0.6, pointerEvents: "none" } : undefined}>
      <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked)} />
      <i className="switch" />
      <div>
        <b>{title}</b>
        <span className="hint">{hint}</span>
      </div>
    </label>
  );
}

export const fmtTime = (s) => {
  s = Math.max(0, Math.round(s || 0));
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
};

const LABELS = {
  verify: { title: "Qwen Agent 2 is checking your records", unit: "records checked", tally: GATES },
  agent1: { title: "Agent 1 is restructuring messy documents", unit: "documents processed", tally: ["applied", "rejected", "error"] },
};

/** Live processing view: ring, speed, ETA, running gate tally and a feed of the latest results. */
export function Processing({ job }) {
  const meta = LABELS[job.kind] || LABELS.verify;
  const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
  const running = job.state === "running";
  const stopped = job.state === "stopped";
  const [asking, setAsking] = useState(false);
  const stop = async () => {
    setAsking(true);
    try {
      await api.stop();
    } catch {
      // the step may have just finished; polling shows the final state
    } finally {
      setAsking(false);
    }
  };
  return (
    <div className="proc" role="status" aria-live="polite">
      <div className={`ring ${running ? "live" : ""}`} style={{ "--p": pct }}>
        <div>
          <div className="pct num">{pct}%</div>
          <div className="of num">
            {job.done} of {job.total}
          </div>
        </div>
      </div>
      <h3 style={{ margin: "0 0 4px" }}>{running ? (job.stopping ? "Stopping… finishing the records already in progress" : meta.title) : stopped ? "Stopped" : "Finished"}</h3>
      {stopped && <p className="sub" style={{ margin: "0 0 12px" }}>Records already processed are kept. Run the step again to continue with the rest.</p>}
      {running && (
        <div style={{ margin: "8px 0 16px" }}>
          <button className="btn danger" onClick={stop} disabled={asking || job.stopping}>
            <Icon name="stop" size={16} /> {job.stopping ? "Stopping…" : "Stop"}
          </button>
        </div>
      )}
      <div className="stats">
        <div className="stat">
          <b className="num">{job.rate}</b>
          <span>records / sec</span>
        </div>
        <div className="stat">
          <b className="num">{fmtTime(job.elapsed)}</b>
          <span>elapsed</span>
        </div>
        <div className="stat">
          <b className="num">{running ? fmtTime(job.eta) : "–"}</b>
          <span>time left</span>
        </div>
      </div>
      <div className="bar-wrap" style={{ marginBottom: 16 }}>
        <div className={`bar-fill ${running ? "shimmer" : ""}`} style={{ width: `${pct}%` }} />
      </div>
      {job.kind === "verify" && (
        <div className="tally">
          {GATES.map((g) => (
            <div key={g} className={`t ${g}`}>
              <small>{g}</small>
              <b className="num">{job.counts[g] || 0}</b>
            </div>
          ))}
        </div>
      )}
      {job.kind === "agent1" && (
        <div className="stats">
          {meta.tally.map((k) => (
            <div className="stat" key={k}>
              <b className="num">{job.counts[k] || 0}</b>
              <span>{k}</span>
            </div>
          ))}
        </div>
      )}
      {job.recent?.length > 0 && job.kind === "verify" && (
        <div className="feed">
          {job.recent.slice(0, 5).map((r) => (
            <div className="feed-row" key={r.n}>
              <span className="mono num" style={{ width: 28 }}>#{r.n}</span>
              <GateBadge gate={r.label} />
              <span className="mono">{r.id}</span>
              <span className="grow" />
              <span className="num" style={{ color: "var(--muted)" }}>{r.confidence != null ? `${Math.round(r.confidence * 100)}% sure` : ""}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Messages (dialogue) or plain text, shown as readable text. */
export function asText(value, fallback = "") {
  if (Array.isArray(value) && value.length) return value.map((m) => `${m.role === "user" ? "User" : "Assistant"}: ${m.content}`).join("\n\n");
  return (Array.isArray(value) ? "" : value) || fallback;
}
