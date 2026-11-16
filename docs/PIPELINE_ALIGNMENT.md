# WaRiSAN pipeline alignment

How the dashboard implements the architecture slides:
**Bronze -> Silver [Agent 1 + MinHash] -> Quality Gate [Agent 2] -> PTSL Review -> Gold.**

Code lives in `watan_gold_studio.py` (orchestration and UI), `silver.py` (pure Silver helpers) and `storage.py` (persistence).

| Slide stage | Stepper label | What it does | Code |
|---|---|---|---|
| **0. Bronze** | 1. Bronze & Silver (Extract + MinHash) | Per raw file: SHA-256 of the exact bytes, byte size, masked filename, UTC ingestion time. Source bytes are never modified or stored. | `silver.bronze_entry`, called from `ingest_files`; `batch["bronze"]`; `Storage._register_bronze`, `Storage.list_bronze`, `bronze_registry` table; `pipeline_summary` -> `manifest.json` |
| **1a. Silver rules** | 1 | YAML rules: Unicode NFC, control-character stripping, whitespace normalisation, line-wrap unwrapping, blank-line collapse, paragraph segmentation. Rules are versioned (`rules_sha256`). | `silver.parse_rules`, `silver.silver_clean`, `silver.segment_paragraphs`; applied inside `ingest_files` before PII masking |
| **1b. Agent 1** (Format & Extraction Agent, qwen3-14b) | 1 | Optional (toggle "Enable AI Agent 1 for messy PDF/DOCX restructuring"). Rejoins broken wraps, drops page numbers, returns clean paragraphs, Q&A pairs and structural tags. Output is rejected if it adds words or alters `<IC>`/`<PHONE>`/`<EMAIL>`. | `is_messy` (trigger), `agent1_call`, `agent1_process`, `agent1_apply`, `run_agent1`; guard `silver.validate_agent1_output` |
| **1c. MinHash / LSH** | 1 | 5-shingle MinHash with banded LSH, default Jaccard threshold 0.85, next to the exact-hash dedupe. Members of a near-duplicate group (not the representative) go to human review. | `silver.find_near_duplicates`, `mark_near_duplicates`; `classify` routes `near_duplicate_of` records to PTSL_REVIEW |
| **Sampling filter** | 1 (Silver options) | Rules and MinHash run on every record. With the filter on, only WARN/REVIEW candidates plus a deterministic sample of clean records go to Agent 2. The rest are held at Silver. | `sampling_candidate`, `apply_sampling`; `ui_verification_targets` skips held records |
| **2. Quality Gate** (Qwen Agent 2) | 2. Quality Gate (Qwen Agent 2) | Qwen3-14B verification (parallel, `QWEN_WORKERS`), then the PASS/WARN/REVIEW/BLOCK gate. | `verify_with_qwen`, `verify_record`, `verify_records`, `classify`; `gate_outcome`, `gate_warn_only`, `gate_reasons`, `gate_counts` |
| **3. PTSL review** | 3. PTSL Human Review | Approve, edit or quarantine; Excel review pack; bulk approve for high-confidence rows. | `human_decision`, `bulk_eligible`, `export_review_pack`, `import_review_pack` |
| **4. Gold** | 4. Gold Release | Tier routing, semantic chunking, Parquet/JSONL exports, checksums, `manifest.json`, immutable releases. | `tier_plan`, `semantic_chunks`, `build_exports`, `ReleaseJob`, `bundle_zip`, `Storage.save_release` |

## Gate mapping

| Internal status | Gate | Meaning |
|---|---|---|
| `AUTO_PASS`, `HUMAN_APPROVED` | **PASS** | Eligible for Gold. |
| `PTSL_REVIEW` with only minor flags | **WARN** | No DBP/policy/factual errors, not unsafe, no 3R/explicit/damaged flags, confidence >= 0.75, and a variant, near-malay, near-duplicate, seed-hygiene, Agent 1 problem or a light edit. Routed to human review. |
| `PTSL_REVIEW` otherwise | **REVIEW** | Substantive errors, low confidence, sensitive flags. Human review required. |
| `QUARANTINE`, `ERROR` | **BLOCK** | Excluded from Gold. |
| `DUPLICATE` | n/a | Exact duplicate, counted separately. |

The gate is derived from the status and verification result, so existing statuses, exports and review-pack formats are unchanged. Thresholds are `PASS_THRESHOLD = 0.95` and `REVIEW_THRESHOLD = 0.75`.

## Where the gate shows up

- **UI metrics:** Step 2 shows PASS/WARN/REVIEW/BLOCK counts and a per-record table with colour badges. The Quality & provenance table also has a gate column.
- **Manifest:** `manifest.json` (`manifest_version` 5) gains a `pipeline` block: Bronze files, Silver rules hash and applied-rule counts, near-duplicate stats, Agent 1 counts, sampling counts, gate counts and the per-record gate with reasons.

## Behaviour notes

- Verification still auto-advances to PTSL Review (or Gold Release when nothing needs review). The Quality Gate step is reachable from the stepper once verification finishes.
- Records held by the sampling filter stay `PENDING` and out of Gold, because release requires a current Agent 2 verification. Raise the sample percentage to 100 to verify all of them.
- Agent 1 is off by default and runs at upload, only on messy prose records of at most 6,000 characters. Dialogue and clean prose never reach it. A failed or rejected call keeps the rules-cleaned text and adds a WARN cause.
- The `bronze_registry` table is append-only: SQLite and PostgreSQL triggers reject UPDATE and DELETE. Rows are keyed by `(batch_id, sha256)`.
- Performance: Silver rules and MinHash are pure CPU (500 records in about 0.1 s). Agent 1 and Agent 2 reuse the parallel `ThreadPoolExecutor` path (`QWEN_WORKERS`, default 12, `max_tokens` 1536 for Agent 2).
