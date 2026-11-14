# Gates: large and adversarial dashboard test

Scope: reproducible synthetic upload files, isolated large-data pipeline tests, fault injection, and an honest Qwen endpoint assessment.

- [x] G1: large and complex synthetic upload fixtures have fingerprints and documented expected handling
  CHECK: /private/tmp/watan-gold-studio-venv/bin/python scripts/stress_lab.py generate --output output/hard-test
  EXPECT: FIXTURES_READY
  EVIDENCE: 2026-11-15; zsh; project root; exit 0; FIXTURES_READY matched; 9 files, large JSONL 20,020,000 bytes, 331 complex rows; output/hard-test/fixture_manifest.json.

- [x] G2: large ingestion, persistence, review, export and negative controls succeed without using the curator database
  CHECK: /private/tmp/watan-gold-studio-venv/bin/python scripts/stress_lab.py pipeline --output output/hard-test
  EXPECT: PIPELINE_STRESS_PASSED
  EVIDENCE: 2026-11-15; zsh; project root; exit 0; PIPELINE_STRESS_PASSED matched; 20,000 HTTP verifications and exports, 2,000-row reopen, 500-record review/export, 63 Excel approvals; output/hard-test/pipeline_report.json.

- [x] G3: endpoint benchmark records mock identity and per-case failures without certifying mock scores as Qwen quality
  CHECK: /private/tmp/watan-gold-studio-venv/bin/python scripts/stress_lab.py qwen --output output/hard-test
  EXPECT: ENDPOINT_ASSESSMENT_RECORDED
  EVIDENCE: 2026-11-15; zsh; project root; exit 0; ENDPOINT_ASSESSMENT_RECORDED matched; current endpoint mock=true, 17 of 26 cases met expectations, real Qwen explicitly NOT ASSESSED; output/hard-test/qwen_report.json.

- [x] G4: regression and adversarial transport tests pass
  CHECK: /private/tmp/watan-gold-studio-venv/bin/python -m pytest -q tests --tb=short
  EXPECT: /^237 passed, 1 skipped, 1 warning in [0-9.]+s$/m
  EVIDENCE: 2026-11-15; zsh; project root; exit 0; 237 passed, 1 skipped, 1 warning in 28.74s; skip is PostgreSQL-only without TEST_POSTGRES_URL.
