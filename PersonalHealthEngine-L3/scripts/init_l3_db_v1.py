import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(r"D:\PersonalHealthEngine-L3")

DB = ROOT / "db" / "personal_health_features.sqlite3"

MIGRATION = (
    ROOT
    / "migrations"
    / "001_foundation.sql"
)

L2 = Path(
    r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3"
)

EXPECTED_TABLES = {
    "schema_migrations",
    "definition_registry",
    "pipeline_runs",
    "processing_checkpoints",
    "normalization_issues",
}

VERSION = 1
NAME = "foundation"


def utc_now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
    )


def load_sql_statements(text):
    statements = []
    current = ""

    for line in text.splitlines():
        current += line + "\n"

        if sqlite3.complete_statement(current):
            statement = current.strip()

            if statement:
                statements.append(statement)

            current = ""

    if current.strip():
        raise RuntimeError(
            "Incomplete SQL statement in migration"
        )

    return statements


if not L2.exists():
    raise SystemExit(
        f"FAIL: canonical L2 DB not found: {L2}"
    )

DB.parent.mkdir(
    parents=True,
    exist_ok=True
)

sql_bytes = MIGRATION.read_bytes()

checksum = hashlib.sha256(
    sql_bytes
).hexdigest()

sql_text = sql_bytes.decode(
    "utf-8-sig"
)

conn = sqlite3.connect(DB)

try:
    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    journal_mode = conn.execute(
        "PRAGMA journal_mode = WAL"
    ).fetchone()[0]

    # --------------------------------------------------------
    # Determine whether migration already exists.
    # --------------------------------------------------------

    migration_table_exists = conn.execute(
        """
        SELECT COUNT(*)
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'schema_migrations'
        """
    ).fetchone()[0] == 1

    already_applied = False

    if migration_table_exists:
        row = conn.execute(
            """
            SELECT checksum_sha256
            FROM schema_migrations
            WHERE version = ?
            """,
            (VERSION,)
        ).fetchone()

        if row is not None:
            if row[0] != checksum:
                raise RuntimeError(
                    "Migration version 1 already exists "
                    "with a different checksum"
                )

            already_applied = True

    # --------------------------------------------------------
    # Atomic migration.
    # --------------------------------------------------------

    if not already_applied:
        statements = load_sql_statements(
            sql_text
        )

        conn.execute(
            "BEGIN IMMEDIATE"
        )

        try:
            for statement in statements:
                conn.execute(statement)

            conn.execute(
                """
                INSERT INTO schema_migrations (
                    version,
                    name,
                    applied_at_utc,
                    checksum_sha256
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    VERSION,
                    NAME,
                    utc_now(),
                    checksum,
                )
            )

            conn.execute(
                "PRAGMA user_version = 1"
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        migration_result = "APPLIED"

    else:
        migration_result = "ALREADY_APPLIED"

    # --------------------------------------------------------
    # Acceptance audit.
    # --------------------------------------------------------

    integrity = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    foreign_keys = conn.execute(
        "PRAGMA foreign_keys"
    ).fetchone()[0]

    journal_mode = conn.execute(
        "PRAGMA journal_mode"
    ).fetchone()[0]

    user_version = conn.execute(
        "PRAGMA user_version"
    ).fetchone()[0]

    migration_version = conn.execute(
        """
        SELECT MAX(version)
        FROM schema_migrations
        """
    ).fetchone()[0]

    tables = {
        row[0]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    }

    missing_tables = (
        EXPECTED_TABLES - tables
    )

    extra_tables = (
        tables - EXPECTED_TABLES
    )

    migration_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM schema_migrations
        """
    ).fetchone()[0]

    checks = [
        (
            "SQLite integrity",
            integrity == "ok"
        ),
        (
            "Foreign keys enabled",
            foreign_keys == 1
        ),
        (
            "WAL journal mode",
            journal_mode.lower() == "wal"
        ),
        (
            "Production schema version",
            user_version == 1
            and migration_version == 1
        ),
        (
            "Foundation table set",
            not missing_tables
            and not extra_tables
        ),
        (
            "Migration audit row",
            migration_count == 1
        ),
        (
            "L2 exists and remains separate",
            L2.resolve() != DB.resolve()
        ),
    ]

    print(
        "=" * 72
    )
    print(
        "L3 SCHEMA V1 FOUNDATION"
    )
    print(
        "=" * 72
    )

    print(
        "database         =", DB
    )
    print(
        "migration        =", migration_result
    )
    print(
        "migration sha256 =", checksum
    )
    print()

    passed = 0

    for name, ok in checks:
        status = (
            "PASS"
            if ok
            else "FAIL"
        )

        print(
            f"{status:<5} {name}"
        )

        if ok:
            passed += 1

    print()
    print(
        "checks_passed =",
        f"{passed}/{len(checks)}"
    )

    if missing_tables:
        print(
            "missing_tables =",
            sorted(missing_tables)
        )

    if extra_tables:
        print(
            "extra_tables =",
            sorted(extra_tables)
        )

    print()

    if passed == len(checks):
        print(
            "RESULT = PASS"
        )
        print(
            "L3 SCHEMA V1 FOUNDATION = READY"
        )
    else:
        print(
            "RESULT = FAIL"
        )
        print(
            "L3 SCHEMA V1 FOUNDATION = NOT READY"
        )
        raise SystemExit(1)

finally:
    conn.close()
