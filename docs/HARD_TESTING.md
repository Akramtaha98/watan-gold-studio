# Large-data and Qwen verification tests

Run from the project folder with the app's Python environment. No live H200 is needed for the pipeline stress test. Tests use synthetic records, temporary SQLite storage, and a separate ephemeral mock port. They do not load `.env`, modify your saved batch, or occupy port 8000.

```bash
python scripts/stress_lab.py generate --output output/hard-test
python scripts/stress_lab.py pipeline --output output/hard-test
python -m pytest -q tests --tb=short
```

The generator creates nine upload files, including a 20,020,000-byte / 20,000-record JSONL corpus, a 331-record mixed corpus, and CSV, XLSX, Parquet, TXT, DOCX and PDF fixtures. DOCX includes headings and tables; PDF includes an intentionally blank page requiring OCR. All content and rights declarations are testing fixtures, not a corpus licensed for production release.

The pipeline test covers actual record/file limits, valid rows after malformed JSON, 2,000-record persistence across reopened stores, full 20,000-record verification over HTTP and Gold 1 export, all-tier routing, duplicate provenance, 500-record mixed training/reference exports, 63 Excel approvals, checksums, Parquet round trips, synthetic segregation and benchmark contamination. It measures process peak RSS and timings. Mock character-token timings are not real Qwen tokenizer or H200 throughput measurements.

## Test the running model

```bash
python scripts/stress_lab.py qwen --base-url http://127.0.0.1:8000/v1 --model Qwen/Qwen3-8B --output output/real-qwen-test
```

Set `QWEN_API_KEY` in your environment if needed; do not put credentials in the command or report. You may select `--mode structured_outputs`, `guided_json`, or `json_object` to match your vLLM version. This runs only 26 synthetic verification requests, not the entire large corpus. `qwen_report.json` records expected vs actual outcomes, strict-schema validity through the real app adapter, corrected text, failures and request latency. Expected labels are not sent to the agent.

The evaluator detects the provided mock by its HTTP header, model ownership and model-list marker. A mock assessment explicitly says **real Qwen quality NOT ASSESSED**. Those indicators identify this project's mock; they do not attest the identity of an arbitrary third-party endpoint. The script exits successfully when an assessment is recorded even if quality cases fail: inspect `passed`, `total` and every `failed_checks` entry. It is a diagnostic benchmark, not a production certification.

Checks include contextual spelling, all four routes, near-Malay, neutral 3R, Jawi, dialect preservation, legacy/wrong identities, unsupported facts, invented references, contradictions, safe refusals, unsafe assistant output, prompt injection, multi-turn preservation and PII placeholders. Review labels and proposed edits with a qualified linguist against approved references before production use. A self-reported confidence of 0.98 does not establish 98% accuracy. Benchmark real Qwen separately from its use as a grader; do not use only the same model to certify its own quality.

## Try the fixtures in the dashboard

1. Start with `uploads/complex_mixed.jsonl`, choose **Auto-Route All 4 Gold Stages**, then run verification. With the supplied mock, ingestion produces 322 pending, 1 duplicate and 8 quarantined records. Verification produces 281 auto-pass, 41 review, 1 duplicate and 8 quarantined records. Real Qwen can legitimately produce different results.
2. Inspect the review queue. Test Approve, Quarantine and the Excel review pack. The corpus contains intentional negative cases; do not blindly approve everything or treat these generated releases as training data.
3. Export and inspect the strict JSONL schemas, separate human/synthetic SFT files, source metadata and governance checksums. The mock misses several deliberate quality problems, so AUTO_PASS is not proof of correctness.
4. Use `large_20000_records.jsonl` for upload/verification stress. On a real model this makes up to 20,000 inference requests; run the 26-case quality check first. Large-file inference latency and GPU memory must be measured on the actual H200 endpoint.
5. Test `malformed_rows.jsonl`: four quarantined rows followed by one valid pending row. Test the PDF: eleven text pages and one quarantined blank page. Use the DOCX to inspect section/table preservation.

## Current scaling boundaries

The app accepts at most 25 MiB per file, 20,000 records per batch and 48,000 characters per record. Excess rows are stopped with a visible input issue; they must not be mistaken for a complete import. Export deliberately blocks when benchmark-count × input-record-count exceeds 100,000 leakage comparisons. A 20,000-record batch with a 12% holdout exceeds that budget. Keep the guard enabled; an indexed, cross-corpus leakage check is needed for large evaluation/training releases. Splitting files alone does not establish cross-batch isolation.

Tests prove bounded functional behavior on one local machine. They do not establish multi-user capacity, PostgreSQL production performance, GPU memory safety, independent DBP correctness, or semantic detection of every paraphrased benchmark leak.
