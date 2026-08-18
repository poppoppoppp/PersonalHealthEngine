"""Trigger L7 Today refresh after a successful L1-L5 daily pipeline."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


URL = "http://127.0.0.1:8707/today/refresh"


def require_env(name: str) -> str:
    value = os.environ.get(name)

    if not value:
        raise RuntimeError(
            f"required environment variable is missing: {name}"
        )

    return value


def main() -> int:
    token = require_env("L7_API_TOKEN")

    request = urllib.request.Request(
        URL,
        data=b"",
        method="POST",
    )

    request.add_header(
        "Authorization",
        f"Bearer {token}",
    )

    request.add_header(
        "Content-Type",
        "application/json",
    )

    print("========== L7 POST-PIPELINE REFRESH ==========")

    try:
        with urllib.request.urlopen(
            request,
            timeout=600,
        ) as response:
            body = response.read().decode("utf-8")

    except urllib.error.HTTPError as exc:
        body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"L7 refresh HTTP {exc.code}: {body[:500]}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"L7 refresh unavailable: {exc.reason}"
        ) from exc

    payload = json.loads(body)

    print(
        "outcome =",
        payload.get("outcome"),
    )

    print(
        "model_calls =",
        payload.get("model_calls"),
    )

    print(
        "judgment_updated =",
        payload.get("judgment_updated"),
    )

    print("L7 POST-PIPELINE REFRESH = PASS")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print("L7 POST-PIPELINE REFRESH = FAIL")
        print(f"ERROR = {exc}", file=sys.stderr)
        raise
