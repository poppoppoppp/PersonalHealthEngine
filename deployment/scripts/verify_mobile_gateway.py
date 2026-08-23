"""Verify the deployed PHE HTTPS gateway without printing credentials or payloads."""

from __future__ import annotations

import json
import os
import ssl
import urllib.request


BASE = os.environ.get("PHE_MOBILE_BASE_URL", "https://47.111.229.39").rstrip("/")


def request(path: str, token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as res:
        if res.status != 200:
            raise RuntimeError(f"{path} returned HTTP {res.status}")
        payload = json.load(res)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} did not return a JSON object")
    return payload


def main() -> None:
    token = os.environ.get("L7_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("L7_API_TOKEN is required in the process environment")
    assert request("/health").get("status") == "ok"
    today = request("/today", token)
    if today.get("schema") != "l7.today/v1":
        raise RuntimeError("Today schema mismatch")
    for path in ("/history/episodes", "/patterns", "/settings"):
        request(path, token)
    print("PHE_MOBILE_GATEWAY_API=PASS")


if __name__ == "__main__":
    main()
