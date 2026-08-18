from pathlib import Path
import sqlite3
import hashlib
from datetime import datetime, timezone

ROOT = Path(r"D:\PersonalHealthEngine-L2")
DB_PATH = ROOT / "db" / "personal_health_raw.sqlite3"

SCHEMA_VERSION = 1
SCHEMA_NAME = "L2 Raw Health Store v0.1"

SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id TEXT PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,

    window_start TEXT,
    window_end TEXT,

    status TEXT NOT NULL
        CHECK(status IN ('RUNNING','SUCCESS','PARTIAL','FAILED')),

    collector_version TEXT,
    source_root TEXT NOT NULL,

    artifacts_seen INTEGER NOT NULL DEFAULT 0,
    records_seen INTEGER NOT NULL DEFAULT 0,
    logical_new INTEGER NOT NULL DEFAULT 0,
    versions_new INTEGER NOT NULL DEFAULT 0,
    observations_new INTEGER NOT NULL DEFAULT 0,
    revisions INTEGER NOT NULL DEFAULT 0,
    late_arrivals INTEGER NOT NULL DEFAULT 0,
    reobservations INTEGER NOT NULL DEFAULT 0,
    issues INTEGER NOT NULL DEFAULT 0,

    error_summary TEXT
);

CREATE TABLE IF NOT EXISTS captures (
    capture_id TEXT PRIMARY KEY,

    provider TEXT NOT NULL,
    region TEXT NOT NULL,

    collector TEXT,
    collector_version TEXT,
    schema_version TEXT,

    start_date TEXT,
    end_date TEXT,
    range_mode TEXT,
    overlap_days INTEGER,

    capture_started_at_utc TEXT,
    capture_finished_at_utc TEXT,

    credentials_source TEXT,
    credentials_embedded INTEGER,
    runtime_dependency TEXT,

    source_dir TEXT NOT NULL,

    manifest_sha256 TEXT NOT NULL,
    manifest_raw_json TEXT NOT NULL,

    first_imported_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    capture_id TEXT NOT NULL
        REFERENCES captures(capture_id),

    dataset TEXT,
    endpoint TEXT,

    relative_path TEXT NOT NULL,
    source_path TEXT NOT NULL,
    archived_path TEXT,

    file_sha256 TEXT NOT NULL,
    file_size INTEGER NOT NULL,

    manifest_output_sha256 TEXT,

    first_imported_at_utc TEXT NOT NULL,
    last_verified_at_utc TEXT NOT NULL,

    UNIQUE(capture_id, relative_path)
);

CREATE TABLE IF NOT EXISTS ingestion_run_artifacts (
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id),

    source_artifact_id INTEGER NOT NULL
        REFERENCES source_artifacts(id),

    status TEXT NOT NULL
        CHECK(status IN ('SUCCESS','PARTIAL','FAILED','SKIPPED')),

    records_seen INTEGER NOT NULL DEFAULT 0,
    issues INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY(run_id, source_artifact_id)
);

CREATE TABLE IF NOT EXISTS logical_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    provider TEXT NOT NULL,
    region TEXT NOT NULL,
    dataset TEXT NOT NULL,

    raw_key TEXT NOT NULL,
    raw_sid TEXT NOT NULL,
    raw_time INTEGER NOT NULL,

    identity_version TEXT NOT NULL DEFAULT 'xiaomi-v0.1',
    logical_key TEXT NOT NULL UNIQUE,

    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,

    created_by_run_id TEXT
        REFERENCES ingestion_runs(run_id),

    UNIQUE(
        provider,
        region,
        dataset,
        raw_key,
        raw_sid,
        raw_time
    )
);

CREATE TABLE IF NOT EXISTS raw_record_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    logical_record_id INTEGER NOT NULL
        REFERENCES logical_records(id),

    payload_sha256 TEXT NOT NULL,

    raw_json TEXT NOT NULL,

    raw_update_time INTEGER,
    zone_name TEXT,
    zone_offset INTEGER,

    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,

    first_seen_run_id TEXT
        REFERENCES ingestion_runs(run_id),

    UNIQUE(logical_record_id, payload_sha256)
);

CREATE TABLE IF NOT EXISTS raw_record_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    source_artifact_id INTEGER NOT NULL
        REFERENCES source_artifacts(id),

    source_record_ordinal INTEGER NOT NULL,

    raw_record_version_id INTEGER NOT NULL
        REFERENCES raw_record_versions(id),

    ingestion_run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id),

    capture_id TEXT NOT NULL
        REFERENCES captures(capture_id),

    ingested_at_utc TEXT NOT NULL,

    classification TEXT NOT NULL
        CHECK(classification IN (
            'NEW',
            'REVISION',
            'REOBSERVATION'
        )),

    late_arrival INTEGER NOT NULL DEFAULT 0
        CHECK(late_arrival IN (0,1)),

    l1_identity_hint TEXT,

    envelope_schema_version TEXT,
    endpoint TEXT,
    dataset TEXT NOT NULL,
    provider TEXT NOT NULL,
    region TEXT NOT NULL,

    UNIQUE(source_artifact_id, source_record_ordinal)
);

CREATE TABLE IF NOT EXISTS ingestion_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id),

    source_artifact_id INTEGER
        REFERENCES source_artifacts(id),

    source_record_ordinal INTEGER,

    issue_code TEXT NOT NULL,

    severity TEXT NOT NULL
        CHECK(severity IN ('WARNING','ERROR')),

    message TEXT NOT NULL,

    raw_line TEXT,

    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_logical_dataset_time
ON logical_records(dataset, raw_time);

CREATE INDEX IF NOT EXISTS idx_logical_sid
ON logical_records(raw_sid);

CREATE INDEX IF NOT EXISTS idx_versions_logical
ON raw_record_versions(logical_record_id);

CREATE INDEX IF NOT EXISTS idx_observations_version
ON raw_record_observations(raw_record_version_id);

CREATE INDEX IF NOT EXISTS idx_observations_capture
ON raw_record_observations(capture_id);

CREATE INDEX IF NOT EXISTS idx_observations_run
ON raw_record_observations(ingestion_run_id);

CREATE INDEX IF NOT EXISTS idx_issues_run
ON ingestion_issues(run_id);
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    schema_checksum = hashlib.sha256(
        SCHEMA_SQL.encode("utf-8")
    ).hexdigest()

    conn = sqlite3.connect(DB_PATH)

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = FULL")

        journal_mode = conn.execute(
            "PRAGMA journal_mode = WAL"
        ).fetchone()[0]

        conn.executescript(SCHEMA_SQL)

        existing = conn.execute(
            """
            SELECT checksum
            FROM schema_migrations
            WHERE version = ?
            """,
            (SCHEMA_VERSION,)
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO schema_migrations
                (version, name, checksum, applied_at_utc)
                VALUES (?, ?, ?, ?)
                """,
                (
                    SCHEMA_VERSION,
                    SCHEMA_NAME,
                    schema_checksum,
                    utc_now(),
                )
            )
        elif existing[0] != schema_checksum:
            raise RuntimeError(
                "Schema v1 exists but checksum differs."
            )

        conn.commit()

        integrity = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        fk_errors = conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        tables = [
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]

        expected = {
            "schema_migrations",
            "ingestion_runs",
            "captures",
            "source_artifacts",
            "ingestion_run_artifacts",
            "logical_records",
            "raw_record_versions",
            "raw_record_observations",
            "ingestion_issues",
        }

        print("========== L2 DATABASE INIT ==========")
        print(f"DB = {DB_PATH}")
        print(f"journal_mode = {journal_mode}")
        print(f"integrity_check = {integrity}")
        print(f"foreign_key_errors = {len(fk_errors)}")
        print(f"schema_version = {SCHEMA_VERSION}")
        print(f"schema_checksum = {schema_checksum}")
        print()
        print("TABLES:")

        for table in tables:
            print(f"  {table}")

        print()

        if (
            integrity == "ok"
            and len(fk_errors) == 0
            and expected.issubset(set(tables))
        ):
            print("RESULT = PASS")
            print("L2 SCHEMA v0.1 = READY")
        else:
            print("RESULT = FAIL")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
