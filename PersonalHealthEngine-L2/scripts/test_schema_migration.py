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

stamp = datetime.now().strftime(
    "%Y%m%dT%H%M%S"
)

TEST_DB = (
    TEST_DIR
    / f"migration_{stamp}.sqlite3"
)


MIGRATION_VERSION = 2
MIGRATION_NAME = "migration-framework-smoke-test"

MIGRATION_SQL = """
CREATE TABLE migration_smoke_test (
    id INTEGER PRIMARY KEY,
    marker TEXT NOT NULL
);

INSERT INTO migration_smoke_test (
    id,
    marker
)
VALUES (
    1,
    'L2 migration framework operational'
);
"""


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def checksum(text):
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def apply_migration(conn):

    migration_checksum = checksum(
        MIGRATION_SQL
    )

    existing = conn.execute(
        """
        SELECT
            name,
            checksum
        FROM schema_migrations
        WHERE version = ?
        """,
        (MIGRATION_VERSION,)
    ).fetchone()

    if existing is not None:

        if existing[1] != migration_checksum:
            raise RuntimeError(
                "Migration checksum conflict."
            )

        return "ALREADY_APPLIED"

    try:

        conn.execute("BEGIN IMMEDIATE")

        conn.executescript(
            MIGRATION_SQL
        )

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
                MIGRATION_VERSION,
                MIGRATION_NAME,
                migration_checksum,
                utc_now(),
            )
        )

        conn.commit()

        return "APPLIED"

    except Exception:
        conn.rollback()
        raise


def main():

    TEST_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # ---------------------------------
    # CLONE PRODUCTION DB
    # ---------------------------------

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

    conn = sqlite3.connect(
        TEST_DB
    )

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    print(
        "========== L2 SCHEMA MIGRATION TEST =========="
    )

    print(
        f"SOURCE DB = {SOURCE_DB}"
    )

    print(
        f"TEST DB   = {TEST_DB}"
    )

    print()

    # ---------------------------------
    # BASELINE
    # ---------------------------------

    before_version = conn.execute(
        """
        SELECT MAX(version)
        FROM schema_migrations
        """
    ).fetchone()[0]

    before_logical = conn.execute(
        """
        SELECT COUNT(*)
        FROM logical_records
        """
    ).fetchone()[0]

    before_versions = conn.execute(
        """
        SELECT COUNT(*)
        FROM raw_record_versions
        """
    ).fetchone()[0]

    before_observations = conn.execute(
        """
        SELECT COUNT(*)
        FROM raw_record_observations
        """
    ).fetchone()[0]

    # ---------------------------------
    # FIRST MIGRATION
    # ---------------------------------

    first_result = apply_migration(
        conn
    )

    after_version = conn.execute(
        """
        SELECT MAX(version)
        FROM schema_migrations
        """
    ).fetchone()[0]

    marker = conn.execute(
        """
        SELECT marker
        FROM migration_smoke_test
        WHERE id = 1
        """
    ).fetchone()

    # ---------------------------------
    # SECOND APPLICATION
    # MUST BE IDEMPOTENT
    # ---------------------------------

    second_result = apply_migration(
        conn
    )

    migration_rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM schema_migrations
        WHERE version = ?
        """,
        (MIGRATION_VERSION,)
    ).fetchone()[0]

    # ---------------------------------
    # RAW DATA MUST NOT CHANGE
    # ---------------------------------

    after_logical = conn.execute(
        """
        SELECT COUNT(*)
        FROM logical_records
        """
    ).fetchone()[0]

    after_versions = conn.execute(
        """
        SELECT COUNT(*)
        FROM raw_record_versions
        """
    ).fetchone()[0]

    after_observations = conn.execute(
        """
        SELECT COUNT(*)
        FROM raw_record_observations
        """
    ).fetchone()[0]

    integrity = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    conn.close()

    print(
        f"before_schema_version = {before_version}"
    )

    print(
        f"after_schema_version  = {after_version}"
    )

    print(
        f"first_apply           = {first_result}"
    )

    print(
        f"second_apply          = {second_result}"
    )

    print(
        f"migration_rows        = {migration_rows}"
    )

    print(
        f"marker_present        = {marker is not None}"
    )

    print()

    print(
        f"logical_records       = "
        f"{before_logical} -> {after_logical}"
    )

    print(
        f"raw_record_versions   = "
        f"{before_versions} -> {after_versions}"
    )

    print(
        f"observations          = "
        f"{before_observations} -> "
        f"{after_observations}"
    )

    print()

    print(
        f"integrity_check       = {integrity}"
    )

    print(
        f"foreign_key_errors    = "
        f"{len(fk_errors)}"
    )

    passed = (
        before_version == 1
        and after_version == 2
        and first_result == "APPLIED"
        and second_result == "ALREADY_APPLIED"
        and migration_rows == 1
        and marker is not None
        and before_logical == after_logical
        and before_versions == after_versions
        and before_observations
            == after_observations
        and integrity == "ok"
        and len(fk_errors) == 0
    )

    print()

    if passed:
        print("RESULT = PASS")
        print(
            "L2 SCHEMA MIGRATION = PASS"
        )
        print(
            "PRODUCTION DATABASE = UNCHANGED"
        )
    else:
        print("RESULT = FAIL")


if __name__ == "__main__":
    main()
