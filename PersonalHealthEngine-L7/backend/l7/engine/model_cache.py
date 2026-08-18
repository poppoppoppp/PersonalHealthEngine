"""Provenance-aware model-call cache.

Same input -> same result, without paying again. Keys are SHA-256 hashes of the canonical
request payload (exactly the hashes the sealed L6 materializer computes for
`model_invocations`), so a repeated evaluation over an unchanged Evidence Bundle reuses the
stored model output instead of calling DeepSeek/MedGemma again.
"""

from __future__ import annotations

import json
import sqlite3

from l7.store.db import utc_now


def lookup(l7: sqlite3.Connection, request_sha256: str, adapter_kind: str) -> dict | None:
    row = l7.execute(
        "SELECT response_json FROM model_call_cache WHERE request_sha256=? AND adapter_kind=?",
        (request_sha256, adapter_kind),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["response_json"])
    except json.JSONDecodeError:
        return None


def store(
    l7: sqlite3.Connection,
    request_sha256: str,
    adapter_kind: str,
    model_id: str,
    response: dict,
) -> None:
    l7.execute(
        "INSERT OR REPLACE INTO model_call_cache "
        "(request_sha256, adapter_kind, model_id, response_json, created_at_utc) VALUES (?,?,?,?,?)",
        (request_sha256, adapter_kind, model_id, json.dumps(response, ensure_ascii=False), utc_now()),
    )
