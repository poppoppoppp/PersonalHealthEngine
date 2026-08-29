"""Production wrapper for the SEALED Xiaomi L1 collector."""

from __future__ import annotations

from datetime import datetime, timedelta

import asyncio
import os
import sys
from pathlib import Path

import keyring


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

L1_CODE = (
    REPO
    / "PersonalHealthEngine-L1Lab"
    / "xiaomi-raw-collector"
)

sys.path.insert(0, str(L1_CODE))

from file_secret_keyring import FileSecretKeyring  # noqa: E402
import collector  # noqa: E402


def require_env(name: str) -> str:
    value = os.environ.get(name)

    if not value:
        raise RuntimeError(
            f"required environment variable is missing: {name}"
        )

    return value


def main() -> int:
    captures = Path(require_env("PHE_L1_CAPTURES"))
    state = Path(require_env("PHE_L1_STATE"))

    captures.mkdir(parents=True, exist_ok=True)
    state.parent.mkdir(parents=True, exist_ok=True)

    # Keep SEALED auth.py unchanged.
    # Its normal keyring.get_password() calls are redirected here.
    keyring.set_keyring(FileSecretKeyring())

    # Xiaomi cloud quirks (observed 2026-08-29):
    # 1. Heart-rate/SpO2/stress sample datasets return today's samples only when the
    #    query's end_date extends into tomorrow (chunked storage ignores an end at
    #    today midnight; summary datasets like steps are unaffected).
    # 2. Their samples also live in chunks outside a 2-day window, so overlap 8 days
    #    keeps those queries non-empty. L2 dedupes re-pulled records by logical key,
    #    so both the extra overlap and the tomorrow end date are idempotent.
    from datetime import timedelta

    end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    sys.argv = [
        str(L1_CODE / "collector.py"),
        "--output-dir",
        str(captures),
        "--state-file",
        str(state),
        "--end-date",
        end_date,
        "--overlap-days",
        "8",
    ]

    print("========== L1 PRODUCTION COLLECTOR ==========")
    print(f"captures = {captures}")
    print(f"state    = {state}")

    args = collector.parse_args()
    result = asyncio.run(collector.run(args))

    if result != 0:
        raise RuntimeError(
            f"L1 collector failed with exit code {result}"
        )

    print()
    print("L1 PRODUCTION COLLECTOR = PASS")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print("L1 PRODUCTION COLLECTOR = FAIL")
        print(f"ERROR = {exc}", file=sys.stderr)
        raise
