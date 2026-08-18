"""Production wrapper for SEALED Layer 5."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

L5_ROOT = REPO / "PersonalHealthEngine-L5"
SCRIPTS = L5_ROOT / "scripts"
DEFS = L5_ROOT / "definitions"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"required environment variable is missing: {name}"
        )
    return value


def runtime_audit(
    l3_path: Path,
    l4_path: Path,
    l5_path: Path,
) -> None:

    l3 = sqlite3.connect(
        l3_path.resolve().as_uri() + "?mode=ro",
        uri=True,
    )

    l4 = sqlite3.connect(
        l4_path.resolve().as_uri() + "?mode=ro",
        uri=True,
    )

    l5 = sqlite3.connect(l5_path)

    try:
        integrity = l5.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        fk_errors = l5.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        schema = l5.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        l3_frontier = l3.execute(
            """
            SELECT COALESCE(MAX(id),0)
            FROM derived_features
            """
        ).fetchone()[0]

        l4_frontier = l4.execute(
            """
            SELECT COALESCE(MAX(id),0)
            FROM rolling_baselines
            """
        ).fetchone()[0]

        checkpoint = l5.execute(
            """
            SELECT
                last_l3_feature_id,
                last_l4_baseline_id
            FROM processing_checkpoints
            WHERE pipeline_name='l5.analytics'
            """
        ).fetchone()

        latest = l5.execute(
            """
            SELECT status
            FROM pipeline_runs
            ORDER BY started_at_utc DESC
            LIMIT 1
            """
        ).fetchone()

        current_series = l5.execute(
            """
            SELECT COUNT(*)
            FROM analytics_series
            WHERE status='CURRENT'
            """
        ).fetchone()[0]

        current_analytics = 0

        for table in (
            "deviation_analytics",
            "persistence_analytics",
            "trend_analytics",
            "change_point_analytics",
            "relationship_analytics",
        ):
            current_analytics += l5.execute(
                f"""
                SELECT COUNT(*)
                FROM {table}
                WHERE status='CURRENT'
                """
            ).fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                f"L5 integrity_check failed: {integrity}"
            )

        if fk_errors:
            raise RuntimeError(
                f"L5 foreign_key_check failed: "
                f"{len(fk_errors)} errors"
            )

        if schema != 2:
            raise RuntimeError(
                f"L5 schema version must be 2, found {schema}"
            )

        if checkpoint is None:
            raise RuntimeError(
                "L5 analytics checkpoint is missing"
            )

        if checkpoint[0] != l3_frontier:
            raise RuntimeError(
                "L5 L3 checkpoint mismatch: "
                f"{checkpoint[0]} != {l3_frontier}"
            )

        if checkpoint[1] != l4_frontier:
            raise RuntimeError(
                "L5 L4 checkpoint mismatch: "
                f"{checkpoint[1]} != {l4_frontier}"
            )

        if latest is None or latest[0] != "PASS":
            raise RuntimeError(
                "latest L5 pipeline run is not PASS"
            )

        if current_series <= 0:
            raise RuntimeError(
                "L5 has no CURRENT analytics series"
            )

        if current_analytics <= 0:
            raise RuntimeError(
                "L5 has no CURRENT analytics"
            )

        print()
        print("L5 RUNTIME AUDIT = PASS")
        print(f"schema_version = {schema}")
        print(f"l3_frontier = {l3_frontier}")
        print(f"l4_frontier = {l4_frontier}")
        print(f"checkpoint_l3 = {checkpoint[0]}")
        print(f"checkpoint_l4 = {checkpoint[1]}")
        print(f"current_series = {current_series}")
        print(f"current_analytics = {current_analytics}")
        print("foreign_key_errors = 0")

    finally:
        l3.close()
        l4.close()
        l5.close()


def main() -> int:

    l3 = Path(require_env("PHE_L3_DB"))
    l4 = Path(require_env("PHE_L4_DB"))
    l5 = Path(require_env("PHE_L5_DB"))

    command = [
        sys.executable,
        str(
            SCRIPTS
            / "l5_analytics_materializer_v0_1.py"
        ),
        "--mode",
        "incremental",
        "--l3",
        str(l3),
        "--l4",
        str(l4),
        "--l5",
        str(l5),

        "--deviation",
        str(
            DEFS
            / "deviation"
            / "l5a_deviation_robust_v0_1.json"
        ),

        "--persistence",
        str(
            DEFS
            / "persistence"
            / "l5b_persistence_v0_1.json"
        ),

        "--trend",
        str(
            DEFS
            / "trend"
            / "l5b_trend_robust_v0_1.json"
        ),

        "--change",
        str(
            DEFS
            / "change"
            / "l5c_change_point_v0_1.json"
        ),

        "--relationship",
        str(
            DEFS
            / "relationship"
            / "l5d_relationship_v0_1.json"
        ),

        "--evidence",
        str(
            DEFS
            / "evidence"
            / "l5e_evidence_v0_1.json"
        ),
    ]

    print("========== L5 PRODUCTION PIPELINE ==========")

    result = subprocess.run(
        command,
        cwd=L5_ROOT,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "L5 materializer failed with exit code "
            f"{result.returncode}"
        )

    runtime_audit(l3, l4, l5)

    print()
    print("L5 PRODUCTION PIPELINE = PASS")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print("L5 PRODUCTION PIPELINE = FAIL")
        print(f"ERROR = {exc}", file=sys.stderr)
        raise
