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
        "sleep-incremental-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    try:
        if l3.execute("PRAGMA user_version").fetchone()[0] < 6:
            raise RuntimeError("Sleep incremental runner requires L3 schema >= 6")
        checkpoint_row = l3.execute(
            "SELECT last_l2_observation_id FROM processing_checkpoints WHERE pipeline_name=?",
            (PIPELINE,),
        ).fetchone()
        if checkpoint_row is None:
            raise RuntimeError("normalize.sleep checkpoint is missing; run full build first")
        checkpoint = checkpoint_row[0]

        l2.execute("BEGIN")
        frontier = l2.execute(
            "SELECT COALESCE(MAX(id),0) FROM raw_record_observations"
        ).fetchone()[0]
        observations = l2.execute(
            """
            SELECT o.id observation_id, o.classification,
                   lr.id logical_record_id, lr.provider, lr.raw_sid, lr.raw_time,
                   rv.id raw_version_id, rv.raw_json, rv.zone_name, rv.zone_offset
            FROM raw_record_observations o
            JOIN raw_record_versions rv ON rv.id=o.raw_record_version_id
            JOIN logical_records lr ON lr.id=rv.logical_record_id
            WHERE o.id>? AND o.id<=? AND o.dataset='sleep'
            ORDER BY o.id
            """,
            (checkpoint, frontier),
        ).fetchall()

        counts = {
            "NEW": 0,
            "REVISION": 0,
            "REOBSERVATION": 0,
            "inserted": 0,
            "superseded": 0,
            "skipped": 0,
        }
        l3.execute("BEGIN IMMEDIATE")
        register_definition(l3, definition_sha256)
        l3.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, mode, status, source_l2_path, source_schema_version,
                started_at_utc, details_json
            ) VALUES (?,'INCREMENTAL','RUNNING',?,?,?,?)
            """,
            (
                run_id,
                str(Path(args.l2).resolve()),
                l2.execute("PRAGMA user_version").fetchone()[0],
                utc_now(),
                json.dumps({"dataset": "sleep", "from_checkpoint": checkpoint}),
            ),
        )

        for row in observations:
            classification = row["classification"]
            counts[classification] += 1
            if classification == "REOBSERVATION":
                continue
            if classification not in ("NEW", "REVISION"):
                raise RuntimeError(f"unsupported classification {classification}")
            facts = normalize_sleep_row(row, definition)
            result = materialize_fact_set(
                l3, row["logical_record_id"], row["raw_version_id"], facts
            )
            for name in ("inserted", "superseded", "skipped"):
                counts[name] += result[name]

        finished = utc_now()
        details = {
            **counts,
            "observations": len(observations),
            "from_checkpoint": checkpoint,
            "checkpoint": frontier,
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
            SET status='PASS', finished_at_utc=?, details_json=?
            WHERE run_id=?
            """,
            (finished, json.dumps(details, separators=(",", ":")), run_id),
        )
        if l3.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("foreign key check failed")
        l3.commit()
        print(json.dumps({"status": "PASS", "run_id": run_id, **details}, indent=2))
    except Exception:
        l3.rollback()
        raise
    finally:
        l2.close()
        l3.close()


if __name__ == "__main__":
    main()
