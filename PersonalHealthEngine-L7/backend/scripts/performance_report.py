"""Emit aggregate performance telemetry without request or health content."""

from __future__ import annotations

import argparse
import json
import sqlite3

from l7.performance import summarize_request_metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    args = parser.parse_args()
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    try:
        endpoints = [row[0] for row in con.execute(
            "SELECT DISTINCT endpoint FROM performance_requests ORDER BY endpoint"
        )]
        report = {
            endpoint: summarize_request_metrics(con, endpoint=endpoint)
            for endpoint in endpoints
        }
    finally:
        con.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
