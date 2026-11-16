// API client for the isolated PTSL reviewer portal. It only knows /api/ptsl/* and its own token.
const KEY = "warisan_review_token";
export const getReviewToken = () => sessionStorage.getItem(KEY) || "";
export const setReviewToken = (t) => sessionStorage.setItem(KEY, t);

async function call(path, options = {}) {
  const headers = {};
  const token = getReviewToken();
  if (token) headers["X-Review-Token"] = token;
  if (options.body) {
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
  return res.json();
}

export const portal = {
  auth: () => call("/api/ptsl/auth"),
  stats: () => call("/api/ptsl/stats"),
  queue: (offset = 0, limit = 10) => call(`/api/ptsl/queue?offset=${offset}&limit=${limit}`),
  decide: (id, body) => call(`/api/ptsl/records/${encodeURIComponent(id)}/decision`, { method: "POST", body }),
};
