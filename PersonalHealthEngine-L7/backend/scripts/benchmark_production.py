"""Measure production endpoint latency and sizes without printing response bodies."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://47.111.229.39")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("paths", nargs="*", default=[
        "/health", "/today", "/history/episodes", "/patterns", "/context",
    ])
    args = parser.parse_args()
    token = os.environ.get("L7_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("L7_API_TOKEN is required")
    result = {}
    for path in args.paths:
        durations = []
        sizes = []
        for _ in range(args.samples):
            request = urllib.request.Request(
                args.base.rstrip("/") + path,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            started = time.perf_counter()
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
            durations.append((time.perf_counter() - started) * 1000)
            sizes.append(len(body))
        ordered = sorted(durations)
        result[path] = {
            "samples": len(ordered),
            "p50_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[max(0, int(len(ordered) * .95 + .999) - 1)], 3),
            "wire_bytes_median": int(statistics.median(sizes)),
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
