from pathlib import Path
from datetime import datetime
import sqlite3

ROOT = Path(r"D:\PersonalHealthEngine-L2")

SOURCE_DB = (
    ROOT
    / "db"
    / "personal_health_raw.sqlite3"
)

BACKUP_DIR = ROOT / "backups"
RESTORE_DIR = ROOT / "restore_test"

TABLES = [
    "captures",
    "source_artifacts",
    "logical_records",
    "raw_record_versions",
    "raw_record_observations",
    "ingestion_runs",
    "ingestion_issues",
]


def counts(conn):
    result = {}

    for table in TABLES:
        result[table] = conn.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]

    return result


def main():

    BACKUP_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    RESTORE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    stamp = datetime.now().strftime(
        "%Y%m%dT%H%M%S"
    )

    backup_db = (
        BACKUP_DIR
        / f"l2_verified_{stamp}.sqlite3"
    )

    restored_db = (
        RESTORE_DIR
        / f"restored_{stamp}.sqlite3"
    )

    # -------- CREATE BACKUP --------

    src = sqlite3.connect(SOURCE_DB)
    dst = sqlite3.connect(backup_db)

    try:
        src.backup(dst)
    finally:
        dst.close()

    source_counts = counts(src)

    source_integrity = src.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    source_fk = src.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    src.close()

    # -------- RESTORE FROM BACKUP --------

    backup_conn = sqlite3.connect(backup_db)
    restore_conn = sqlite3.connect(restored_db)

    try:
        backup_conn.backup(restore_conn)
    finally:
        backup_conn.close()
        restore_conn.close()

    # -------- VERIFY RESTORED DB --------

    restored = sqlite3.connect(restored_db)

    restored.execute(
        "PRAGMA foreign_keys = ON"
    )

    restored_counts = counts(restored)

    restored_integrity = restored.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    restored_fk = restored.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    restored.close()

    print(
        "========== L2 BACKUP RESTORE TEST =========="
    )

    print(f"SOURCE   = {SOURCE_DB}")
    print(f"BACKUP   = {backup_db}")
    print(f"RESTORED = {restored_db}")

    print()

    print("========== TABLE COUNTS ==========")

    all_match = True

    for table in TABLES:

        s = source_counts[table]
        r = restored_counts[table]

        match = s == r

        if not match:
            all_match = False

        print(
            f"{table:26s} "
            f"source={s:<8} "
            f"restored={r:<8} "
            f"match={match}"
        )

    print()

    print(
        f"source_integrity   = "
        f"{source_integrity}"
    )

    print(
        f"restored_integrity = "
        f"{restored_integrity}"
    )

    print(
        f"source_fk_errors   = "
        f"{len(source_fk)}"
    )

    print(
        f"restored_fk_errors = "
        f"{len(restored_fk)}"
    )

    print()

    if (
        all_match
        and source_integrity == "ok"
        and restored_integrity == "ok"
        and len(source_fk) == 0
        and len(restored_fk) == 0
    ):
        print("RESULT = PASS")
        print(
            "L2 BACKUP RESTORE = PASS"
        )
    else:
        print("RESULT = FAIL")


if __name__ == "__main__":
    main()
