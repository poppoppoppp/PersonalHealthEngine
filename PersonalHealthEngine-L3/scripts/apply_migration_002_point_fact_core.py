import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(r"D:\PersonalHealthEngine-L3")

DB = (
    ROOT
    / "db"
    / "personal_health_features.sqlite3"
)

MIGRATION = (
    ROOT
    / "migrations"
    / "002_point_fact_core.sql"
)

VERSION = 2
NAME = "point_fact_core"

EXPECTED_NEW_TABLES = {
    "fact_registry",
    "normalized_point_facts",
    "fact_provenance",
}


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
            "Incomplete SQL statement"
        )

    return statements


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

    current_version = conn.execute(
        "PRAGMA user_version"
    ).fetchone()[0]

    if current_version not in (1, 2):
        raise RuntimeError(
            f"Unexpected starting schema version: "
            f"{current_version}"
        )

    row = conn.execute(
        """
        SELECT checksum_sha256
        FROM schema_migrations
        WHERE version = ?
        """,
        (VERSION,)
    ).fetchone()

    already_applied = False

    if row is not None:
        if row[0] != checksum:
            raise RuntimeError(
                "Migration 002 checksum mismatch"
            )

        already_applied = True

    if not already_applied:

        if current_version != 1:
            raise RuntimeError(
                "Migration 002 requires schema version 1"
            )

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
                "PRAGMA user_version = 2"
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        result = "APPLIED"

    else:
        result = "ALREADY_APPLIED"

    # ========================================================
    # Acceptance audit
    # ========================================================

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

    migration_versions = [
        row[0]
        for row in conn.execute(
            """
            SELECT version
            FROM schema_migrations
            ORDER BY version
            """
        ).fetchall()
    ]

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

    missing = (
        EXPECTED_NEW_TABLES - tables
    )

    # Validate FK topology.
    point_fk = conn.execute(
        """
        PRAGMA foreign_key_list(
            normalized_point_facts
        )
        """
    ).fetchall()

    provenance_fk = conn.execute(
        """
        PRAGMA foreign_key_list(
            fact_provenance
        )
        """
    ).fetchall()

    empty_counts = {
        table: conn.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        for table in EXPECTED_NEW_TABLES
    }

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
            user_version == 2
        ),
        (
            "Migration chain 1 -> 2",
            migration_versions == [1, 2]
        ),
        (
            "Point fact tables present",
            not missing
        ),
        (
            "Point fact FK",
            len(point_fk) == 1
        ),
        (
            "Provenance FK",
            len(provenance_fk) == 1
        ),
        (
            "New business tables empty",
            all(
                count == 0
                for count in empty_counts.values()
            )
        ),
    ]

    print("=" * 76)
    print("L3 SCHEMA V2 POINT FACT CORE")
    print("=" * 76)

    print("database         =", DB)
    print("migration        =", result)
    print("migration sha256 =", checksum)
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

    print(
        "schema_version =",
        user_version
    )

    print(
        "migration_chain =",
        migration_versions
    )

    print(
        "fact_registry rows =",
        empty_counts["fact_registry"]
    )

    print(
        "normalized_point_facts rows =",
        empty_counts[
            "normalized_point_facts"
        ]
    )

    print(
        "fact_provenance rows =",
        empty_counts["fact_provenance"]
    )

    print()

    if passed == len(checks):
        print("RESULT = PASS")
        print(
            "L3 POINT FACT CORE = READY"
        )
    else:
        print("RESULT = FAIL")
        print(
            "L3 POINT FACT CORE = NOT READY"
        )
        raise SystemExit(1)

finally:
    conn.close()
