"""Production wrapper for SEALED Layer 4."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

L4_ROOT = REPO / "PersonalHealthEngine-L4"
SCRIPTS = L4_ROOT / "scripts"
DEFS = L4_ROOT / "definitions"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"required environment variable is missing: {name}"
        )
    return value


def runtime_audit(l3_path: Path, l4_path: Path) -> None:
    l3 = sqlite3.connect(
        l3_path.resolve().as_uri() + "?mode=ro",
        uri=True,
    )
    l4 = sqlite3.connect(l4_path)

    try:
        integrity = l4.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        fk_errors = l4.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        schema = l4.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        l3_frontier = l3.execute(
            "SELECT COALESCE(MAX(id),0) FROM derived_features"
        ).fetchone()[0]

        checkpoint_row = l4.execute(
            """
            SELECT last_l3_feature_id
            FROM processing_checkpoints
            WHERE pipeline_name='l4.baseline'
            """
        ).fetchone()

        current_series = l4.execute(
            """
            SELECT COUNT(*)
            FROM baseline_series
            WHERE status='CURRENT'
            """
        ).fetchone()[0]

        current_baselines = l4.execute(
            """
            SELECT COUNT(*)
            FROM rolling_baselines
            WHERE status='CURRENT'
            """
        ).fetchone()[0]

        latest = l4.execute(
            """
            SELECT status
            FROM pipeline_runs
            ORDER BY started_at_utc DESC
            LIMIT 1
            """
        ).fetchone()

        if integrity != "ok":
            raise RuntimeError(
                f"L4 integrity_check failed: {integrity}"
            )

        if fk_errors:
            raise RuntimeError(
                f"L4 foreign_key_check failed: "
                f"{len(fk_errors)} errors"
            )

        if schema != 2:
            raise RuntimeError(
                f"L4 schema version must be 2, found {schema}"
            )

        if checkpoint_row is None:
            raise RuntimeError(
                "L4 baseline checkpoint is missing"
            )

        if checkpoint_row[0] != l3_frontier:
            raise RuntimeError(
                "L4 checkpoint is behind L3: "
                f"{checkpoint_row[0]} != {l3_frontier}"
            )

        if latest is None or latest[0] != "PASS":
            raise RuntimeError(
                "latest L4 pipeline run is not PASS"
            )

        if current_series <= 0:
            raise RuntimeError(
                "L4 has no CURRENT baseline series"
            )

        if current_baselines <= 0:
            raise RuntimeError(
                "L4 has no CURRENT rolling baselines"
            )

        print()
        print("L4 RUNTIME AUDIT = PASS")
        print(f"schema_version = {schema}")
        print(f"l3_frontier = {l3_frontier}")
        print(f"checkpoint = {checkpoint_row[0]}")
        print(f"current_series = {current_series}")
        print(f"current_baselines = {current_baselines}")
        print("foreign_key_errors = 0")

    finally:
        l3.close()
        l4.close()


def main() -> int:
    l3 = Path(require_env("PHE_L3_DB"))
    l4 = Path(require_env("PHE_L4_DB"))

    command = [
        sys.executable,
        str(
            SCRIPTS
            / "l4_baseline_materializer_v0_1.py"
        ),
        "--mode",
        "incremental",
        "--l3",
        str(l3),
        "--l4",
        str(l4),
        "--eligibility",
        str(
            DEFS
            / "eligibility"
            / "l4a_baseline_eligibility_v0_1.json"
        ),
        "--series",
        str(
            DEFS
            / "series"
            / "l4b_baseline_series_v0_1.json"
        ),
        "--windows",
        str(
            DEFS
            / "windows"
            / "l4c_baseline_windows_v0_1.json"
        ),
        "--maturity",
        str(
            DEFS
            / "maturity"
            / "l4d_baseline_maturity_v0_1.json"
        ),
    ]

    print("========== L4 PRODUCTION PIPELINE ==========")

    result = subprocess.run(
        command,
        cwd=L4_ROOT,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "L4 materializer failed with exit code "
            f"{result.returncode}"
        )

    runtime_audit(l3, l4)

    print()
    print("L4 PRODUCTION PIPELINE = PASS")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print("L4 PRODUCTION PIPELINE = FAIL")
        print(f"ERROR = {exc}", file=sys.stderr)
        raise
