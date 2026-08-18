"""Production daily pipeline for Personal Health Engine.

Runs L1 -> L2 -> L3 -> L4 -> L5 strictly in order.

Linux production behavior:
- one process at a time via fcntl.flock
- fail fast
- downstream stages never run after an upstream failure
- lock is released automatically when the process exits
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

    lock_path = runtime / "pipeline.lock"

    # Keep this file permanent.
    # flock belongs to the open file descriptor, not the file's existence.
    lock_file = lock_path.open("a+")

    try:
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            print(
                "DAILY PIPELINE = SKIPPED "
                "(another pipeline run holds the lock)"
            )
            return 75

        print("========== PERSONAL HEALTH ENGINE DAILY ==========")
        print(f"started_at_utc = {utc_now()}")
        print(f"lock = {lock_path}")

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
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_UN,
            )
        finally:
            lock_file.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print("DAILY PIPELINE = FAIL")
        print(f"ERROR = {exc}", file=sys.stderr)
        raise
