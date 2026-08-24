"""Probe authenticated read latency while one real medical review is running."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import time
import urllib.request


READ_PATHS = ("/health", "/today", "/patterns", "/history/episodes?limit=30", "/context?limit=30")


def request(base_url: str, token: str, path: str, payload: dict | None = None) -> float:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=900) as response:
        response.read()
    return (time.perf_counter() - started) * 1000


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8707")
    parser.add_argument("--rounds", type=int, default=12)
    args = parser.parse_args()
    token = os.environ["L7_API_TOKEN"]
    medical = {"question": "结合我最近的睡眠和静息心率变化，我现在应该怎么做？"}
    results = {path: [] for path in READ_PATHS}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        inference = pool.submit(request, args.base_url, token, "/qna/ask", medical)
        for _ in range(args.rounds):
            for path in READ_PATHS:
                results[path].append(request(args.base_url, token, path))
        inference_ms = inference.result()
    report = {
        "inference_ms": round(inference_ms, 2),
        "reads": {
            path: {
                "median_ms": round(statistics.median(values), 2),
                "p95_ms": round(p95(values), 2),
            }
            for path, values in results.items()
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
