# WaRiSAN Gold specification alignment

Read references: `Gold_Pipeline_Review.pdf` (9 pages, including the dual-layer field contracts on pp7–8) and `Data Engineering Pipeline.pdf` (7 pages; pipeline diagrams reviewed visually). The latest user-authored pasted requirements define the implementation scope. These documents are source specifications, not independent authorisation to change cluster accounts or production systems.

| Requirement | Implementation | Evidence |
|---|---|---|
| Four governance tables, lean projections from Parquet | Four strict full schemas, typed Arrow round trips, JSONL from restored rows; Gold 1 document order | `test_dual_layer.py::test_rich_canonical_contract_and_lean_projection` |
| Canonical messages once, ShareGPT optional, no SFT CSV | One SFT Parquet with all origins; requested classic from/value projection only | Optional ShareGPT/system-turn test; base-file contract tests |
| Dedicated eval by default, richer multi-turn/MCQ behaviour | Default empty eval unless dedicated input; context/reference/choices/scoring schema | Dedicated/default isolation and MCQ contract tests |
| Intent and answer-stem holdout | Connected components, deterministic whole-group selection; protected/derived sources excluded from training tables | Intent/answer-stem tests and four-tier leakage regression suite |
| Block contaminated releases | Exact/near checks before tables and final projected-content scan, including synthetic files | Injected chunk leakage and cross-stage contamination tests |
| Dialect, old spelling, Jawi, near-Malay retention | Review/tag rather than variant-only quarantine; canonical script/dialect/lang and manifest variant flags | Parameterised variant tests; near-Malay low-confidence tests |
| Model-name/hedging/mutable-fact seed hygiene | Flags at intake and verifier proposal; mutable-fact edits require Project_Lead | Seed-hygiene and candidate-edit tests |
| Narrow synthetic fake-PII exception | Explicit synthetic tag + reserved fixtures; real contact values still masked; Excel always masked | Synthetic PII negative/positive checks |
| 3R, explicit content, damaged characters | Strict Qwen fields, review routing, quality counts | Gate/flag and UI tests |
| Duplicate provenance, source fingerprints | SHA-256 sources, stable groups, representative links and manifest duplicate_links | Duplicate precedence/conflict/file alias tests |
| RAG 300–600, overlap 50–80/default64 | Real vLLM tokenize/detokenize; short-source flag; paragraph/page/token lineage | Boundary/transport/lineage tests and dual-layer RAG projection test |
| Licence/allowed uses by product | Approved/permitted plus explicit train/rag_display/eval; no implicit grant from filename/license description | Parameterised permissions tests; canonical retention/projection exclusion |
| Masked Excel Good/Bad/Unsure, role+UTC | Atomic snapshot-bound import; stale/tamper/replay/formula protections; hashes of review notes in release | Excel round-trip/negative tests |
| No staff usernames in governance | Three allowed roles, no reviewer-name field, sanitized legacy audit/source filename removal | Role rejection, legacy audit redaction and sidebar tests |
| Version/Silver linkage, release checksums/card | Gold version per row; run IDs, counts/coverage/duplicate links/config hashes; SHA file including manifest | Version/hash/artifact tests |
| Guided 3-step workflow and focused reviewer workspace | Automatic forward transitions, safe backward navigation, role-based defaults, waiting/completed review states, retained settings, global queue gating | Updated AppTest + test_wizard.py and wide/narrow browser checks |

## Explicit limits

- Selected roles are declarations, not authenticated RBAC. Source allowed uses need owner-approved contracts; this app cannot certify licences.
- Local pattern checks and lexical contamination checks are bounded controls, not universal PII/semantic guarantees. Comparison-budget overflow blocks export.
- Gold 1/2/3/4 tables follow the exact requested fields. Additional provenance stays in the manifest; origin-less synthetic prose/RAG/eval receives separate tables.
- Held-out/connected records are absent from Gold 3 Parquet and training projections. Exclusion lineage stays in the manifest. Other licence-excluded canonical rows are retained but never granted use in JSONL.
- The declared base checkpoint/fact sheet are user-supplied policy, not verified deployment facts. DBP compliance/confidence is model-estimated without an authoritative DBP service.
- No live H200 test is claimed. Offline tests use mocked transport and explicit tokenizer doubles; runtime RAG uses only vLLM token IDs.
- Airflow orchestration, immutable Bronze/object storage, durable multi-user review/approvals, production authentication, licence verification, full-corpus LSH indexing, OCR/layout recovery and release registry remain platform work.

This is an implementation mapping for the requested single-app scope, not a certificate that the full platform or corpus is production approved.

The wizard refactor changes main() and adds UI-only helpers. All 77 pre-existing function/class definitions outside main(), including render_turns, and every top-level schema/constant were compared by Python AST against the pre-refactor source and remained identical.

## Persistent platform extension

SQLite/SQLAlchemy now persists masked batches, public verification context, record snapshots/revisions, Excel registrations, role decisions, immutable full release archives and webhook outbox. DATABASE_URL supports PostgreSQL/Supabase database connections through psycopg; switching URLs is not automatic data migration. Source contracts/DBP authority and real hosted deployment still require owner configuration.

PTSL API token scopes and the existing role/PII/structure rules govern atomic portal submissions. Stale per-task or batch revisions fail without lost updates. Native charts contain aggregate counts. Default download ZIPs are JSONL-only; governance remains stored and is available on demand. Streamlit production startup requires a shared access secret; tenant/named-user authentication belongs at the trusted deployment gateway.
