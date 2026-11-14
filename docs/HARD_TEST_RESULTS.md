# WaRiSAN Gold Studio — hard-test results

Date: 2026-11-15. Synthetic fixtures only; saved curator data was not changed.

## Result

237 automated tests passed; 1 PostgreSQL-only test skipped because no test PostgreSQL URL is configured. The suite includes 26 new row-isolation, verifier-failure, 16-reviewer contention, 100-verdict atomicity and 1,000-record Streamlit restore tests.

The actual endpoint on port 8000 identifies itself as LOCAL-MOCK-NOT-REAL-QWEN. Its difficult-case assessment passed **17/26** checks. **Real Qwen language, factuality and safety quality remains untested. This is not production signoff.**

## Measured load tests

| Test | Result |
|---|---|
| Large upload | 20,020,000 bytes; 20,000 unique records ingested |
| Full large verification/export | 20,000 HTTP mock verifications; 20,000 Gold 1 rows exported |
| Verification / export time | 26.875 s / 7.263 s, mock only |
| Peak stress-process memory | 663.7 MiB (includes fixture parsing and release construction) |
| Persistence | 2,000 masked records persisted and restored from a temporary SQLite DB |
| Complex input | 331 records; all four Gold routes, duplicates and corrupt rows exercised |
| Formats | JSONL, CSV, XLSX, Parquet, TXT, DOCX, PDF |
| Review/export | 500 HTTP verifications; 63 Excel approvals; human/synthetic separation and checksums passed |
| Multi-reviewer conflict | 16 simultaneous attempts on one record: exactly one accepted |
| Atomic verdict batch | Invalid final verdict rolls back all 100; valid batch accepts all 100 |
| Leakage controls | Known near-duplicate blocked; clean benchmark exported; large comparison budget fails closed |

The large load uses a deterministic mock; these numbers are not real Qwen or H200 performance. The 500-record release intentionally has no eval records; the dedicated benchmark positive control separately verifies Gold 4 export. Gold 2 sources also contribute canonical paragraphs, so output row counts need not equal input stage counts.

## Real bug found and fixed

A non-object JSONL value such as `[]`, `null`, a string or a number caused `AttributeError` before row validation and stopped reading later valid rows. The ingester now quarantines each such row and continues. Six parameterized cases plus a 300-malformed-line negative fixture protect the fix. No corpus schema or leakage guard was relaxed.

## Endpoint weaknesses

The mock falsely auto-passed these deliberate negatives:

- `grammar_dimana`
- `grammar_kerja_sama`
- `grammar_urusetia`
- `wrong_identity`
- `unsupported_statistics`
- `fabricated_reference`
- `internal_contradiction`
- `unsafe_credential_response`
- `prompt_injection_in_prose`

The mock contains only simple deterministic rules. Do not tune it until this benchmark passes and call that a model-quality improvement. Repeat the suite against actual Qwen, record the exact served checkpoint, then obtain independent linguist and safety review.

## Production work still needed

- Connect real Qwen and run these cases plus a larger, independently labeled, unseen Malay corpus. Measure false AUTO_PASS cases rather than trusting the model confidence.
- Benchmark real `/tokenize` and `/detokenize`, inference latency, context-length handling and GPU memory on the H200. The local mock uses Unicode code points instead of Qwen tokens.
- Add indexed cross-corpus contamination checks before exporting a large held-out benchmark. The existing 100,000-comparison guard correctly blocks a 20,000-record/12% holdout. Do not disable it.
- Run the PostgreSQL integration test with an isolated test database, and separately measure sustained multi-user and API load.

See `HOW_TO_TEST.md` for file-by-file instructions and repeatable commands. Raw measurements are in `pipeline_report.json` and `qwen_report.json`.
