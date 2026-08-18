from pathlib import Path
from datetime import datetime, timezone
import hashlib
import sqlite3


ROOT = Path(r"D:\PersonalHealthEngine-L2")

SOURCE_DB = (
    ROOT
    / "db"
    / "personal_health_raw.sqlite3"
)

TEST_DIR = ROOT / "migration_test"

stamp = datetime.now().strftime("%Y%m%dT%H%M%S")

TEST_DB = (
    TEST_DIR
    / f"atomic_migration_{stamp}.sqlite3"
)


SUCCESS_VERSION = 2
SUCCESS_NAME = "atomic-migration-smoke-test"

SUCCESS_SQL = [
    """
    CREATE TABLE migration_atomic_smoke_test (
        id INTEGER PRIMARY KEY,
        marker TEXT NOT NULL
    )
    """,
    """
    INSERT INTO migration_atomic_smoke_test (
        id,
        marker
    )
    VALUES (
        1,
        'atomic migration operational'
    )
    """,
]


FAIL_VERSION = 3
FAIL_NAME = "intentional-rollback-test"

FAIL_SQL = [
    """
    CREATE TABLE migration_atomic_rollback_test (
        id INTEGER PRIMARY KEY,
        marker TEXT NOT NULL
    )
    """,
    """
    INSERT INTO migration_atomic_rollback_test (
        id,
        marker
    )
    VALUES (
        1,
        'this must disappear after rollback'
    )
    """,
    """
    INSERT INTO definitely_missing_table (
        id
    )
    VALUES (
        1
    )
    """,
]


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def migration_checksum(statements):
    canonical = "\n".join(
        statement.strip()
        for statement in statements
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def apply_migration(
    conn,
    version,
    name,
    statements,
):
    checksum = migration_checksum(
        statements
    )

    existing = conn.execute(
        """
        SELECT checksum
        FROM schema_migrations
        WHERE version = ?
        """,
        (version,),
    ).fetchone()

    if existing is not None:

        if existing[0] != checksum:
            raise RuntimeError(
                f"Migration checksum conflict "
                f"for version {version}"
            )

        return "ALREADY_APPLIED"

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
                checksum,
                applied_at_utc
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                version,
                name,
                checksum,
                utc_now(),
            ),
        )

        conn.execute(
            "COMMIT"
        )

        return "APPLIED"

    except Exception:

        conn.execute(
            "ROLLBACK"
        )

        raise


def table_exists(conn, name):

    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (name,),
    ).fetchone()

    return row[0] == 1


def count(conn, table):

    return conn.execute(
        f"SELECT COUNT(*) FROM {table}"
    ).fetchone()[0]


def main():

    TEST_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # -------------------------------
    # CLONE PRODUCTION DATABASE
    # -------------------------------

    src = sqlite3.connect(
        SOURCE_DB
    )

    dst = sqlite3.connect(
        TEST_DB
    )

    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    # isolation_level=None means
    # transaction control is explicit.
    conn = sqlite3.connect(
        TEST_DB,
        isolation_level=None,
    )

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    before_version = conn.execute(
        """
        SELECT MAX(version)
        FROM schema_migrations
        """
    ).fetchone()[0]

    before_logical = count(
        conn,
        "logical_records",
    )

    before_versions = count(
        conn,
        "raw_record_versions",
    )

    before_observations = count(
        conn,
        "raw_record_observations",
    )

    # -------------------------------
    # SUCCESSFUL MIGRATION
    # -------------------------------

    first_result = apply_migration(
        conn,
        SUCCESS_VERSION,
        SUCCESS_NAME,
        SUCCESS_SQL,
    )

    second_result = apply_migration(
        conn,
        SUCCESS_VERSION,
        SUCCESS_NAME,
        SUCCESS_SQL,
    )

    successful_table_exists = table_exists(
        conn,
        "migration_atomic_smoke_test",
    )

    successful_marker = conn.execute(
        """
        SELECT marker
        FROM migration_atomic_smoke_test
        WHERE id = 1
        """
    ).fetchone()

    v2_rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM schema_migrations
        WHERE version = 2
        """
    ).fetchone()[0]

    # -------------------------------
    # INTENTIONALLY FAIL V3
    # -------------------------------

    failure_caught = False
    failure_message = None

    try:

        apply_migration(
            conn,
            FAIL_VERSION,
            FAIL_NAME,
            FAIL_SQL,
        )

    except Exception as e:
        failure_caught = True
        failure_message = str(e)

    rollback_table_exists = table_exists(
        conn,
        "migration_atomic_rollback_test",
    )

    v3_rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM schema_migrations
        WHERE version = 3
        """
    ).fetchone()[0]

    after_version = conn.execute(
        """
        SELECT MAX(version)
        FROM schema_migrations
        """
    ).fetchone()[0]

    after_logical = count(
        conn,
        "logical_records",
    )

    after_versions = count(
        conn,
        "raw_record_versions",
    )

    after_observations = count(
        conn,
        "raw_record_observations",
    )

    integrity = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    conn.close()

    # -------------------------------
    # VERIFY PRODUCTION DB UNCHANGED
    # -------------------------------

    prod = sqlite3.connect(
        SOURCE_DB
    )

    production_version = prod.execute(
        """
        SELECT MAX(version)
        FROM schema_migrations
        """
    ).fetchone()[0]

    production_logical = count(
        prod,
        "logical_records",
    )

    production_versions = count(
        prod,
        "raw_record_versions",
    )

    production_observations = count(
        prod,
        "raw_record_observations",
    )

    prod.close()

    print(
        "========== L2 ATOMIC MIGRATION TEST =========="
    )

    print(f"SOURCE DB = {SOURCE_DB}")
    print(f"TEST DB   = {TEST_DB}")

    print()

    print("========== SUCCESS PATH ==========")

    print(
        f"before_schema_version = "
        f"{before_version}"
    )

    print(
        f"after_schema_version  = "
        f"{after_version}"
    )

    print(
        f"first_apply           = "
        f"{first_result}"
    )

    print(
        f"second_apply          = "
        f"{second_result}"
    )

    print(
        f"v2_migration_rows     = "
        f"{v2_rows}"
    )

    print(
        f"v2_table_exists       = "
        f"{successful_table_exists}"
    )

    print(
        f"v2_marker_present     = "
        f"{successful_marker is not None}"
    )

    print()

    print("========== ROLLBACK PATH ==========")

    print(
        f"failure_caught        = "
        f"{failure_caught}"
    )

    print(
        f"failure_message       = "
        f"{failure_message}"
    )

    print(
        f"rollback_table_exists = "
        f"{rollback_table_exists}"
    )

    print(
        f"v3_migration_rows     = "
        f"{v3_rows}"
    )

    print()

    print("========== RAW DATA ==========")

    print(
        f"logical_records       = "
        f"{before_logical} -> "
        f"{after_logical}"
    )

    print(
        f"raw_record_versions   = "
        f"{before_versions} -> "
        f"{after_versions}"
    )

    print(
        f"observations          = "
        f"{before_observations} -> "
        f"{after_observations}"
    )

    print()

    print("========== PRODUCTION ==========")

    print(
        f"production_schema_version = "
        f"{production_version}"
    )

    print(
        f"production_logical        = "
        f"{production_logical}"
    )

    print(
        f"production_versions       = "
        f"{production_versions}"
    )

    print(
        f"production_observations   = "
        f"{production_observations}"
    )

    print()

    print(
        f"integrity_check       = "
        f"{integrity}"
    )

    print(
        f"foreign_key_errors    = "
        f"{len(fk_errors)}"
    )

    passed = (
        before_version == 1
        and after_version == 2

        and first_result == "APPLIED"
        and second_result
            == "ALREADY_APPLIED"

        and v2_rows == 1
        and successful_table_exists
        and successful_marker is not None

        and failure_caught
        and not rollback_table_exists
        and v3_rows == 0

        and before_logical
            == after_logical
            == production_logical

        and before_versions
            == after_versions
            == production_versions

        and before_observations
            == after_observations
            == production_observations

        and production_version == 1

        and integrity == "ok"
        and len(fk_errors) == 0
    )

    print()

    if passed:
        print("RESULT = PASS")
        print(
            "L2 ATOMIC SCHEMA MIGRATION = PASS"
        )
        print(
            "FAILED MIGRATION ROLLBACK = PASS"
        )
        print(
            "PRODUCTION DATABASE = UNCHANGED"
        )
    else:
        print("RESULT = FAIL")


if __name__ == "__main__":
    main()
