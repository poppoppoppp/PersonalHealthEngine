"""Production daily pipeline for Personal Health Engine.

Runs L1 -> L2 -> L3 -> L4 -> L5 strictly in order.

Linux production behavior:
- daily.lock prevents duplicate daily pipeline instances
- pipeline.lock is the shared consistency gate with L7
- fail fast
- downstream stages never run after an upstream failure
- locks are released automatically when the process exits
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent


def require_env(name: str) -> str:
    value = os.environ.get(name)

    if not value:
        raise RuntimeError(
            f"required environment variable is missing: {name}"
        )

    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_stage(name: str, script: str) -> None:
    path = HERE / script

    if not path.exists():
        raise RuntimeError(
            f"{name} script does not exist: {path}"
        )

    print()
    print("=" * 72)
    print(f"STAGE {name} START")
    print(f"time = {utc_now()}")
    print("=" * 72)

    result = subprocess.run(
        [sys.executable, str(path)],
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"{name} failed with exit code "
            f"{result.returncode}"
        )

    print()
    print(f"STAGE {name} = PASS")


def main() -> int:
    runtime = Path(require_env("PHE_RUNTIME"))
    runtime.mkdir(parents=True, exist_ok=True)

    instance_lock_path = runtime / "daily.lock"
    gate_lock_path = runtime / "pipeline.lock"

    instance_lock = instance_lock_path.open("a+")
    gate_lock = None

    try:
        # This lock is only for duplicate daily jobs.
        try:
            fcntl.flock(
                instance_lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            print(
                "DAILY PIPELINE = SKIPPED "
                "(another daily pipeline is already running)"
            )
            return 75

        # This is the consistency gate shared with L7.
        #
        # Blocking is intentional:
        # if an L7 request is already using the databases,
        # wait for that request to finish rather than skipping
        # the entire daily refresh.
        gate_lock = gate_lock_path.open("a+")

        print("waiting_for_pipeline_gate = true")

        fcntl.flock(
            gate_lock.fileno(),
            fcntl.LOCK_EX,
        )

        print("waiting_for_pipeline_gate = false")

        print("========== PERSONAL HEALTH ENGINE DAILY ==========")
        print(f"started_at_utc = {utc_now()}")
        print(f"instance_lock = {instance_lock_path}")
        print(f"pipeline_gate = {gate_lock_path}")

        stages = [
            ("L1", "run_l1_collector.py"),
            ("L2", "run_l2_import.py"),
            ("L3", "run_l3_pipeline.py"),
            ("L4", "run_l4_pipeline.py"),
            ("L5", "run_l5_pipeline.py"),
        ]

        for name, script in stages:
            run_stage(name, script)

        print()
        print("==================================================")
        print("DAILY PIPELINE = PASS")
        print(f"finished_at_utc = {utc_now()}")
        print("completed_stages = L1,L2,L3,L4,L5")
        print("==================================================")

        return 0

    finally:
        if gate_lock is not None:
            try:
                fcntl.flock(
                    gate_lock.fileno(),
                    fcntl.LOCK_UN,
                )
            finally:
                gate_lock.close()

        try:
            fcntl.flock(
                instance_lock.fileno(),
                fcntl.LOCK_UN,
            )
        finally:
            instance_lock.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print("DAILY PIPELINE = FAIL")
        print(f"ERROR = {exc}", file=sys.stderr)
        raise
