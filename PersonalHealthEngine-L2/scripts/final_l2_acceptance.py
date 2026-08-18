from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(r"D:\PersonalHealthEngine-L2")

DB = ROOT / "db" / "personal_health_raw.sqlite3"
ARCHIVE = ROOT / "archive"
BACKUPS = ROOT / "backups"
RESTORE_TEST = ROOT / "restore_test"
REBUILD_TEST = ROOT / "rebuild_test"
MIGRATION_TEST = ROOT / "migration_test"

SEAL_FILE = ROOT / "L2_SEAL.md"
AUDIT_JSON = ROOT / "L2_FINAL_AUDIT.json"

EXPECTED_TABLES = {
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

EXPECTED_COUNTS = {
    "captures": 9,
    "source_artifacts": 81,
    "logical_records": 5030,
    "raw_record_versions": 5297,
    "raw_record_observations": 15751,
}

EXPECTED_REVISIONS = {
    "calories": 67,
    "heart_rate": 155,
    "spo2": 3,
    "steps": 40,
    "stress": 2,
}


def now_utc():
    return datetime.now(
        timezone.utc
    ).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_hash(raw_record: dict) -> str:
    return sha256_bytes(
        canonical_json(
            raw_record
        ).encode("utf-8")
    )


def logical_key(
    provider,
    region,
    dataset,
    raw_key,
    raw_sid,
    raw_time,
):
    identity = {
        "provider": str(provider),
        "region": str(region),
        "dataset": str(dataset),
        "key": str(raw_key),
        "sid": str(raw_sid),
        "time": int(raw_time),
    }

    return sha256_bytes(
        canonical_json(
            identity
        ).encode("utf-8")
    )


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    return conn


def db_integrity(conn):
    integrity = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    fk = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    return integrity, len(fk)


def table_count(conn, table):
    return conn.execute(
        f"SELECT COUNT(*) FROM {table}"
    ).fetchone()[0]


def latest_file(pattern):
    files = list(pattern)

    if not files:
        return None

    return max(
        files,
        key=lambda p: p.stat().st_mtime,
    )


def add_check(checks, name, passed, detail):
    checks.append(
        {
            "name": name,
            "pass": bool(passed),
            "detail": str(detail),
        }
    )


def semantic_digest(conn, sql):
    h = hashlib.sha256()
    count = 0

    for row in conn.execute(sql):

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


def raw_semantics(conn):

    return {
        "logical_records": semantic_digest(
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
        ),

        "raw_record_versions": semantic_digest(
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
        ),

        "raw_record_observations": semantic_digest(
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
        ),
    }


def verify_core_database(checks, conn):

    integrity, fk_errors = db_integrity(
        conn
    )

    add_check(
        checks,
        "SQLite integrity",
        integrity == "ok",
        integrity,
    )

    add_check(
        checks,
        "Foreign keys",
        fk_errors == 0,
        f"errors={fk_errors}",
    )

    journal = conn.execute(
        "PRAGMA journal_mode"
    ).fetchone()[0]

    add_check(
        checks,
        "WAL journal mode",
        str(journal).lower() == "wal",
        journal,
    )

    tables = {
        row[0]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
              AND name NOT LIKE 'sqlite_%'
            """
        )
    }

    add_check(
        checks,
        "Layer boundary / table set",
        tables == EXPECTED_TABLES,
        f"tables={sorted(tables)}",
    )

    schema_versions = [
        row[0]
        for row in conn.execute(
            """
            SELECT version
            FROM schema_migrations
            ORDER BY version
            """
        )
    ]

    add_check(
        checks,
        "Production schema version",
        schema_versions == [1],
        f"versions={schema_versions}",
    )

    for table, expected in (
        EXPECTED_COUNTS.items()
    ):
        actual = table_count(
            conn,
            table,
        )

        add_check(
            checks,
            f"Count {table}",
            actual == expected,
            f"{actual} expected={expected}",
        )

    run_count = table_count(
        conn,
        "ingestion_runs",
    )

    success_runs = conn.execute(
        """
        SELECT COUNT(*)
        FROM ingestion_runs
        WHERE status = 'SUCCESS'
        """
    ).fetchone()[0]

    add_check(
        checks,
        "Ingestion run audit",
        run_count == 2
        and success_runs == 2,
        (
            f"runs={run_count}, "
            f"success={success_runs}"
        ),
    )

    issue_count = table_count(
        conn,
        "ingestion_issues",
    )

    add_check(
        checks,
        "Ingestion issues",
        issue_count == 0,
        f"issues={issue_count}",
    )

    embedded = conn.execute(
        """
        SELECT COUNT(*)
        FROM captures
        WHERE credentials_embedded <> 0
        """
    ).fetchone()[0]

    add_check(
        checks,
        "Credential isolation",
        embedded == 0,
        f"credentials_embedded={embedded}",
    )

    missing_sid = conn.execute(
        """
        SELECT COUNT(*)
        FROM logical_records
        WHERE raw_sid IS NULL
           OR raw_sid = ''
        """
    ).fetchone()[0]

    add_check(
        checks,
        "Raw SID preservation",
        missing_sid == 0,
        f"missing_raw_sid={missing_sid}",
    )

    sleep_sources = conn.execute(
        """
        SELECT COUNT(DISTINCT raw_sid)
        FROM logical_records
        WHERE dataset = 'sleep'
        """
    ).fetchone()[0]

    generated_sleep = conn.execute(
        """
        SELECT COUNT(*)
        FROM logical_records
        WHERE dataset = 'sleep'
          AND raw_sid LIKE 'hlth.gen_%'
        """
    ).fetchone()[0]

    wearable_sleep = conn.execute(
        """
        SELECT COUNT(*)
        FROM logical_records
        WHERE dataset = 'sleep'
          AND raw_sid NOT LIKE 'hlth.gen_%'
        """
    ).fetchone()[0]

    add_check(
        checks,
        "Sleep source coexistence",
        sleep_sources >= 2
        and generated_sleep > 0
        and wearable_sleep > 0,
        (
            f"sources={sleep_sources}, "
            f"generated={generated_sleep}, "
            f"wearable={wearable_sleep}"
        ),
    )


def verify_version_semantics(
    checks,
    conn,
):

    logical = table_count(
        conn,
        "logical_records",
    )

    versions = table_count(
        conn,
        "raw_record_versions",
    )

    observations = table_count(
        conn,
        "raw_record_observations",
    )

    classes = {
        row["classification"]: row["n"]
        for row in conn.execute(
            """
            SELECT
                classification,
                COUNT(*) AS n
            FROM raw_record_observations
            GROUP BY classification
            """
        )
    }

    new = classes.get(
        "NEW",
        0,
    )

    revision = classes.get(
        "REVISION",
        0,
    )

    reobs = classes.get(
        "REOBSERVATION",
        0,
    )

    invariant_ok = (
        new == logical
        and revision
            == versions - logical
        and reobs
            == observations - versions
        and new + revision + reobs
            == observations
    )

    add_check(
        checks,
        "Version classification invariant",
        invariant_ok,
        (
            f"NEW={new}, "
            f"REVISION={revision}, "
            f"REOBSERVATION={reobs}"
        ),
    )

    revision_rows = {
        row["dataset"]:
            row["revision_versions"]
        for row in conn.execute(
            """
            SELECT
                lr.dataset,
                SUM(x.version_count - 1)
                    AS revision_versions

            FROM (
                SELECT
                    logical_record_id,
                    COUNT(*) AS version_count
                FROM raw_record_versions
                GROUP BY logical_record_id
                HAVING COUNT(*) > 1
            ) x

            JOIN logical_records lr
              ON lr.id = x.logical_record_id

            GROUP BY lr.dataset
            """
        )
    }

    add_check(
        checks,
        "Revision preservation",
        revision_rows
            == EXPECTED_REVISIONS,
        revision_rows,
    )

    late_total = conn.execute(
        """
        SELECT COUNT(*)
        FROM raw_record_observations
        WHERE late_arrival = 1
        """
    ).fetchone()[0]

    bad_late = conn.execute(
        """
        SELECT COUNT(*)
        FROM raw_record_observations
        WHERE late_arrival = 1
          AND classification <> 'NEW'
        """
    ).fetchone()[0]

    add_check(
        checks,
        "Late-arrival semantics",
        late_total == 56
        and bad_late == 0,
        (
            f"late_arrivals={late_total}, "
            f"invalid={bad_late}"
        ),
    )


def verify_archive_provenance(
    checks,
    conn,
):

    artifact_rows = conn.execute(
        """
        SELECT
            id,
            capture_id,
            archived_path,
            file_sha256
        FROM source_artifacts
        ORDER BY id
        """
    ).fetchall()

    artifact_ok = 0
    lines_total = 0
    obs_total = 0
    provenance_errors = 0

    for artifact in artifact_rows:

        path = Path(
            artifact["archived_path"]
        )

        if (
            not path.exists()
            or not path.is_file()
        ):
            provenance_errors += 1
            continue

        if (
            sha256_file(path)
            != artifact["file_sha256"]
        ):
            provenance_errors += 1
            continue

        artifact_ok += 1

        lines = {}

        with path.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            for ordinal, line in enumerate(
                f,
                start=1,
            ):
                line = line.strip()

                if line:
                    lines[ordinal] = line

        lines_total += len(lines)

        rows = conn.execute(
            """
            SELECT
                o.source_record_ordinal,
                o.capture_id,
                o.dataset,
                o.provider,
                o.region,

                rv.payload_sha256,
                rv.raw_json,

                lr.logical_key,
                lr.raw_key,
                lr.raw_sid,
                lr.raw_time

            FROM raw_record_observations o

            JOIN raw_record_versions rv
              ON rv.id = o.raw_record_version_id

            JOIN logical_records lr
              ON lr.id = rv.logical_record_id

            WHERE o.source_artifact_id = ?

            ORDER BY o.source_record_ordinal
            """,
            (artifact["id"],),
        ).fetchall()

        obs_total += len(rows)

        if len(rows) != len(lines):
            provenance_errors += 1

        for row in rows:

            line = lines.get(
                row["source_record_ordinal"]
            )

            if line is None:
                provenance_errors += 1
                continue

            try:
                envelope = json.loads(
                    line
                )

                raw = envelope[
                    "raw_record"
                ]

                if (
                    envelope["capture_id"]
                    != row["capture_id"]
                    or envelope["dataset"]
                    != row["dataset"]
                    or envelope["provider"]
                    != row["provider"]
                    or envelope["region"]
                    != row["region"]
                ):
                    provenance_errors += 1
                    continue

                if (
                    payload_hash(raw)
                    != row["payload_sha256"]
                    or envelope[
                        "raw_record_sha256"
                    ]
                    != row["payload_sha256"]
                ):
                    provenance_errors += 1
                    continue

                lk = logical_key(
                    envelope["provider"],
                    envelope["region"],
                    envelope["dataset"],
                    raw["key"],
                    raw["sid"],
                    raw["time"],
                )

                if (
                    lk
                    != row["logical_key"]
                    or str(raw["key"])
                    != row["raw_key"]
                    or str(raw["sid"])
                    != row["raw_sid"]
                    or int(raw["time"])
                    != row["raw_time"]
                ):
                    provenance_errors += 1
                    continue

                if (
                    canonical_json(raw)
                    != row["raw_json"]
                ):
                    provenance_errors += 1

            except Exception:
                provenance_errors += 1

    manifest_ok = 0

    capture_rows = conn.execute(
        """
        SELECT
            capture_id,
            manifest_sha256
        FROM captures
        """
    ).fetchall()

    for capture in capture_rows:

        candidates = list(
            ARCHIVE.glob(
                f"{capture['capture_id']}*"
            )
        )

        found = False

        for capture_dir in candidates:

            manifest = (
                capture_dir
                / "manifest.json"
            )

            if (
                manifest.exists()
                and sha256_file(manifest)
                == capture["manifest_sha256"]
            ):
                found = True
                break

        if found:
            manifest_ok += 1

    passed = (
        artifact_ok == 81
        and manifest_ok == 9
        and lines_total == 15751
        and obs_total == 15751
        and provenance_errors == 0
    )

    add_check(
        checks,
        "Full provenance traceability",
        passed,
        (
            f"artifacts={artifact_ok}/81, "
            f"manifests={manifest_ok}/9, "
            f"lines={lines_total}, "
            f"observations={obs_total}, "
            f"errors={provenance_errors}"
        ),
    )


def verify_backup_restore(
    checks,
):

    backup = latest_file(
        BACKUPS.glob(
            "l2_verified_*.sqlite3"
        )
    )

    restored = latest_file(
        RESTORE_TEST.glob(
            "restored_*.sqlite3"
        )
    )

    if (
        backup is None
        or restored is None
    ):
        add_check(
            checks,
            "Backup / restore",
            False,
            "verified backup or restored DB missing",
        )
        return

    backup_conn = connect(
        backup
    )

    restore_conn = connect(
        restored
    )

    try:

        b_integrity, b_fk = db_integrity(
            backup_conn
        )

        r_integrity, r_fk = db_integrity(
            restore_conn
        )

        counts_match = True

        for table, expected in (
            EXPECTED_COUNTS.items()
        ):

            if (
                table_count(
                    backup_conn,
                    table,
                )
                != expected
            ):
                counts_match = False

            if (
                table_count(
                    restore_conn,
                    table,
                )
                != expected
            ):
                counts_match = False

        passed = (
            counts_match
            and b_integrity == "ok"
            and r_integrity == "ok"
            and b_fk == 0
            and r_fk == 0
        )

        add_check(
            checks,
            "Backup / restore",
            passed,
            (
                f"backup={backup.name}, "
                f"restored={restored.name}"
            ),
        )

    finally:
        backup_conn.close()
        restore_conn.close()


def verify_archive_rebuild(
    checks,
    prod_semantics,
):

    candidates = list(
        REBUILD_TEST.glob(
            "*/db/personal_health_raw.sqlite3"
        )
    )

    rebuilt = latest_file(
        candidates
    )

    if rebuilt is None:
        add_check(
            checks,
            "Full rebuild from archive",
            False,
            "rebuild DB missing",
        )
        return

    conn = connect(
        rebuilt
    )

    try:

        integrity, fk = db_integrity(
            conn
        )

        rebuilt_semantics = (
            raw_semantics(
                conn
            )
        )

        passed = (
            integrity == "ok"
            and fk == 0
            and rebuilt_semantics
                == prod_semantics
        )

        add_check(
            checks,
            "Full rebuild from archive",
            passed,
            f"db={rebuilt}",
        )

    finally:
        conn.close()


def verify_atomic_migration(
    checks,
):

    migration_db = latest_file(
        MIGRATION_TEST.glob(
            "atomic_migration_*.sqlite3"
        )
    )

    if migration_db is None:
        add_check(
            checks,
            "Atomic schema migration",
            False,
            "atomic migration test DB missing",
        )
        return

    conn = connect(
        migration_db
    )

    try:

        integrity, fk = db_integrity(
            conn
        )

        versions = [
            row[0]
            for row in conn.execute(
                """
                SELECT version
                FROM schema_migrations
                ORDER BY version
                """
            )
        ]

        v2_table = conn.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type='table'
              AND name='migration_atomic_smoke_test'
            """
        ).fetchone()[0]

        rollback_table = conn.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type='table'
              AND name='migration_atomic_rollback_test'
            """
        ).fetchone()[0]

        v3_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM schema_migrations
            WHERE version=3
            """
        ).fetchone()[0]

        counts_ok = all(
            table_count(
                conn,
                table,
            )
            == expected
            for table, expected
            in EXPECTED_COUNTS.items()
        )

        passed = (
            versions == [1, 2]
            and v2_table == 1
            and rollback_table == 0
            and v3_rows == 0
            and counts_ok
            and integrity == "ok"
            and fk == 0
        )

        add_check(
            checks,
            "Atomic schema migration",
            passed,
            (
                f"versions={versions}, "
                f"rollback_table={rollback_table}, "
                f"v3_rows={v3_rows}"
            ),
        )

    finally:
        conn.close()


def write_seal(
    checks,
    summary,
):

    timestamp = now_utc()

    lines = [
        "# Personal Health Engine",
        "# Layer 2 Seal",
        "",
        f"Status: SEALED",
        f"Sealed at UTC: {timestamp}",
        "",
        "## Final Decision",
        "",
        "L2 CORE ACCEPTANCE = PASS",
        "",
        "Layer 2 has demonstrated durable, append-preserving,",
        "version-aware and provenance-complete storage of",
        "Xiaomi raw health records.",
        "",
        "## Canonical Layer 2",
        "",
        f"- Root: `{ROOT}`",
        f"- Database: `{DB}`",
        f"- Archive: `{ARCHIVE}`",
        "- Production schema version: `1`",
        "- Logical identity version: `xiaomi-v0.1`",
        "",
        "## Frozen Logical Identity",
        "",
        "`provider + region + dataset + raw_record.key + raw_record.sid + raw_record.time`",
        "",
        "`update_time`, `value`, timezone fields and all other payload content",
        "do not participate in logical identity.",
        "",
        "## Final Raw Store Counts",
        "",
        f"- captures: {summary['captures']}",
        f"- source artifacts: {summary['source_artifacts']}",
        f"- logical records: {summary['logical_records']}",
        f"- raw record versions: {summary['raw_record_versions']}",
        f"- observations: {summary['raw_record_observations']}",
        f"- revisions: {summary['revisions']}",
        f"- late arrivals under L2 semantics: {summary['late_arrivals']}",
        "",
        "## Validated",
        "",
    ]

    for check in checks:

        status = (
            "PASS"
            if check["pass"]
            else "FAIL"
        )

        lines.append(
            f"- {check['name']}: {status}"
        )

    lines += [
        "",
        "## Layer Boundary",
        "",
        "Layer 2 stores raw / near-raw health facts and provenance only.",
        "It does not perform feature engineering, daily aggregation,",
        "sleep-source selection, personal baseline calculation,",
        "anomaly detection, health scoring or AI reasoning.",
        "",
        "## Recovery Contract",
        "",
        "- SQLite backup provides fast recovery.",
        "- Immutable L2 archive is the source of full rebuild.",
        "- Raw Store semantic state has been rebuilt successfully from archive alone.",
        "",
        "## Upstream Boundary",
        "",
        "Layer 1 remains SEALED.",
        "Layer 2 does not reopen Xiaomi authentication, protocol or Collector logic.",
        "",
        "## Next Layer",
        "",
        "Next formal work may proceed to:",
        "",
        "Layer 3 = Feature Engineering / 特征工程",
        "",
        "Do not modify the frozen Layer 2 raw identity/version semantics",
        "without an explicit migration and compatibility review.",
        "",
    ]

    SEAL_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main():

    checks = []

    if not DB.exists():
        raise RuntimeError(
            f"Production DB missing: {DB}"
        )

    conn = connect(
        DB
    )

    try:

        verify_core_database(
            checks,
            conn,
        )

        verify_version_semantics(
            checks,
            conn,
        )

        verify_archive_provenance(
            checks,
            conn,
        )

        prod_semantics = raw_semantics(
            conn
        )

        summary = {
            "captures":
                table_count(
                    conn,
                    "captures",
                ),

            "source_artifacts":
                table_count(
                    conn,
                    "source_artifacts",
                ),

            "logical_records":
                table_count(
                    conn,
                    "logical_records",
                ),

            "raw_record_versions":
                table_count(
                    conn,
                    "raw_record_versions",
                ),

            "raw_record_observations":
                table_count(
                    conn,
                    "raw_record_observations",
                ),

            "revisions":
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM raw_record_observations
                    WHERE classification='REVISION'
                    """
                ).fetchone()[0],

            "late_arrivals":
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM raw_record_observations
                    WHERE late_arrival=1
                    """
                ).fetchone()[0],
        }

    finally:
        conn.close()

    verify_backup_restore(
        checks
    )

    verify_archive_rebuild(
        checks,
        prod_semantics,
    )

    verify_atomic_migration(
        checks
    )

    passed_count = sum(
        1
        for c in checks
        if c["pass"]
    )

    failed = [
        c
        for c in checks
        if not c["pass"]
    ]

    report = {
        "schema": "PHE-L2-Final-Audit-v0.1",
        "generated_at_utc": now_utc(),
        "status":
            "PASS"
            if not failed
            else "FAIL",
        "summary": summary,
        "checks": checks,
    }

    AUDIT_JSON.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "========== L2 FINAL ACCEPTANCE AUDIT =========="
    )

    for check in checks:

        status = (
            "PASS"
            if check["pass"]
            else "FAIL"
        )

        print(
            f"{status:4s}  "
            f"{check['name']}"
        )

        if not check["pass"]:
            print(
                f"      {check['detail']}"
            )

    print()

    print(
        f"checks_passed = "
        f"{passed_count}/{len(checks)}"
    )

    print(
        f"checks_failed = "
        f"{len(failed)}"
    )

    print(
        f"audit_json    = "
        f"{AUDIT_JSON}"
    )

    print()

    if failed:

        print("RESULT = FAIL")
        print("L2 STATUS = ACTIVE")
        print("L2_SEAL.md NOT CREATED")

        return

    write_seal(
        checks,
        summary,
    )

    print("RESULT = PASS")
    print("L2 CORE ACCEPTANCE = PASS")
    print("L2 STATUS = SEALED")
    print(
        f"L2_SEAL = {SEAL_FILE}"
    )


if __name__ == "__main__":
    main()
