import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from l3_sleep_core_v0_1 import (
    DEFINITION_ID,
    load_definition,
    materialize_fact_set,
    normalize_sleep_row,
    register_definition,
    utc_now,
)


PIPELINE = "normalize.sleep"


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--definition", required=True)
    args = parser.parse_args()

    definition, definition_sha256 = load_definition(args.definition)
    l2 = sqlite3.connect(readonly_uri(args.l2), uri=True)
    l2.row_factory = sqlite3.Row
    l3 = sqlite3.connect(args.l3)
    l3.row_factory = sqlite3.Row
    l3.execute("PRAGMA foreign_keys = ON")
    run_id = (
        "sleep-full-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    try:
        if l3.execute("PRAGMA user_version").fetchone()[0] < 6:
            raise RuntimeError("Sleep full runner requires L3 schema >= 6")
        l2.execute("BEGIN")
        frontier = l2.execute(
            "SELECT COALESCE(MAX(id),0) FROM raw_record_observations"
        ).fetchone()[0]
        rows = l2.execute(
            """
            WITH latest AS (
                SELECT logical_record_id, MAX(id) raw_version_id
                FROM raw_record_versions GROUP BY logical_record_id
            )
            SELECT lr.id logical_record_id, lr.provider, lr.raw_sid, lr.raw_time,
                   rv.id raw_version_id, rv.raw_json, rv.zone_name, rv.zone_offset
            FROM logical_records lr
            JOIN latest ON latest.logical_record_id=lr.id
            JOIN raw_record_versions rv ON rv.id=latest.raw_version_id
            WHERE lr.dataset='sleep'
            ORDER BY lr.id
            """
        ).fetchall()

        expected_total = 0
        totals = {"inserted": 0, "superseded": 0, "skipped": 0}
        l3.execute("BEGIN IMMEDIATE")
        register_definition(l3, definition_sha256)
        l3.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, mode, status, source_l2_path, source_schema_version,
                started_at_utc, details_json
            ) VALUES (?,'FULL_REBUILD','RUNNING',?,?,?,?)
            """,
            (
                run_id,
                str(Path(args.l2).resolve()),
                l2.execute("PRAGMA user_version").fetchone()[0],
                utc_now(),
                json.dumps({"dataset": "sleep", "logical_records": len(rows)}),
            ),
        )
        for row in rows:
            facts = normalize_sleep_row(row, definition)
            expected_total += len(facts)
            result = materialize_fact_set(
                l3, row["logical_record_id"], row["raw_version_id"], facts
            )
            for name in totals:
                totals[name] += result[name]

        current_total = l3.execute(
            """
            SELECT COUNT(*) FROM fact_registry
            WHERE status='CURRENT' AND definition_id=?
              AND definition_version='0.1'
            """,
            (DEFINITION_ID,),
        ).fetchone()[0]
        if current_total != expected_total:
            raise RuntimeError(
                f"Sleep current fact mismatch: {current_total} != {expected_total}"
            )
        if l3.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("foreign key check failed")

        finished = utc_now()
        details = {
            "status": "PASS",
            "run_id": run_id,
            "logical_records": len(rows),
            "current_facts": current_total,
            "checkpoint": frontier,
            **totals,
        }
        l3.execute(
            """
            INSERT INTO processing_checkpoints (
                pipeline_name, last_l2_observation_id,
                last_successful_run_id, updated_at_utc
            ) VALUES (?,?,?,?)
            ON CONFLICT(pipeline_name) DO UPDATE SET
                last_l2_observation_id=excluded.last_l2_observation_id,
                last_successful_run_id=excluded.last_successful_run_id,
                updated_at_utc=excluded.updated_at_utc
            """,
            (PIPELINE, frontier, run_id, finished),
        )
        l3.execute(
            """
            UPDATE pipeline_runs
            SET status='PASS', finished_at_utc=?, details_json=? WHERE run_id=?
            """,
            (finished, json.dumps(details, separators=(",", ":")), run_id),
        )
        l3.commit()
        print(json.dumps(details, indent=2))
    except Exception:
        l3.rollback()
        raise
    finally:
        l2.close()
        l3.close()


if __name__ == "__main__":
    main()
