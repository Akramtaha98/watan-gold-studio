"""WaRiSAN Silver-layer helpers (pure functions, no Streamlit, no network).

Bronze entry -> Silver rule cleaning -> MinHash/LSH near-duplicates -> Agent 1
output guard. Orchestration and the Qwen calls live in watan_gold_studio.py.
Raw bytes are never modified: every function here returns new values.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

PLACEHOLDER_RE = re.compile(r"<(?:IC|PHONE|EMAIL)>")

DEFAULT_SILVER_YAML = """\
# WaRiSAN Silver cleaning rules. Versioned; raw files are never modified.
version: 1
unicode_form: NFC            # NFC | NFKC | none
strip_control_chars: true    # C0/C1 controls, zero-width space, BOM, soft hyphen
normalize_whitespace: true   # CRLF -> LF, runs of spaces/tabs/nbsp -> one space
unwrap_line_breaks: true     # join hard-wrapped lines inside a paragraph
collapse_blank_lines: true   # 3+ newlines -> one blank line
segment_paragraphs: true     # re-emit paragraphs separated by one blank line
"""

_RULE_TYPES = {"version": int, "unicode_form": str, "strip_control_chars": bool, "normalize_whitespace": bool,
               "unwrap_line_breaks": bool, "collapse_blank_lines": bool, "segment_paragraphs": bool}

_CONTROL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​﻿­]")
_SPACE_RE = re.compile("[ \t  -  　]+")
_HYPHEN_WRAP_RE = re.compile(r"(?<=\w-)[ ]*\n(?=[a-zà-ɏ])")
_UNWRAP_RE = re.compile(r"(?<=[^\s.!?:;\"')\]…])[ ]*\n(?=[a-zà-ɏ])")


def sha256_hex(data):
    return hashlib.sha256(data if isinstance(data, bytes) else str(data).encode("utf-8")).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- Bronze

def bronze_entry(name, data, ingested_at=None):
    """Immutable descriptor of one raw file. The bytes themselves are not kept."""
    return {"filename": Path(str(name)).name, "sha256": sha256_hex(data), "bytes": len(data), "ingested_at": ingested_at or utc_now()}


# ---------------------------------------------------------------- Silver rules

def parse_rules(text):
    """Parse and validate Silver YAML rules; unknown keys or wrong types are errors."""
    try:
        rules = yaml.safe_load(text or DEFAULT_SILVER_YAML)
    except yaml.YAMLError as exc:
        raise ValueError("Silver rules are not valid YAML.") from exc
    if not isinstance(rules, dict):
        raise ValueError("Silver rules must be a YAML mapping.")
    unknown = sorted(set(rules) - set(_RULE_TYPES))
    if unknown:
        raise ValueError("Unknown Silver rule keys: " + ", ".join(map(str, unknown)))
    merged = yaml.safe_load(DEFAULT_SILVER_YAML)
    merged.update(rules)
    for key, kind in _RULE_TYPES.items():
        if not isinstance(merged[key], kind) or (kind is int and isinstance(merged[key], bool)):
            raise ValueError(f"Silver rule '{key}' must be {kind.__name__}.")
    if merged["unicode_form"] not in {"NFC", "NFKC", "none"}:
        raise ValueError("unicode_form must be NFC, NFKC or none.")
    return merged


def rules_sha256(rules):
    return sha256_hex(json.dumps(rules, sort_keys=True, separators=(",", ":")))


def segment_paragraphs(text):
    """Structural paragraph segmentation on blank lines."""
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def silver_clean(text, rules):
    """Return (cleaned_text, applied_rule_names). PII masks are plain text and survive."""
    out, applied = text or "", []
    def step(name, new):
        nonlocal out
        if new != out:
            applied.append(name)
            out = new
    if rules["unicode_form"] != "none":
        step("unicode_" + rules["unicode_form"], unicodedata.normalize(rules["unicode_form"], out))
    if rules["strip_control_chars"]:
        step("strip_control_chars", _CONTROL_RE.sub("", out))
    if rules["normalize_whitespace"]:
        norm = _SPACE_RE.sub(" ", out.replace("\r\n", "\n").replace("\r", "\n"))
        step("normalize_whitespace", "\n".join(line.strip(" ") for line in norm.split("\n")))
    if rules["unwrap_line_breaks"]:
        step("unwrap_line_breaks", "\n\n".join(_UNWRAP_RE.sub(" ", _HYPHEN_WRAP_RE.sub("", p)) for p in re.split(r"\n\s*\n", out)))
    if rules["collapse_blank_lines"]:
        step("collapse_blank_lines", re.sub(r"\n{3,}", "\n\n", out).strip())
    if rules["segment_paragraphs"]:
        paragraphs = segment_paragraphs(out)
        step("segment_paragraphs", "\n\n".join(paragraphs) if paragraphs else out)
    return out, applied


def is_messy(text, source_name=""):
    """Cheap trigger for Agent 1: only noisy documents are worth a model call."""
    ext = Path(str(source_name)).suffix.lower()
    return bool(re.search(r"(?<!\n)\n(?!\n)", text or "") or re.search(r"\w-\n\w", text or "")
                or re.search(r"(?m)^\s*(?:page\s*)?\d{1,4}\s*$", text or "") or (ext in {".pdf", ".docx"} and len(text or "") > 400))


# ---------------------------------------------------------------- MinHash / LSH

NUM_PERM = 128
_PRIME = np.uint64(2147483647)  # 2**31 - 1; (a*h + b) < 2**63 fits in uint64
_rng = np.random.RandomState(20260116)
_A = _rng.randint(1, 2147483646, size=NUM_PERM).astype(np.uint64)
_B = _rng.randint(0, 2147483646, size=NUM_PERM).astype(np.uint64)


def word_tokens(text):
    return re.findall(r"\w+", unicodedata.normalize("NFC", text or "").casefold())


def shingles(text, k=5):
    tokens = word_tokens(text)
    if not tokens:
        return set()
    if len(tokens) < k:
        return {" ".join(tokens)}
    return {" ".join(tokens[i:i + k]) for i in range(len(tokens) - k + 1)}


def minhash_signature(shingle_set):
    if not shingle_set:
        return None
    hashes = np.fromiter((zlib.crc32(s.encode("utf-8")) for s in shingle_set), dtype=np.uint64, count=len(shingle_set))
    return ((hashes[None, :] * _A[:, None] + _B[:, None]) % _PRIME).min(axis=1)


def estimated_jaccard(sig_a, sig_b):
    return float(np.mean(sig_a == sig_b))


def lsh_params(threshold):
    """Pick (bands, rows) whose S-curve midpoint is the largest one <= threshold."""
    best = (128, 1)
    for bands, rows in ((128, 1), (64, 2), (32, 4), (16, 8), (8, 16), (4, 32)):
        if (1.0 / bands) ** (1.0 / rows) <= threshold:
            best = (bands, rows)
    return best


def find_near_duplicates(texts, threshold=0.85, k=5):
    """Group near-identical texts. texts = [(key, text)], returns a list of groups.

    Each group is {"group_id", "representative", "members": [{"key", "jaccard"}]}
    with the first key (input order) as representative. Exact-hash duplicates
    (jaccard 1.0) are included so callers can show both kinds together.
    """
    if not 0.0 < threshold <= 1.0:
        raise ValueError("MinHash threshold must be in (0, 1].")
    bands, rows = lsh_params(threshold)
    keys, signatures = [], []
    for key, text in texts:
        sig = minhash_signature(shingles(text, k))
        if sig is not None:
            keys.append(key)
            signatures.append(sig)
    buckets, candidates = {}, set()
    for index, sig in enumerate(signatures):
        for band in range(bands):
            bucket = buckets.setdefault((band, sig[band * rows:(band + 1) * rows].tobytes()), [])
            candidates.update((other, index) for other in bucket)
            bucket.append(index)
    parent = list(range(len(keys)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in sorted(candidates):
        if estimated_jaccard(signatures[a], signatures[b]) >= threshold:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    members = {}
    for index in range(len(keys)):
        members.setdefault(find(index), []).append(index)
    groups = []
    for root, indexes in sorted(members.items()):
        if len(indexes) < 2:
            continue
        rep = indexes[0]
        out = []
        for index in indexes[1:]:
            out.append({"key": keys[index], "jaccard": round(estimated_jaccard(signatures[rep], signatures[index]), 4)})
        groups.append({"group_id": "ND-" + sha256_hex(" ".join(sorted(word_tokens(texts_by_key(texts, keys[rep])))))[:16],
                       "representative": keys[rep], "members": out})
    return groups


def texts_by_key(texts, key):
    return next(text for k, text in texts if k == key)


# ---------------------------------------------------------------- Agent 1 output guard

def _novel_tokens(source_tokens, produced_tokens):
    known = set(source_tokens)
    joined = {a + b for a, b in zip(source_tokens, source_tokens[1:])}
    return [t for t in produced_tokens if t not in known and t not in joined]


def validate_agent1_output(original_text, paragraphs, qa_pairs=()):
    """Return (ok, reason, paragraphs, qa_pairs). Agent 1 may only re-flow text.

    Rejected when it changes PII masks, adds words that are not in the source
    (other than words formed by rejoining a broken line), or drops >15% of it.
    Q&A pairs failing the same no-new-words test are dropped individually.
    """
    cleaned = [p.strip() for p in paragraphs if isinstance(p, str) and p.strip()]
    if not cleaned:
        return False, "Agent 1 returned no paragraphs.", [], []
    produced = "\n\n".join(cleaned)
    if Counter(PLACEHOLDER_RE.findall(original_text)) != Counter(PLACEHOLDER_RE.findall(produced)):
        return False, "Agent 1 changed PII placeholders.", [], []
    source_tokens, produced_tokens = word_tokens(original_text), word_tokens(produced)
    if len(_novel_tokens(source_tokens, produced_tokens)) > int(0.01 * len(produced_tokens)):
        return False, "Agent 1 added words that are not in the source.", [], []
    compact = lambda t: re.sub(r"\W+", "", unicodedata.normalize("NFC", t).casefold())
    if compact(original_text) and len(compact(produced)) / len(compact(original_text)) < 0.85:
        return False, "Agent 1 dropped too much of the source text.", [], []
    good_pairs = []
    for pair in qa_pairs:
        if not isinstance(pair, dict):
            continue
        q, a = str(pair.get("question", "")).strip(), str(pair.get("answer", "")).strip()
        if q and a and not _novel_tokens(source_tokens, word_tokens(q + " " + a)):
            good_pairs.append({"question": q, "answer": a})
    return True, "", cleaned, good_pairs


# ---------------------------------------------------------------- helpers

def parallel_map(fn, items, workers=1, on_progress=None, stop=None):
    """Run fn over items concurrently; results are returned in input order.

    on_progress(done, total) runs on the calling thread only. If `stop` (a threading.Event) is set,
    items that have not started are skipped and their result stays None; running items finish.
    """
    items = list(items)
    results = [None] * len(items)
    workers = max(1, min(int(workers or 1), len(items) or 1))
    if workers == 1:
        for i, item in enumerate(items):
            if stop is not None and stop.is_set():
                break
            results[i] = fn(item)
            if on_progress:
                on_progress(i + 1, len(items))
        return results
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
        done = 0
        for future in as_completed(futures):
            if future.cancelled():
                continue
            results[futures[future]] = future.result()
            done += 1
            if on_progress:
                on_progress(done, len(items))
            if stop is not None and stop.is_set():
                for pending in futures:
                    pending.cancel()
    return results
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
        for done, future in enumerate(as_completed(futures), 1):
            results[futures[future]] = future.result()
            if on_progress:
                on_progress(done, len(items))
    return results
