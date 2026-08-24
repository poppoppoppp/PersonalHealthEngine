"""Measure production Q&A paths without emitting prompts, tokens, or answers."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.request


QUESTIONS = {
    "product_meta": "这个产品能做什么？",
    "health_data": "我最近七天的静息心率平均值是多少？",
    "medical_decision": "结合我最近的睡眠和静息心率变化，我现在应该怎么做？",
}


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def post(base_url: str, token: str, question: str) -> tuple[float, dict]:
    payload = json.dumps({"question": question}).encode()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/qna/ask",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=900) as response:
        result = json.load(response)
    return (time.perf_counter() - started) * 1000, result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8707")
    parser.add_argument("--samples", type=int, default=2)
    args = parser.parse_args()
    token = os.environ["L7_API_TOKEN"]
    report: dict[str, dict] = {}
    for name, question in QUESTIONS.items():
        timings = []
        routes = []
        for _ in range(args.samples):
            elapsed, result = post(args.base_url, token, question)
            timings.append(round(elapsed, 2))
            routes.append(result.get("route", "unknown"))
        report[name] = {
            "samples_ms": timings,
            "median_ms": round(statistics.median(timings), 2),
            "p95_ms": round(percentile(timings, 0.95), 2),
            "routes": routes,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
