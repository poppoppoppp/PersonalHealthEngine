"""Production orchestrator for SEALED Layer 3.

Runs the canonical L3 production procedures in dependency order without
modifying any SEALED L3 implementation.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

L3_ROOT = REPO / "PersonalHealthEngine-L3"
SCRIPTS = L3_ROOT / "scripts"
DEFS = L3_ROOT / "definitions"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def run_step(name: str, command: list[str]) -> None:
    print()
    print("=" * 72)
    print(f"L3 STEP: {name}")
    print("=" * 72)

    result = subprocess.run(
        command,
        cwd=L3_ROOT,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"L3 step failed: {name} "
            f"(exit code {result.returncode})"
        )

    print(f"L3 STEP PASS: {name}")


def runtime_audit(l2_path: Path, l3_path: Path) -> None:
    l2 = sqlite3.connect(
        l2_path.resolve().as_uri() + "?mode=ro",
        uri=True,
    )
    l3 = sqlite3.connect(l3_path)

    try:
        integrity = l3.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = l3.execute("PRAGMA foreign_key_check").fetchall()
        schema = l3.execute("PRAGMA user_version").fetchone()[0]

        frontier = l2.execute(
            "SELECT COALESCE(MAX(id),0) FROM raw_record_observations"
        ).fetchone()[0]

        current_features = l3.execute(
            "SELECT COUNT(*) FROM derived_features WHERE status='CURRENT'"
        ).fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                f"L3 integrity_check failed: {integrity}"
            )

        if fk_errors:
            raise RuntimeError(
                f"L3 foreign_key_check failed: {len(fk_errors)} errors"
            )

        if schema != 8:
            raise RuntimeError(
                f"L3 schema version must be 8, found {schema}"
            )

        if current_features <= 0:
            raise RuntimeError(
                "L3 has no CURRENT derived features"
            )

        required_pipelines = []

        for filename in (
            "heart_rate_v0_1.json",
            "spo2_v0_1.json",
            "xiaomi_stress_score_v0_1.json",
            "resting_heart_rate_v0_1.json",
            "steps_v0_1.json",
            "calories_v0_1.json",
            "sleep_v0_1.json",
        ):
            payload = json.loads(
                (DEFS / "normalizers" / filename).read_text(
                    encoding="utf-8-sig"
                )
            )
            required_pipelines.append(payload["definition_id"])

        required_pipelines.extend(
            (
                "l3b.quality_resolution",
                "l3c.derived_features",
            )
        )

        lagging = []

        for pipeline in required_pipelines:
            row = l3.execute(
                """
                SELECT last_l2_observation_id
                FROM processing_checkpoints
                WHERE pipeline_name=?
                """,
                (pipeline,),
            ).fetchone()

            if row is None or row[0] != frontier:
                lagging.append(
                    {
                        "pipeline": pipeline,
                        "checkpoint": None if row is None else row[0],
                        "frontier": frontier,
                    }
                )

        if lagging:
            raise RuntimeError(
                "L3 checkpoint audit failed: "
                + json.dumps(lagging, ensure_ascii=False)
            )

        print()
        print("L3 RUNTIME AUDIT = PASS")
        print(f"schema_version = {schema}")
        print(f"l2_frontier = {frontier}")
        print(f"current_features = {current_features}")
        print("foreign_key_errors = 0")
        print("all_pipeline_checkpoints = CURRENT")

    finally:
        l2.close()
        l3.close()


def main() -> int:
    python = sys.executable

    l2 = Path(require_env("PHE_L2_DB"))
    l3 = Path(require_env("PHE_L3_DB"))

    point_runner = SCRIPTS / "l3_point_incremental_runner_v0_1.py"
    point_core = SCRIPTS / "l3_point_core_v0_1.py"

    # POINT
    for label, definition in (
        ("heart_rate", "heart_rate_v0_1.json"),
        ("spo2", "spo2_v0_1.json"),
        ("stress", "xiaomi_stress_score_v0_1.json"),
    ):
        run_step(
            f"POINT {label}",
            [
                python,
                str(point_runner),
                "--l2", str(l2),
                "--l3", str(l3),
                "--definition",
                str(DEFS / "normalizers" / definition),
                "--core", str(point_core),
            ],
        )

    # DAILY
    run_step(
        "DAILY resting_heart_rate",
        [
            python,
            str(SCRIPTS / "l3_daily_full_runner_v0_1.py"),
            "--l2", str(l2),
            "--l3", str(l3),
            "--definition",
            str(
                DEFS
                / "normalizers"
                / "resting_heart_rate_v0_1.json"
            ),
        ],
    )

    # BUCKET
    for label in ("steps", "calories"):
        run_step(
            f"BUCKET {label}",
            [
                python,
                str(
                    SCRIPTS
                    / "l3_bucket_incremental_runner_v0_1.py"
                ),
                "--l2", str(l2),
                "--l3", str(l3),
                "--definition",
                str(
                    DEFS
                    / "normalizers"
                    / f"{label}_v0_1.json"
                ),
            ],
        )

    # SLEEP
    run_step(
        "SLEEP",
        [
            python,
            str(
                SCRIPTS
                / "l3_sleep_incremental_runner_v0_1.py"
            ),
            "--l2", str(l2),
            "--l3", str(l3),
            "--definition",
            str(DEFS / "normalizers" / "sleep_v0_1.json"),
        ],
    )

    # L3B
    run_step(
        "L3B quality + source resolution",
        [
            python,
            str(SCRIPTS / "l3b_materializer_v0_1.py"),
            "--mode", "incremental",
            "--l2", str(l2),
            "--l3", str(l3),
            "--quality-definition",
            str(
                DEFS
                / "quality"
                / "l3b_structural_quality_v0_1.json"
            ),
            "--resolution-definition",
            str(
                DEFS
                / "resolution"
                / "l3b_source_resolution_v0_1.json"
            ),
        ],
    )

    # L3C
    run_step(
        "L3C derived features",
        [
            python,
            str(SCRIPTS / "l3c_materializer_v0_1.py"),
            "--mode", "incremental",
            "--l2", str(l2),
            "--l3", str(l3),
            "--definition",
            str(
                DEFS
                / "features"
                / "daily_features_v0_1.json"
            ),
        ],
    )

    runtime_audit(l2, l3)

    print()
    print("=" * 72)
    print("L3 PRODUCTION PIPELINE = PASS")
    print("=" * 72)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print("L3 PRODUCTION PIPELINE = FAIL")
        print(f"ERROR = {exc}", file=sys.stderr)
        raise

