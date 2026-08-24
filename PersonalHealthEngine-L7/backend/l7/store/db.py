"""L7 product database: versioned migrations + connection helpers.

The L7 db is L7-owned. It stores product projections only (Today versions, evaluation
runs, model-call cache, conversations, notification decisions, settings, episodes,
context time metadata). Every table carries `user_id` so multi-user rollout never needs a
schema rewrite. Upstream health facts are never copied here — they stay in the sealed
layer databases and are referenced by id/hash.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 4

MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS schema_migrations_l7 (
    version          INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    applied_at_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS today_versions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                TEXT NOT NULL REFERENCES users(id),
    analysis_date          TEXT NOT NULL,
    l6_daily_reasoning_id  INTEGER,
    bundle_sha256          TEXT NOT NULL,
    product_state          TEXT NOT NULL
        CHECK (product_state IN ('A','B','C','D','E')),
    signature_sha256       TEXT NOT NULL,
    rendered_json          TEXT NOT NULL,
    judgment_updated       INTEGER NOT NULL DEFAULT 0,
    change_note            TEXT,
    trigger                TEXT NOT NULL,
    created_at_utc         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_today_versions_user ON today_versions(user_id, id);

CREATE TABLE IF NOT EXISTS eval_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT NOT NULL REFERENCES users(id),
    started_at_utc     TEXT NOT NULL,
    finished_at_utc    TEXT,
    trigger            TEXT NOT NULL,
    upstream_sig_json  TEXT NOT NULL,
    bundle_sha256      TEXT,
    recompute_reason   TEXT,
    model_calls        INTEGER NOT NULL DEFAULT 0,
    outcome            TEXT NOT NULL,
    today_version_id   INTEGER REFERENCES today_versions(id)
);

CREATE TABLE IF NOT EXISTS model_call_cache (
    request_sha256   TEXT NOT NULL,
    adapter_kind     TEXT NOT NULL CHECK (adapter_kind IN ('REASONING','MEDICAL')),
    model_id         TEXT NOT NULL,
    response_json    TEXT NOT NULL,
    created_at_utc   TEXT NOT NULL,
    PRIMARY KEY (request_sha256, adapter_kind)
);

CREATE TABLE IF NOT EXISTS evidence_change_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL REFERENCES users(id),
    eval_run_id    INTEGER REFERENCES eval_runs(id),
    kind           TEXT NOT NULL,
    detail_json    TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_decisions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT NOT NULL REFERENCES users(id),
    created_at_utc     TEXT NOT NULL,
    decision           TEXT NOT NULL CHECK (decision IN ('SEND','SUPPRESS')),
    reason             TEXT NOT NULL,
    mode               TEXT NOT NULL,
    related_version_id INTEGER REFERENCES today_versions(id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL REFERENCES users(id),
    opened_at_utc   TEXT NOT NULL,
    closed_at_utc   TEXT,
    boundary_reason TEXT,
    status          TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED'))
);

CREATE TABLE IF NOT EXISTS qa_turns (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id   INTEGER NOT NULL REFERENCES conversations(id),
    user_id           TEXT NOT NULL REFERENCES users(id),
    role              TEXT NOT NULL CHECK (role IN ('USER','ASSISTANT')),
    text              TEXT NOT NULL,
    l6_qa_session_id  INTEGER,
    evidence_ref_json TEXT,
    created_at_utc    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_time_meta (
    l6_context_id      INTEGER PRIMARY KEY,
    user_id            TEXT NOT NULL REFERENCES users(id),
    occurred_on        TEXT,
    ongoing            INTEGER NOT NULL DEFAULT 0,
    ended_on           TEXT,
    valid_until        TEXT,
    last_confirmed_at  TEXT,
    extraction_confidence TEXT,
    created_at_utc     TEXT NOT NULL,
    updated_at_utc     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    user_id        TEXT NOT NULL REFERENCES users(id),
    key            TEXT NOT NULL,
    value_json     TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS health_episodes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT NOT NULL REFERENCES users(id),
    episode_key      TEXT NOT NULL,
    start_date       TEXT NOT NULL,
    end_date         TEXT,
    phase            TEXT NOT NULL CHECK (phase IN ('DEVELOPING','RECOVERING','CLOSED')),
    summary          TEXT,
    status           TEXT NOT NULL DEFAULT 'CURRENT' CHECK (status IN ('CURRENT','MERGED','SUPERSEDED')),
    created_at_utc   TEXT NOT NULL,
    updated_at_utc   TEXT NOT NULL,
    UNIQUE (user_id, episode_key)
);

CREATE TABLE IF NOT EXISTS episode_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id    INTEGER NOT NULL REFERENCES health_episodes(id),
    event_date    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    ref_layer     TEXT,
    ref_id        INTEGER,
    detail_json   TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
"""

MIGRATION_002 = """
CREATE TABLE IF NOT EXISTS qna_audits (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                         TEXT NOT NULL REFERENCES users(id),
    conversation_id                 INTEGER NOT NULL REFERENCES conversations(id),
    user_turn_id                    INTEGER NOT NULL REFERENCES qa_turns(id),
    assistant_turn_id               INTEGER NOT NULL REFERENCES qa_turns(id),
    semantic_classifier_model       TEXT NOT NULL,
    semantic_classification_json    TEXT,
    reasoning_model                 TEXT,
    reasoning_called                INTEGER NOT NULL CHECK (reasoning_called IN (0,1)),
    medical_review_required         INTEGER NOT NULL CHECK (medical_review_required IN (0,1)),
    medical_model                   TEXT,
    medical_review_state            TEXT NOT NULL,
    finalization_path               TEXT NOT NULL,
    stage_events_json               TEXT NOT NULL,
    context_write_state             TEXT,
    created_at_utc                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qna_audits_conversation
    ON qna_audits(user_id, conversation_id, id);
"""

MIGRATION_003 = """
CREATE TABLE IF NOT EXISTS performance_requests (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id                 TEXT NOT NULL UNIQUE,
    method                     TEXT NOT NULL,
    endpoint                   TEXT NOT NULL,
    status_code                INTEGER NOT NULL,
    total_ms                   REAL NOT NULL,
    db_ms                      REAL NOT NULL DEFAULT 0,
    bundle_assembly_ms         REAL NOT NULL DEFAULT 0,
    deepseek_semantic_ms       REAL NOT NULL DEFAULT 0,
    deepseek_reasoning_ms      REAL NOT NULL DEFAULT 0,
    medgemma_load_ms           REAL NOT NULL DEFAULT 0,
    medgemma_prompt_eval_ms    REAL NOT NULL DEFAULT 0,
    medgemma_eval_ms           REAL NOT NULL DEFAULT 0,
    medgemma_total_ms          REAL NOT NULL DEFAULT 0,
    finalizer_ms               REAL NOT NULL DEFAULT 0,
    response_serialization_ms  REAL NOT NULL DEFAULT 0,
    prompt_eval_count          INTEGER,
    eval_count                 INTEGER,
    response_bytes             INTEGER NOT NULL DEFAULT 0,
    error_category             TEXT,
    created_at_utc             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_performance_requests_endpoint_time
    ON performance_requests(endpoint, created_at_utc DESC);
"""

MIGRATION_004 = """
CREATE TABLE IF NOT EXISTS read_projection_versions (
    user_id         TEXT NOT NULL REFERENCES users(id),
    projection      TEXT NOT NULL,
    version         INTEGER NOT NULL,
    metadata_json   TEXT NOT NULL,
    updated_at_utc  TEXT NOT NULL,
    PRIMARY KEY (user_id, projection)
);
CREATE INDEX IF NOT EXISTS idx_health_episodes_user_status_id
    ON health_episodes(user_id, status, id DESC);
CREATE INDEX IF NOT EXISTS idx_episode_events_episode_id_id
    ON episode_events(episode_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_qa_turns_conversation_id_id
    ON qa_turns(conversation_id, id DESC);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_l7(path: str) -> sqlite3.Connection:
    """Open the L7 product db and ensure migrations + seed rows exist."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the FastAPI server serves requests from worker threads while
    # the connection is created at startup. Access is serialized by the single-process
    # design (one orchestrator, BEGIN IMMEDIATE transactions for writes).
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    _migrate(con)
    return con


def _migrate(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations_l7 ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at_utc TEXT NOT NULL)"
    )
    applied = {row[0] for row in con.execute("SELECT version FROM schema_migrations_l7")}
    if 1 not in applied:
        con.executescript(MIGRATION_001)
        con.execute(
            "INSERT INTO schema_migrations_l7 (version, name, applied_at_utc) VALUES (1, 'foundation', ?)",
            (utc_now(),),
        )
    if 2 not in applied:
        con.executescript(MIGRATION_002)
        con.execute(
            "INSERT INTO schema_migrations_l7 (version, name, applied_at_utc) "
            "VALUES (2, 'qna_orchestration_audit', ?)",
            (utc_now(),),
        )
    if 3 not in applied:
        con.executescript(MIGRATION_003)
        con.execute(
            "INSERT INTO schema_migrations_l7 (version, name, applied_at_utc) "
            "VALUES (3, 'performance_telemetry', ?)",
            (utc_now(),),
        )
    if 4 not in applied:
        con.executescript(MIGRATION_004)
        con.execute(
            "INSERT INTO schema_migrations_l7 (version, name, applied_at_utc) "
            "VALUES (4, 'bounded_read_indexes', ?)",
            (utc_now(),),
        )
    con.execute("INSERT OR IGNORE INTO users (id, created_at_utc) VALUES ('owner', ?)", (utc_now(),))
    con.commit()


def open_readonly(
    path: str, *, immutable_if_checkpointed: bool = False
) -> sqlite3.Connection:
    """Open a sealed upstream database strictly read-only."""
    resolved = Path(path).resolve()
    wal_path = Path(f"{resolved}-wal")
    pending_wal = wal_path.exists() and wal_path.stat().st_size > 0
    immutable = immutable_if_checkpointed and not pending_wal
    query = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    uri = resolved.as_uri() + query
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con
