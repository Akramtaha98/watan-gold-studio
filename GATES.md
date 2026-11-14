# Persistent curation platform acceptance

Scope: persistent batches/reviews/releases, JSONL-first wizard, analytics and authenticated portal integration; publish source only to a new private GitHub repository. Real deployment credentials and live PostgreSQL require deployment-specific validation.

- [x] P1: SQLite survives process/session recreation; PostgreSQL URL support; optimistic concurrency rejects lost updates; only masked corpus snapshots and public inference settings are stored.
  EVIDENCE: SQLite restart, conflict and WAL-backup checks pass; PostgreSQL URL/private-schema adapter and an inactive CI integration template provided; no hosted database connection claimed. Local full suite exit=0; output_sha256=36c6af8bbb19073cd66aaf304f95d75fda268609edc4773b7d3f5ad9dfc4e700.
- [x] P2: Authenticated queue/submit/status, atomic batched role/PII/stale checks and signed outgoing webhook delivery have positive and negative integration tests.
  EVIDENCE: Bearer auth, role scope, strict masks, atomic multi-verdict rollback, stale/replay rejection and signed durable webhook retries pass. Local full suite exit=0; output_sha256=36c6af8bbb19073cd66aaf304f95d75fda268609edc4773b7d3f5ad9dfc4e700.
- [x] P3: Wizard restores/selects persistent batches, syncs external decisions and advances; Excel fallback and native analytics remain focused.
  EVIDENCE: AppTest restore/sync/wizard progression passes; native aggregate charts and focused UI visually inspected. Local full suite exit=0; output_sha256=36c6af8bbb19073cd66aaf304f95d75fda268609edc4773b7d3f5ad9dfc4e700.
- [x] P4: Validated immutable release snapshots persist; default ZIP is JSONL-only; governance is optional; history survives restart with verified downloads.
  EVIDENCE: Default ZIP contains five JSONL files; full immutable DB snapshot/mirror/history and corruption checks pass. Local full suite exit=0; output_sha256=36c6af8bbb19073cd66aaf304f95d75fda268609edc4773b7d3f5ad9dfc4e700.
- [x] P5: Existing backend regressions and new integration/UI tests pass; desktop/narrow layout and deployment docs are checked.
  EVIDENCE: 205 local tests pass; one PostgreSQL case is explicitly skipped locally; the CI template is inactive because the OAuth credential lacks workflow scope. Core validators/routing/leakage/schema logic is preserved. Local full suite exit=0; output_sha256=36c6af8bbb19073cd66aaf304f95d75fda268609edc4773b7d3f5ad9dfc4e700.
- [x] P6: Source-only private GitHub repository is created and pushed, with secrets, databases and corpus/releases excluded; remote privacy and source identity verified.
  EVIDENCE: GitHub reports PRIVATE/isPrivate=true for Akramtaha98/watan-gold-studio; remote main matched audited source commit c8ce3ceaeab6f3c68b5ccd65e586306d27eedc9a. Staging audit excluded DB, releases, raw data and generated credentials. Active workflow rejected for missing OAuth workflow scope; complete CI config is shipped as inactive template without changing permissions.

Measured acceptance: 6 met, 0 unmet, 0 abandoned. Local tests: 205 passed; hosted PostgreSQL test explicitly skipped until TEST_POSTGRES_URL/CI is configured. External portal hosting and production deployment credentials were not supplied.
