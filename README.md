# WaRiSAN Gold Studio

A persistent Malay corpus curation workspace: upload → Qwen verification → PTSL review → validated, versioned Gold release. The guided wizard and focused reviewer mode share the same masked database as the external portal API.

## Run locally

Python 3.11+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run watan_gold_studio.py
```

The default database is `watan_gold_studio.db` beside the app. It is created automatically; raw uploaded file bytes are never stored. Native analytics require no separate chart package.

For a demonstration without H200, run `python3 mock_qwen.py` in another terminal. Keep `http://127.0.0.1:8000/v1` and `Qwen/Qwen3-8B`, then click **Test Connection**. The mock is scripted and uses reversible character tokens; it does not assess DBP quality or provide real Qwen token counts.

Start the portal API from this folder, in a terminal with a private bearer secret:

```bash
export PTSL_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
python ptsl_api.py
```

The API listens at **127.0.0.1:8001**. Both processes use the same `DATABASE_URL` and `RELEASES_DIR`. The generated token stays in that terminal's environment; configure it in the external portal's backend secret store. Do not place it in a public frontend bundle or commit it.

## Guided workflow

1. **Upload & Verify.** Upload JSONL/XLSX/CSV/Parquet/TXT/DOCX/PDF. Choose automatic routing or a compatible tier; column mapping is collapsed. PII is masked before inference. Run Qwen verification. Every completed record is saved; retryable errors remain in Step 1. The wizard opens review if needed, otherwise advances directly to release. The **Analytics** expander shows routed tier/origin counts, quality-gate counts, DBP-score histogram and language/script flags. Scores are model estimates.
2. **PTSL Human Review.** Compare masked original with proposed edits and Approve, Edit & Approve or Quarantine. DBP corrections require Linguist/Project_Lead; mutable facts require Project_Lead. Stage filters never complete another tier's unresolved queue. Excel Good/Bad/Unsure packs remain an offline fallback: registrations, snapshots, UTC decisions and replay protection now survive restarts in the DB. Unsure stays queued. **External PTSL Portal Sync** reads the shared database, optionally polling every five seconds; portal completion automatically advances to Step 3. Concurrent/stale edits are rejected rather than overwriting newer decisions.
3. **Validate & Export.** Dedicated evaluation inputs are default; optional 10–15% SFT holdout groups whole intents/answer stems. Final schema, PII, permissions and cross-tier leakage checks run before release. The default ZIP contains only five clean JSONL files. Enable **Include governance & audit files** for full archival artifacts. Every finalized release retains a full immutable snapshot in the DB and versioned filesystem mirror, independently of download preference.

Use **Saved batch** to reopen work after a browser/server restart. **Start new batch** creates a fresh workspace without deleting old data. **Release History** inspects and re-downloads earlier, checksum-verified snapshots; missing filesystem mirrors can be restored from the authoritative DB archive.

If a saved-batch reference is missing from the current database, the app now recovers without a traceback. A different healthy active batch stays open. If the missing batch was active, its masked in-memory workspace is retained and exports pause until you explicitly choose **Save recovered workspace as a new batch**. Save that recovery before closing the browser or switching workspaces; it does not recreate an unavailable original DB row or delete another batch.

**PTSL Reviewer Only Mode** hides upload/technical settings, raw pipeline metrics and exports, retaining focused queue/reviewed/role counters, comparison, decisions and Excel. Linguist defaults to this view unless a workspace mode was explicitly chosen. `?mode=ptsl` opens the focused view; saved batches and queue progress are now shared through persistence. At completion it shows a handoff card; Full Pipeline Mode performs the release. Roles in the local workspace are trusted curator declarations, not per-person authentication; use the production access gate/reverse proxy described below.

## Output contracts

| Tier | Default JSONL | Canonical governance storage |
|---|---|---|
| Gold 1 | `gold_1_canonical_cpt.jsonl` | `gold_1_canonical.parquet` |
| Gold 2 | `gold_2_rag_chunks.jsonl` | `gold_2_rag.parquet` |
| Gold 3 curated | `gold_3_sft_messages.jsonl` | `gold_3_sft.parquet` |
| Gold 3 synthetic | `gold_3_sft_synthetic.jsonl` | same SFT table, separate origin |
| Gold 4 | `gold_4_eval_benchmark.jsonl` | `gold_4_eval.parquet` |

JSONL uses UTF-8, one object per line, with complete multi-turn conversations intact. Full strict schema definitions remain in `watan_gold_studio.py`: canonical Parquet is written/read before lean JSONL projection. Gold 1 preserves document order. Gold 2 preserves paragraphs/pages/sections, 300–600 actual configured model tokens and 50–80 overlap (default 64); short sources stay intact and are flagged. A proxy must expose root `/tokenize` and `/detokenize` as well as `/v1/*`.

ShareGPT `from/value` conversations are optional projections, never a duplicate SFT Parquet table. Explicit synthetic sources remain separate. Rich snapshots include `manifest.json`, `dataset_card.md` and `checksums.sha256`; these are not in the default clean ZIP. Download governance artifacts in the wizard/history to obtain them.

Snapshot mirrors are under `releases/<gold_version>/<release_id>/`. Rebuilding an identical release reuses its immutable existing snapshot. A changed source/approval state cannot replace an existing release. JSONL-only downloads do not strip governance from storage. Verify full bundles with `sha256sum -c checksums.sha256` (macOS: `shasum -a 256 -c checksums.sha256`).

## Source metadata and quality safeguards

- Prose: `text`/`paragraph_text`/`body`/`content`. Dialogue: `messages`, supported `conversations` or prompt/answer pairs. One optional system turn precedes alternating complete user/assistant pairs.
- Lineage: source/document/paragraph IDs, paragraph order, page, section and `silver_run_id`. Records receive batch-qualified API IDs to avoid collisions across uploads.
- Governance: `licence_status: approved|permitted` plus explicit `allowed_uses: [train, rag_display, eval]` per required product. Missing approvals remain canonical but are excluded from training/index/eval JSONL. The app does not verify source contracts.
- Origin: human/synthetic declaration, or SFT hand-written/synthetic/from_gold_1 with required derived paragraph IDs. No AI-authorship inference is claimed.
- Benchmark: dedicated turns/reference answer or eval/benchmark tags; category, intent group, expected behaviour, choices/scoring/rubric and approved author role are supported. No missing references are invented.
- Dialect, old spelling, Jawi and Indonesian near-Malay are retained/tagged for review; 3R, explicit and damaged-character flags are surfaced. Legacy checkpoint labels, hedging seeds and mutable institutional facts require review.
- Standard PII patterns are masked before inference; only explicitly tagged synthetic records may preserve narrowly reserved fake fixtures. Portal queues and Excel packs always mask these fixtures too. API edits pass the same user/system-turn and PII-placeholder validators as local review.
- Benchmark isolation uses intent/answer-stem connected groups, source-lineage withholding and exact/near lexical checks, including a final export scan. Remaining contamination blocks release. It cannot prove universal semantic paraphrase isolation or detect every obfuscated PII pattern.

`examples/` contains fictional fixtures, not approved real-world data. PDF ingestion needs a text layer; image-only pages need external OCR. Existing limits remain 25 MiB per file, 20,000 records per batch and 48,000 characters per record. SQLite snapshots target moderate local curation batches; benchmark production throughput with your own corpus before large multi-user deployment.

## External portal and webhooks

See [docs/portal-integration.md](docs/portal-integration.md) for queue, single/batch verdicts, revision conflicts, HMAC verification and retries. API credentials default to **Linguist** scope; explicitly configure `PTSL_API_REVIEWER_ROLES` to authorize other trusted service roles. Submitted role names cannot grant permissions beyond that credential's scope.

Configured outgoing webhooks use a persistent transactional outbox. Events contain IDs/counts, not corpus text; deliveries have a stable event ID and timestamped HMAC signature, retry with backoff, and require receiver deduplication. No network delivery occurs without a configured target/secret. A failed remote webhook does not undo a committed review; polling/status still works.

## Production configuration

Copy `.env.example` into your private environment/secret manager. Environment files are not automatically executed by the app; load them in your service manager or shell. Never commit `.env`, the DB, releases, raw corpus, logs or secret files.

- `DATABASE_URL`: default project-local SQLite; `postgresql+psycopg://...?...sslmode=require` supports PostgreSQL/Supabase database connections. A Supabase HTTP API URL is not a database URL. PostgreSQL tables use a dedicated `warisan` schema (`DATABASE_SCHEMA` can choose another private name). Keep that schema out of Supabase Exposed schemas and use backend-only DB credentials; [Supabase documents why exposed SQL-created tables need explicit protection](https://supabase.com/docs/guides/database/tables). The connection role must be able to bootstrap/own this schema. Changing this variable connects to a different database; it does not migrate existing SQLite data automatically. Use a controlled, tested migration for existing data.
- `RELEASES_DIR`: private, durable release mirror path. The DB retains authoritative archives; all Streamlit/API workers must use the same DB. Shared mirrors require a durable local/shared volume; SQLite WAL must not be hosted on an unsuitable network filesystem.
- `STUDIO_ENV=production`: requires a random `STUDIO_ACCESS_TOKEN` of at least 32 characters. The local UI prompts for it before accessing data. Use an authenticated HTTPS reverse proxy/OIDC layer for named users/tenant isolation. The built-in shared token is a trusted-curator workspace gate, not enterprise RBAC.
- `PTSL_API_TOKEN`: random secret ≥32 characters, kept on the external portal backend. Protect the API with HTTPS/reverse proxy before making it remotely reachable. Both development entry points bind loopback by default; CORS is denied unless explicit origins are configured.
- `PTSL_WEBHOOK_URL`/`PTSL_WEBHOOK_SECRET`: optional HTTPS receiver and secret ≥32 characters. Localhost HTTP is allowed only in development.
- `QWEN_BASE_URL`, `QWEN_MODEL`, `QWEN_API_KEY`: configure a real vLLM instance for live acceptance; API keys are never stored with batch contexts/releases.

Schema version 1 is created automatically. Unknown schema versions fail closed; future changes need explicit migrations. Use least-privilege DB credentials, restricted volume permissions, backups, TLS, monitoring and an approved source/licence/DBP policy before production exposure. No external portal URL, deployment domain or hosted database was supplied; local integration is implemented and tested, not a claim that your external site or hosted DB has already been deployed.

Backup SQLite consistently (includes committed WAL data):

```bash
python manage.py backup --output backups/studio-snapshot.db
python manage.py restore-releases
```

Use managed backups/`pg_dump` for PostgreSQL. Test restoration in a separate environment. The full release archive is in the DB, so DB backups also protect finalized datasets. Source-only repository/ZIP packages deliberately exclude all private runtime data.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```

All tests isolate storage and mock inference. Coverage includes readers, schemas, roles, Excel atomicity, masking, token bounds, benchmark isolation, persistent restarts/conflicts, authenticated API submissions, webhook signing/retries, release history, native analytics and wizard progression after external review. A real PostgreSQL integration test runs when `TEST_POSTGRES_URL` is set; The template `ci/github-actions-tests.yml` provisions an isolated PostgreSQL service when installed as an Actions workflow. The current GitHub OAuth credential lacks workflow scope, so it is shipped inactive; application source and tests can still be pushed privately. Activate it later by adding the template at `.github/workflows/tests.yml` with an appropriately authorized GitHub login. A live H200 is not required for CI.

## Large-data and adversarial tests

See [test instructions](docs/HARD_TESTING.md) and [measured results](docs/HARD_TEST_RESULTS.md). Generate upload fixtures with `python scripts/stress_lab.py generate`, run the isolated pipeline test with `python scripts/stress_lab.py pipeline`, and assess the configured endpoint with `python scripts/stress_lab.py qwen`. The endpoint assessment explicitly distinguishes the demonstration mock from real Qwen inference.
