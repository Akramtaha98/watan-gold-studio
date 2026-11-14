# External PTSL portal integration

Run `python ptsl_api.py`; its default local URL is `http://127.0.0.1:8001`. The service and Streamlit must share `DATABASE_URL`. Export `PTSL_API_TOKEN` with a random ≥32-character secret before startup. Keep this token on the portal's **server**, not in publicly delivered JavaScript. Authentication failures return 401; missing configuration fails startup.

## Fetch tasks

```bash
curl -H "Authorization: Bearer $PTSL_API_TOKEN" \
  'http://127.0.0.1:8001/api/ptsl/queue'
```

Filter queue/status by `?batch_id=<batch UUID>` when reviewing a particular batch. Queue returns `items` and `status`. Every item contains:

```json
{
  "record_id": "<batch UUID>:R000001",
  "batch_id": "<batch UUID>",
  "source_record_id": "R000001",
  "expected_revision": 1,
  "kind": "dialogue",
  "masked_text": "[{\"role\":\"user\",\"content\":\"...\"}, ...]",
  "qwen_suggestion": "[{\"role\":\"user\",\"content\":\"...\"}, ...]",
  "reason": "Short masked verification reason",
  "stage": "GOLD_3",
  "metadata": {"source_id":"example-source"}
}
```

These are illustrative IDs/values: submit the actual opaque record ID and numeric revision returned by the queue. Text fields are strictly masked, including reserved synthetic fixtures. Technical credentials, full verifier/policy payloads and raw uploaded files are never returned. The queue is for a trusted portal service; each database is a shared curation workspace, not tenant-isolated storage.

## Submit verdicts atomically

`POST /api/ptsl/submit` accepts one verdict object or an array of 1–100 verdicts. Use `Content-Type: application/json` and the bearer header.

```json
{
  "record_id": "<actual batch UUID>:R000001",
  "expected_revision": 1,
  "verdict": "Good",
  "reviewer_role": "Linguist",
  "edited_text": null,
  "reviewer_notes": "Semakan selesai."
}
```

- Good accepts the existing Qwen proposal; Bad quarantines; Unsure remains queued for secondary review.
- For Good, optional `edited_text` may be a prose string, a final-assistant-answer string, or a complete dialogue messages array. All existing validators apply: no altered user/system turns, role/turn counts, changed PII placeholders or unmasked personal contacts. Do not copy a masked dummy fixture over protected source turns; an assistant-only edit is often simplest.
- The service token defaults to Linguist scope. `PTSL_API_REVIEWER_ROLES=Linguist,Project_Lead` explicitly grants those service roles. Project_Lead is still required for mutable project facts; selecting its role in the request cannot bypass token scope.
- Every item in a batch is validated before commit. Any invalid item rolls back the whole request. Repeated IDs within one submission are rejected. The request body limit is 2 MiB.
- A stale or replayed revision returns **409**. Refresh tasks and resubmit the intended decision with its current revision. Do not blindly retry an old Good/Bad; it may already be committed. Unsure increments the revision too.
- **403** means token role scope denied; **422** means structure/role-policy/PII rejection; **404** means unknown task. Validation errors are redacted and do not echo submitted private values.
- Accepted decisions persist reviewer_role, verdict, channel PTSL_API, masked notes and UTC timestamp. Gold governance audits use roles and note hashes, not personal staff usernames.

## Live progress

`GET /api/ptsl/status?batch_id=<id>` returns `total_queued`, `completed`, `remaining`, `ready_for_release` and the batch ID. A ready status requires eligible records, no pending/error/stale verification and zero review items; final export validation can still block a release for permissions/schema/leakage issues.

The Streamlit Step 2 sync card polls the DB every five seconds when enabled. The manual **Sync / Refresh Status** button works without polling. Once the global batch queue is empty, the full wizard advances to Step 3; reviewer-only mode shows its final-release handoff. Local reviewers cannot overwrite a concurrently accepted portal decision: they reload on revision conflict. Excel packs remain an offline fallback, with persisted snapshots and atomic/replay-safe import.

## Optional outbound webhooks

Set `PTSL_WEBHOOK_URL=https://your-portal.example/backend/warisan-webhook` and a separate ≥32-character `PTSL_WEBHOOK_SECRET`. The API worker sends durable outbox events such as `batch.created`, `batch.updated`, `ptsl.reviewed`, `release.created`.

Event bodies contain stable IDs, timestamps and aggregate progress; corpus text, notes and credentials are not included. The worker does not follow redirects and verifies HTTPS certificates. Failed attempts retry with persisted exponential backoff; after eight attempts they remain marked failed for operator inspection. A process crash during sending is recovered after the 60-second lease. Delivery is **at least once**, not exactly once.

Headers:

```text
X-WaRiSAN-Event-ID: <stable event UUID>
X-WaRiSAN-Signature: t=<Unix timestamp>,v1=<HMAC hex>
```

Receiver verification (use the exact raw request bytes, before parsing JSON):

```python
import hashlib, hmac, time

def verify_signature(header, body, secret):
    try:
        values = dict(part.split("=", 1) for part in header.split(","))
        timestamp = values["t"]
        if abs(time.time() - int(timestamp)) > 300:
            return False
        expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body,
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, values["v1"])
    except (KeyError, ValueError):
        return False
```

The portal must durably deduplicate event IDs, commit its update, then acknowledge 2xx. A webhook is a notification to refresh API status; it does not grant access or override database decisions. Configure a private/HTTPS route, rate limiting and monitored logs at your proxy. CORS defaults off; only set explicit `PTSL_ALLOWED_ORIGINS` for trusted browser use and never use `*`.
