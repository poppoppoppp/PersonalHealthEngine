import argparse
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def source_class(source_sid):
    return (
        "XIAOMI_GENERATED"
        if str(source_sid).startswith("hlth.gen_")
        else "NUMERIC_SOURCE"
    )


def decode(raw_json):
    outer = json.loads(raw_json)
    value = outer.get("value")
    return json.loads(value) if isinstance(value, str) else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--definition", required=True)
    args = parser.parse_args()

    definition_bytes = Path(args.definition).read_bytes()
    definition = json.loads(definition_bytes.decode("utf-8-sig"))
    if definition["temporal_type"] != "DAILY":
        raise ValueError("daily runner requires a DAILY definition")
    definition_hash = hashlib.sha256(definition_bytes).hexdigest()
    definition_id = definition["definition_id"]
    definition_version = definition["definition_version"]
    dataset = definition["dataset"]
    metric = definition["metric"]
    pipeline = definition_id

    l2 = sqlite3.connect(readonly_uri(args.l2), uri=True)
    l2.row_factory = sqlite3.Row
    l3 = sqlite3.connect(args.l3)
    l3.row_factory = sqlite3.Row
    l3.execute("PRAGMA foreign_keys = ON")
    run_id = (
        f"{dataset}-daily-full-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    try:
        l2.execute("BEGIN")
        frontier = l2.execute(
            "SELECT COALESCE(MAX(id),0) FROM raw_record_observations"
        ).fetchone()[0]
        rows = l2.execute(
            """
            WITH latest AS (
                SELECT logical_record_id,MAX(id) raw_version_id
                FROM raw_record_versions GROUP BY logical_record_id
            )
            SELECT lr.id logical_record_id,lr.provider,lr.raw_sid,lr.raw_time,
                   rv.id raw_version_id,rv.raw_json,rv.zone_name,rv.zone_offset
            FROM logical_records lr
            JOIN latest ON latest.logical_record_id=lr.id
            JOIN raw_record_versions rv ON rv.id=latest.raw_version_id
            WHERE lr.dataset=? ORDER BY lr.id
            """,
            (dataset,),
        ).fetchall()
        l3.execute("BEGIN IMMEDIATE")
        registered = l3.execute(
            """
            SELECT definition_sha256 FROM definition_registry
            WHERE definition_id=? AND definition_version=?
            """,
            (definition_id, definition_version),
        ).fetchone()
        if registered is None:
            l3.execute(
                """
                INSERT INTO definition_registry (
                    definition_id,definition_version,definition_type,status,
                    definition_sha256,registered_at_utc,notes
                ) VALUES (?,?,'NORMALIZER','ACTIVE',?,?,?)
                """,
                (
                    definition_id,
                    definition_version,
                    definition_hash,
                    utc_now(),
                    f"{metric} DAILY normalizer",
                ),
            )
        elif registered[0] != definition_hash:
            raise ValueError("daily definition checksum mismatch")

        inserted = skipped = superseded = 0
        for row in rows:
            value = decode(row["raw_json"])
            date_anchor = int(value["date_time"])
            if int(row["raw_time"]) != date_anchor:
                raise ValueError(
                    f"logical {row['logical_record_id']}: raw_time != date_time"
                )
            local_date = datetime.fromtimestamp(
                date_anchor, tz=timezone.utc
            ).date().isoformat()
            value_num = float(value["bpm"])
            attributes = json.dumps(
                {"xiaomi_date_anchor_epoch": date_anchor},
                sort_keys=True,
                separators=(",", ":"),
            )
            expected = (
                local_date,
                value_num,
                definition["unit"],
                row["provider"],
                str(row["raw_sid"]),
                source_class(row["raw_sid"]),
                row["zone_name"],
                row["zone_offset"],
                attributes,
                row["raw_version_id"],
            )
            existing = l3.execute(
                """
                SELECT fr.id,x.local_date,x.value_num,x.unit,x.provider,
                       x.source_sid,x.source_class,x.timezone_name,
                       x.timezone_offset_seconds,x.attributes_json,
                       fp.l2_raw_version_id
                FROM fact_registry fr JOIN normalized_daily_facts x ON x.fact_id=fr.id
                JOIN fact_provenance fp ON fp.fact_id=fr.id
                WHERE fr.status='CURRENT' AND fr.definition_id=?
                  AND fr.definition_version=? AND fp.l2_logical_record_id=?
                """,
                (definition_id, definition_version, row["logical_record_id"]),
            ).fetchall()
            existing_signatures = {
                tuple(item[key] for key in item.keys() if key != "id")
                for item in existing
            }
            if len(existing) == 1 and existing_signatures == {expected}:
                skipped += 1
                continue
            now = utc_now()
            if existing:
                ids = [item["id"] for item in existing]
                marks = ",".join("?" for _ in ids)
                l3.execute(
                    f"UPDATE fact_registry SET status='STALE',updated_at_utc=? WHERE id IN ({marks})",
                    (now, *ids),
                )
                superseded += len(ids)
            cursor = l3.execute(
                """
                INSERT INTO fact_registry (
                    fact_kind,metric,evidence_type,definition_id,definition_version,
                    status,created_at_utc,updated_at_utc
                ) VALUES ('DAILY',?,?,?,?,'CURRENT',?,?)
                """,
                (
                    metric,
                    definition["evidence_type"],
                    definition_id,
                    definition_version,
                    now,
                    now,
                ),
            )
            fact_id = cursor.lastrowid
            l3.execute(
                """
                INSERT INTO normalized_daily_facts VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    fact_id,local_date,value_num,None,definition["unit"],
                    row["provider"],str(row["raw_sid"]),source_class(row["raw_sid"]),
                    row["zone_name"],row["zone_offset"],attributes,
                ),
            )
            l3.execute(
                """
                INSERT INTO fact_provenance VALUES (?,?,?,'SOURCE',?)
                """,
                (
                    fact_id,row["logical_record_id"],row["raw_version_id"],now,
                ),
            )
            inserted += 1

        current = l3.execute(
            "SELECT COUNT(*) FROM fact_registry WHERE status='CURRENT' AND definition_id=?",
            (definition_id,),
        ).fetchone()[0]
        if current != len(rows):
            raise RuntimeError(f"daily coverage mismatch: {current} != {len(rows)}")
        now = utc_now()
        result = {
            "status": "PASS",
            "run_id": run_id,
            "dataset": dataset,
            "current_facts": current,
            "inserted": inserted,
            "skipped": skipped,
            "superseded": superseded,
            "checkpoint": frontier,
        }
        l3.execute(
            """
            INSERT INTO pipeline_runs (
                run_id,mode,status,source_l2_path,source_schema_version,
                started_at_utc,finished_at_utc,details_json
            ) VALUES (?,'FULL_REBUILD','PASS',?,?,?,?,?)
            """,
            (
                run_id,str(Path(args.l2).resolve()),
                l2.execute("PRAGMA user_version").fetchone()[0],now,now,
                json.dumps(result, separators=(",", ":")),
            ),
        )
        l3.execute(
            """
            INSERT INTO processing_checkpoints VALUES (?,?,?,?)
            ON CONFLICT(pipeline_name) DO UPDATE SET
                last_l2_observation_id=excluded.last_l2_observation_id,
                last_successful_run_id=excluded.last_successful_run_id,
                updated_at_utc=excluded.updated_at_utc
            """,
            (pipeline,frontier,run_id,now),
        )
        if l3.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("daily foreign key check failed")
        l3.commit()
        print(json.dumps(result, indent=2))
    except Exception:
        l3.rollback()
        raise
    finally:
        l2.close()
        l3.close()


if __name__ == "__main__":
    main()
