from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(r"D:\PersonalHealthEngine-L2")

PROD_DB = ROOT / "db" / "personal_health_raw.sqlite3"
SOURCE_ARCHIVE = ROOT / "archive"

INIT_SCRIPT = ROOT / "scripts" / "init_l2_db.py"
IMPORT_SCRIPT = ROOT / "scripts" / "import_l1_to_l2.py"

stamp = datetime.now().strftime("%Y%m%dT%H%M%S")

TEST_ROOT = ROOT / "rebuild_test" / stamp
REBUILD_DB = TEST_ROOT / "db" / "personal_health_raw.sqlite3"
REBUILD_ARCHIVE = TEST_ROOT / "archive"
REBUILD_BACKUPS = TEST_ROOT / "backups"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def sha256_rows(conn, sql):
    h = hashlib.sha256()

    rows = conn.execute(sql)

    count = 0

    for row in rows:
        count += 1

        for value in row:
            if value is None:
                value = "<NULL>"

            h.update(
                str(value).encode("utf-8")
            )

            h.update(b"\x1f")

        h.update(b"\x1e")

    return count, h.hexdigest()


def database_semantics(db_path):
    conn = sqlite3.connect(db_path)

    try:
        conn.execute("PRAGMA foreign_keys = ON")

        integrity = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        fk_errors = conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        checks = {}

        checks["captures"] = sha256_rows(
            conn,
            """
            SELECT
                capture_id,
                provider,
                region,
                collector,
                collector_version,
                schema_version,
                start_date,
                end_date,
                range_mode,
                overlap_days,
                capture_started_at_utc,
                capture_finished_at_utc,
                manifest_sha256
            FROM captures
            ORDER BY capture_id
            """
        )

        checks["source_artifacts"] = sha256_rows(
            conn,
            """
            SELECT
                capture_id,
                dataset,
                endpoint,
                relative_path,
                file_sha256,
                file_size,
                manifest_output_sha256
            FROM source_artifacts
            ORDER BY
                capture_id,
                relative_path
            """
        )

        checks["logical_records"] = sha256_rows(
            conn,
            """
            SELECT
                provider,
                region,
                dataset,
                raw_key,
                raw_sid,
                raw_time,
                identity_version,
                logical_key
            FROM logical_records
            ORDER BY logical_key
            """
        )

        checks["raw_record_versions"] = sha256_rows(
            conn,
            """
            SELECT
                lr.logical_key,
                rv.payload_sha256,
                rv.raw_json,
                rv.raw_update_time,
                rv.zone_name,
                rv.zone_offset
            FROM raw_record_versions rv
            JOIN logical_records lr
              ON lr.id = rv.logical_record_id
            ORDER BY
                lr.logical_key,
                rv.payload_sha256
            """
        )

        checks["raw_record_observations"] = sha256_rows(
            conn,
            """
            SELECT
                o.capture_id,
                sa.relative_path,
                o.source_record_ordinal,
                lr.logical_key,
                rv.payload_sha256,
                o.classification,
                o.late_arrival,
                o.l1_identity_hint,
                o.envelope_schema_version,
                o.endpoint,
                o.dataset,
                o.provider,
                o.region
            FROM raw_record_observations o

            JOIN source_artifacts sa
              ON sa.id = o.source_artifact_id

            JOIN raw_record_versions rv
              ON rv.id = o.raw_record_version_id

            JOIN logical_records lr
              ON lr.id = rv.logical_record_id

            ORDER BY
                o.capture_id,
                sa.relative_path,
                o.source_record_ordinal
            """
        )

        return {
            "integrity": integrity,
            "fk_errors": len(fk_errors),
            "checks": checks,
        }

    finally:
        conn.close()


def main():

    print(
        "========== L2 ARCHIVE REBUILD TEST =========="
    )

    print(f"SOURCE ARCHIVE = {SOURCE_ARCHIVE}")
    print(f"PRODUCTION DB  = {PROD_DB}")
    print(f"REBUILD DB     = {REBUILD_DB}")

    print()

    if not PROD_DB.exists():
        raise RuntimeError(
            "Production L2 database not found."
        )

    if not SOURCE_ARCHIVE.exists():
        raise RuntimeError(
            "L2 archive not found."
        )

    TEST_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    REBUILD_DB.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ----------------------------------
    # CREATE A BRAND-NEW EMPTY DATABASE
    # ----------------------------------

    init_mod = load_module(
        "l2_init_rebuild",
        INIT_SCRIPT,
    )

    schema_sql = init_mod.SCHEMA_SQL

    conn = sqlite3.connect(REBUILD_DB)

    try:
        conn.execute(
            "PRAGMA foreign_keys = ON"
        )

        conn.execute(
            "PRAGMA synchronous = FULL"
        )

        conn.execute(
            "PRAGMA journal_mode = WAL"
        )

        conn.executescript(schema_sql)

        schema_checksum = hashlib.sha256(
            schema_sql.encode("utf-8")
        ).hexdigest()

        conn.execute(
            """
            INSERT INTO schema_migrations (
                version,
                name,
                checksum,
                applied_at_utc
            )
            VALUES (
                1,
                'L2 Raw Health Store v0.1',
                ?,
                datetime('now')
            )
            """,
            (schema_checksum,),
        )

        conn.commit()

    finally:
        conn.close()

    # ----------------------------------
    # RUN IMPORTER AGAINST L2 ARCHIVE
    # NOT AGAINST L1
    # ----------------------------------

    importer = load_module(
        "l2_archive_importer",
        IMPORT_SCRIPT,
    )

    importer.CAPTURES_ROOT = SOURCE_ARCHIVE
    importer.DB_PATH = REBUILD_DB
    importer.ARCHIVE_ROOT = REBUILD_ARCHIVE
    importer.BACKUP_ROOT = REBUILD_BACKUPS

    print(
        "========== REBUILD IMPORT =========="
    )

    importer.main()

    print()
    print(
        "========== SEMANTIC COMPARISON =========="
    )

    prod = database_semantics(PROD_DB)
    rebuilt = database_semantics(REBUILD_DB)

    all_match = True

    for name in (
        "captures",
        "source_artifacts",
        "logical_records",
        "raw_record_versions",
        "raw_record_observations",
    ):

        prod_count, prod_hash = (
            prod["checks"][name]
        )

        rebuild_count, rebuild_hash = (
            rebuilt["checks"][name]
        )

        match = (
            prod_count == rebuild_count
            and prod_hash == rebuild_hash
        )

        if not match:
            all_match = False

        print(
            f"{name:26s} "
            f"prod={prod_count:<8} "
            f"rebuilt={rebuild_count:<8} "
            f"match={match}"
        )

        if not match:
            print(
                f"  prod_hash    = {prod_hash}"
            )

            print(
                f"  rebuild_hash = {rebuild_hash}"
            )

    print()

    print(
        f"production_integrity = "
        f"{prod['integrity']}"
    )

    print(
        f"rebuild_integrity    = "
        f"{rebuilt['integrity']}"
    )

    print(
        f"production_fk_errors = "
        f"{prod['fk_errors']}"
    )

    print(
        f"rebuild_fk_errors    = "
        f"{rebuilt['fk_errors']}"
    )

    print()

    if (
        all_match
        and prod["integrity"] == "ok"
        and rebuilt["integrity"] == "ok"
        and prod["fk_errors"] == 0
        and rebuilt["fk_errors"] == 0
    ):
        print("RESULT = PASS")
        print(
            "L2 FULL REBUILD FROM ARCHIVE = PASS"
        )
    else:
        print("RESULT = FAIL")


if __name__ == "__main__":
    main()
