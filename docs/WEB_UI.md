# WaRiSAN Gold Studio web UI (React)

A simpler front end for the same pipeline as the Streamlit dashboard. It shows the four steps
(**Bronze & Silver, Quality Gate, Human Review, Gold Release**) and a live processing view.
All pipeline logic still lives in `watan_gold_studio.py` and `silver.py`; `studio_api.py` is a thin
HTTP layer, so results, exports and the review rules are identical to the Streamlit app.

## Run (no Node needed on the server)

```bash
pip install -r requirements.txt            # adds python-multipart for uploads
QWEN_BASE_URL=http://127.0.0.1:8000/v1 QWEN_MODEL=qwen3-14b \
  python studio_api.py                      # http://127.0.0.1:8503
```

`web/dist` is committed, so the server only needs Python. Environment variables:
`QWEN_BASE_URL`, `QWEN_MODEL`, `QWEN_API_KEY`, `QWEN_WORKERS` (default 12), `STUDIO_ACCESS_TOKEN`
(shared-workspace token; required with `STUDIO_ENV=production`), `STUDIO_HOST`, `STUDIO_PORT`.

## Develop the UI

```bash
cd web && npm install && npm run dev        # http://localhost:5173, proxies /api to :8503
npm run build                               # refresh web/dist
```

## What each step shows

| Step | What the user sees | Backend |
|---|---|---|
| 1 Bronze & Silver | Drop files, three switches (Silver rules, MinHash near-duplicates, Agent 1), Bronze fingerprints and Silver counts | `POST /api/upload` -> `ingest_files`, `run_agent1` |
| 2 Quality Gate | Run button, live ring with speed / time left, running PASS-WARN-REVIEW-BLOCK tally, live feed, result table | `POST /api/verify` -> `apply_sampling`, `verify_records`; poll `GET /api/job` |
| 3 Human Review | One flagged record at a time: original vs Qwen proposal, Approve (A) / Quarantine (Q) / Not sure (U), approve-all-confident | `human_decision`, `bulk_eligible` |
| 4 Gold Release | Counts, version, build and download | `ReleaseJob` -> `build_exports` |

## Notes

* The workspace is held in memory in one process (one team session). Restarting the server clears it;
  the Streamlit app remains the choice when SQLite persistence is needed.
* Held records (sampling filter) and unreviewed records are never released, same as Streamlit.
* The API binds to 127.0.0.1 by default. Expose it only through a tunnel or proxy with `STUDIO_ACCESS_TOKEN` set.

## Persistent state (SQLite)

Everything the Studio knows is saved to `studio_state.db` (override with `STUDIO_DB`): batches, Bronze file SHA-256 hashes, Silver records, Qwen Agent 2 results (flushed every 20 records and at the end) and every human decision with its UTC timestamp. Restarting `studio_api.py` restores the open batch, the pending review queue and finished reviews. "Start over" archives the batch instead of deleting it. The file is git-ignored; back it up like any dataset.

## Release export rules

* Plain PDF/TXT/CSV files carry no licence field. By default the Studio marks such records `licence_status="approved"` with the allowed uses ticked under *Add your data → Rights* (training, RAG display, evaluation). A licence written in a JSONL row always wins, so a row marked `restricted` stays excluded. Turn the default off to require explicit licences.
* For a batch that is already loaded, *Apply default rights* on the Gold release step fills only the missing values.
* The Gold release screen previews how many Gold records the licence gate would drop and why.
* The `.zip` contains only `.jsonl` files with at least one row. After the build the screen lists every file with its exact row count, size and SHA-256, names the empty files that were left out, and explains each excluded record. If nothing is exportable the build fails with the reasons instead of producing empty files.

## PTSL reviewer portal

For external human evaluators: open `/review` (or `/?mode=ptsl`).

| Variable | Purpose |
| --- | --- |
| `PTSL_REVIEW_TOKEN` | Reviewer credential, at least 32 characters in production. Accepted only by `/api/ptsl/*`; it never opens the admin API. |
| `STUDIO_ACCESS_TOKEN` | Admin token for the dashboard (also opens the portal unless portal-only). |
| `STUDIO_PORTAL_ONLY=1` | This process serves only `/review` and `/api/ptsl/*`; every other `/api/*` path returns 404. Use it for the process behind a public tunnel. |

Reviewers see only WARN/REVIEW records: masked text, Qwen's suggested edit, flag reasons and category/language tags. There is no upload, Qwen setting, raw text, release or export control and no Bronze data in the portal bundle or API. Reviewers choose a role (Linguist, Domain_SME, Project_Lead) and submit Approve, Edit & Approve, Reject / Block or Unsure; each verdict is written to SQLite immediately. The dashboard polls the shared database, so its Human Review counter and Gold totals update within a few seconds. The core rules still apply: DBP corrections need Linguist or Project_Lead, project-fact changes need Project_Lead.
