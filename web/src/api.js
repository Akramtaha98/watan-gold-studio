// Tiny fetch wrapper. The optional workspace token is kept for this tab only.
const KEY = "warisan_token";
export const getToken = () => sessionStorage.getItem(KEY) || "";
export const setToken = (t) => sessionStorage.setItem(KEY, t);

async function call(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["X-Access-Token"] = token;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options = { ...options, body: JSON.stringify(options.body) };
  }
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {}
    const err = new Error(typeof detail === "string" ? detail : "Request failed");
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  auth: () => call("/api/auth"),
  health: () => call("/api/health"),
  state: () => call("/api/state"),
  job: () => call("/api/job"),
  pipeline: (params) => call("/api/pipeline?" + new URLSearchParams(params)),
  settings: (patch) => call("/api/settings", { method: "POST", body: patch }),
  upload: (files) => {
    const form = new FormData();
    // items are Files (folder picker sets webkitRelativePath) or {file, path} from a dropped folder
    [...files].forEach((f) => (f.file ? form.append("files", f.file, f.path) : form.append("files", f, f.webkitRelativePath || f.name)));
    return call("/api/upload", { method: "POST", body: form });
  },
  sample: () => call("/api/sample", { method: "POST" }),
  verify: () => call("/api/verify", { method: "POST" }),
  stop: (groups) => call("/api/stop", { method: "POST", body: groups ? { groups } : {} }),
  verifyGroups: (groups, parallel) => call("/api/groups/verify", { method: "POST", body: { groups, parallel } }),
  records: (params) => call("/api/records?" + new URLSearchParams(params)),
  record: (id) => call(`/api/records/${id}`),
  review: (id, action) => call(`/api/records/${id}/review`, { method: "POST", body: { action } }),
  approveConfident: () => call("/api/review/approve-confident", { method: "POST" }),
  release: () => call("/api/release", { method: "POST" }),
  applyRights: () => call("/api/rights/apply", { method: "POST" }),
  reset: () => call("/api/reset", { method: "POST" }),
  downloadUrl: () => "/api/release/download" + (getToken() ? `?token=${encodeURIComponent(getToken())}` : ""),
  manifestUrl: () => "/api/release/manifest" + (getToken() ? `?token=${encodeURIComponent(getToken())}` : ""),
};

/** Files from a drop, walking dropped folders so each file keeps its relative path. */
export async function filesFromDrop(dataTransfer) {
  const items = [...(dataTransfer.items || [])].map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)).filter(Boolean);
  if (!items.length) return [...dataTransfer.files];
  const out = [];
  const readAll = (reader) =>
    new Promise((resolve, reject) => {
      const all = [];
      const next = () => reader.readEntries((batch) => (batch.length ? (all.push(...batch), next()) : resolve(all)), reject);
      next();
    });
  const walk = async (entry, prefix) => {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej));
      out.push({ file, path: prefix + entry.name });
    } else if (entry.isDirectory) {
      for (const child of await readAll(entry.createReader())) await walk(child, prefix + entry.name + "/");
    }
  };
  for (const entry of items) await walk(entry, "");
  return out;
}
