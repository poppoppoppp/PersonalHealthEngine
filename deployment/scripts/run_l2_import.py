"""Production deployment wrapper for the SEALED L2 importer.

The SEALED importer remains unchanged. This wrapper only:
1. injects Linux/VPS runtime paths from environment variables;
2. runs the canonical importer;
3. performs a dynamic runtime health check instead of relying on historical seal counts.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path


DEPLOYMENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEPLOYMENT_DIR.parent.parent
IMPORTER_PATH = (
    REPO_ROOT
    / "PersonalHealthEngine-L2"
    / "scripts"
    / "import_l1_to_l2.py"
)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def load_importer():
    spec = importlib.util.spec_from_file_location(
        "phe_sealed_l2_importer",
        IMPORTER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load L2 importer: {IMPORTER_PATH}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_audit(db_path: Path) -> None:
    if not db_path.exists():
        raise RuntimeError(f"L2 database does not exist after import: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

        latest = conn.execute(
            """
            SELECT status, error_summary
            FROM ingestion_runs
            ORDER BY started_at_utc DESC
            LIMIT 1
            """
        ).fetchone()

        if integrity != "ok":
            raise RuntimeError(f"L2 integrity_check failed: {integrity}")

        if fk_errors:
            raise RuntimeError(
                f"L2 foreign_key_check failed: {len(fk_errors)} errors"
            )

        if latest is None:
            raise RuntimeError("L2 has no ingestion run after importer execution")

        if latest["status"] != "SUCCESS":
            raise RuntimeError(
                "latest L2 ingestion run is not SUCCESS: "
                f"{latest['status']} / {latest['error_summary']}"
            )

        print("L2 RUNTIME AUDIT = PASS")
        print(f"database = {db_path}")
        print(f"integrity = {integrity}")
        print("foreign_key_errors = 0")
        print("latest_ingestion_status = SUCCESS")

    finally:
        conn.close()


def main() -> int:
    l1_root = Path(require_env("PHE_L1_ROOT"))
    l1_captures = Path(
        os.environ.get("PHE_L1_CAPTURES", str(l1_root / "captures"))
    )

    l2_root = Path(require_env("PHE_L2_ROOT"))
    l2_db = Path(
        os.environ.get(
            "PHE_L2_DB",
            str(l2_root / "db" / "personal_health_raw.sqlite3"),
        )
    )
    l2_archive = Path(
        os.environ.get("PHE_L2_ARCHIVE", str(l2_root / "archive"))
    )
    l2_backups = Path(
        os.environ.get("PHE_L2_BACKUPS", str(l2_root / "backups"))
    )

    module = load_importer()

    # Deployment-only path injection.
    # The SEALED importer source file itself is not modified.
    module.L1_ROOT = l1_root
    module.CAPTURES_ROOT = l1_captures

    module.L2_ROOT = l2_root
    module.DB_PATH = l2_db
    module.ARCHIVE_ROOT = l2_archive
    module.BACKUP_ROOT = l2_backups

    print("========== L2 PRODUCTION WRAPPER ==========")
    print(f"L1 captures : {l1_captures}")
    print(f"L2 database : {l2_db}")
    print(f"L2 archive  : {l2_archive}")
    print(f"L2 backups  : {l2_backups}")
    print()

    module.main()

    runtime_audit(l2_db)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print("L2 PRODUCTION WRAPPER = FAIL")
        print(f"ERROR = {exc}", file=sys.stderr)
        raise
