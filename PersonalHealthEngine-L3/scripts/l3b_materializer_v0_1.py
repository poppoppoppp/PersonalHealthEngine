import argparse
import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


QUALITY_ID = "l3b.quality.structural"
RESOLUTION_ID = "l3b.resolution.source"
VERSION = "0.1"
PIPELINE = "l3b.quality_resolution"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def load_definition(path, expected_id):
    raw = Path(path).read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    if payload.get("definition_id") != expected_id:
        raise ValueError(f"unexpected definition_id in {path}")
    if payload.get("definition_version") != VERSION:
        raise ValueError(f"unexpected definition_version in {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def register_definition(db, definition, checksum):
    definition_id = definition["definition_id"]
    row = db.execute(
        """
        SELECT definition_sha256 FROM definition_registry
        WHERE definition_id=? AND definition_version=?
        """,
        (definition_id, VERSION),
    ).fetchone()
    if row is None:
        db.execute(
            """
            INSERT INTO definition_registry (
                definition_id,definition_version,definition_type,status,
                definition_sha256,registered_at_utc,notes
            ) VALUES (?,? ,?,'ACTIVE',?,?,?)
            """,
            (
                definition_id,
                VERSION,
                definition["definition_type"],
                checksum,
                utc_now(),
                "Conservative Layer 3B rule",
            ),
        )
    elif row[0] != checksum:
        raise ValueError(f"definition checksum mismatch: {definition_id}")


def current_facts(db):
    table_specs = (
        (
            "normalized_point_facts",
            "x.event_time_utc",
            "NULL",
            "NULL",
        ),
        (
            "normalized_daily_facts",
            "x.local_date",
            "NULL",
            "NULL",
        ),
        (
            "normalized_bucket_facts",
            "x.bucket_anchor_time_utc",
            "NULL",
            "NULL",
        ),
        (
            "normalized_interval_facts",
            "x.start_time_utc",
            "x.end_time_utc",
            "x.duration_seconds",
        ),
    )
    facts = []
    for table, primary_time, secondary_time, duration in table_specs:
        facts.extend(
            dict(row)
            for row in db.execute(
                f"""
                SELECT fr.id fact_id,fr.metric,fr.fact_kind,fr.evidence_type,
                       x.provider,x.source_sid,x.source_class,x.timezone_name,
                       x.timezone_offset_seconds,{primary_time} primary_time,
                       {secondary_time} secondary_time,{duration} duration_seconds,
                       x.value_num,x.value_code,x.unit,x.attributes_json,
                       COUNT(fp.l2_raw_version_id) provenance_count
                FROM fact_registry fr JOIN {table} x ON x.fact_id=fr.id
                LEFT JOIN fact_provenance fp ON fp.fact_id=fr.id
                WHERE fr.status='CURRENT'
                GROUP BY fr.id
                ORDER BY fr.id
                """
            )
        )
    facts.sort(key=lambda fact: fact["fact_id"])
    return facts


def desired_quality(facts):
    desired = []
    for fact in facts:
        structurally_valid = bool(fact["primary_time"])
        if fact["fact_kind"] == "INTERVAL":
            structurally_valid = (
                structurally_valid
                and bool(fact["secondary_time"])
                and fact["duration_seconds"] is not None
                and fact["duration_seconds"] >= 0
                and fact["secondary_time"] >= fact["primary_time"]
            )
        structural_details = json.dumps(
            {
                "duration_seconds": fact["duration_seconds"],
                "fact_kind": fact["fact_kind"],
                "zero_duration_valid": fact["duration_seconds"] == 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        desired.append(
            (
                fact["fact_id"],
                fact["metric"],
                "STRUCTURAL_VALIDITY",
                "PASS" if structurally_valid else "FLAGGED",
                "STRUCTURE_VALID" if structurally_valid else "STRUCTURE_INVALID",
                structural_details,
                ((fact["fact_id"], "SUBJECT"),),
            )
        )

        provenance_complete = fact["provenance_count"] > 0
        desired.append(
            (
                fact["fact_id"],
                fact["metric"],
                "PROVENANCE_COMPLETENESS",
                "PASS" if provenance_complete else "FLAGGED",
                "PROVENANCE_PRESENT" if provenance_complete else "PROVENANCE_MISSING",
                json.dumps(
                    {"provenance_count": fact["provenance_count"]},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                ((fact["fact_id"], "SUBJECT"),),
            )
        )

        vendor_uncertain = fact["evidence_type"] in (
            "VENDOR_DERIVED",
            "VENDOR_INFERRED",
        )
        desired.append(
            (
                fact["fact_id"],
                fact["metric"],
                "VENDOR_SEMANTIC_CERTAINTY",
                "UNKNOWN" if vendor_uncertain else "PASS",
                "VENDOR_SEMANTICS_UNCERTAIN"
                if vendor_uncertain
                else "SENSOR_OBSERVATION_SEMANTICS_DECLARED",
                json.dumps(
                    {
                        "evidence_type": fact["evidence_type"],
                        "not_a_clinical_assessment": True,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                ((fact["fact_id"], "SUBJECT"),),
            )
        )
    return desired


def component(fact):
    if fact["fact_kind"] == "POINT":
        return "OBSERVATION_VALUE"
    if fact["fact_kind"] == "DAILY":
        return "DAILY_VENDOR_VALUE"
    if fact["fact_kind"] == "BUCKET":
        return "VENDOR_BUCKET_VALUE"
    if fact["metric"] == "sleep_source_episode":
        return "SOURCE_EPISODE"
    return "VENDOR_STAGE_SEGMENT"


def grouping_key(fact):
    return json.dumps(
        {
            "metric": fact["metric"],
            "primary_time": fact["primary_time"],
            "secondary_time": fact["secondary_time"],
            "timezone_name": fact["timezone_name"],
            "timezone_offset_seconds": fact["timezone_offset_seconds"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def desired_resolutions(facts):
    groups = defaultdict(list)
    for fact in facts:
        groups[(fact["metric"], component(fact), grouping_key(fact))].append(fact)

    desired = []
    for (metric, fact_component, key), members in sorted(groups.items()):
        source_classes = {member["source_class"] for member in members}
        value_signatures = {
            (member["value_num"], member["value_code"], member["unit"])
            for member in members
        }
        if len(source_classes) == 1:
            decision = "SINGLE_SOURCE"
            outcome = "SELECTED" if len(members) == 1 else "COEXIST"
            reason = "ONLY_ONE_SOURCE_CLASS_AVAILABLE"
            membership = "SELECTED" if len(members) == 1 else "RETAINED"
        elif len(value_signatures) == 1:
            decision = "AGREE"
            outcome = "COEXIST"
            reason = "MULTI_SOURCE_VALUES_AGREE"
            membership = "RETAINED"
        else:
            decision = "CONFLICT"
            outcome = "UNRESOLVED"
            reason = "MULTI_SOURCE_VALUES_DISAGREE"
            membership = "CONFLICTING"
        member_spec = tuple(
            sorted((member["fact_id"], membership) for member in members)
        )
        details = json.dumps(
            {
                "fact_count": len(members),
                "no_source_destroyed": True,
                "source_classes": sorted(source_classes),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        desired.append(
            (
                metric,
                fact_component,
                key,
                decision,
                outcome,
                reason,
                details,
                member_spec,
            )
        )
    return desired


def current_quality_signatures(db):
    inputs = defaultdict(list)
    for row in db.execute(
        """
        SELECT qai.assessment_id,qai.fact_id,qai.input_role
        FROM quality_assessment_inputs qai
        JOIN quality_assessments qa ON qa.id=qai.assessment_id
        WHERE qa.status='CURRENT'
        """
    ):
        inputs[row[0]].append((row[1], row[2]))
    return {
        (
            row["subject_fact_id"],
            row["metric"],
            row["quality_dimension"],
            row["result"],
            row["reason_code"],
            row["details_json"],
            tuple(sorted(inputs[row["id"]])),
        )
        for row in db.execute(
            "SELECT * FROM quality_assessments WHERE status='CURRENT'"
        )
    }


def current_resolution_signatures(db):
    inputs = defaultdict(list)
    for row in db.execute(
        """
        SELECT sri.decision_id,sri.fact_id,sri.membership_role
        FROM source_resolution_inputs sri
        JOIN source_resolution_decisions d ON d.id=sri.decision_id
        WHERE d.status='CURRENT'
        """
    ):
        inputs[row[0]].append((row[1], row[2]))
    return {
        (
            row["metric"],
            row["component"],
            row["grouping_key"],
            row["decision"],
            row["outcome"],
            row["reason_code"],
            row["details_json"],
            tuple(sorted(inputs[row["id"]])),
        )
        for row in db.execute(
            "SELECT * FROM source_resolution_decisions WHERE status='CURRENT'"
        )
    }


def materialize_quality(db, desired):
    expected = set(desired)
    current = current_quality_signatures(db)
    if current == expected:
        return 0, 0
    now = utc_now()
    stale = db.execute(
        "UPDATE quality_assessments SET status='STALE',updated_at_utc=? WHERE status='CURRENT'",
        (now,),
    ).rowcount
    for subject_id, metric, dimension, result, reason, details, inputs in desired:
        cursor = db.execute(
            """
            INSERT INTO quality_assessments (
                subject_fact_id,metric,quality_dimension,result,reason_code,
                definition_id,definition_version,status,details_json,
                created_at_utc,updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,'CURRENT',?,?,?)
            """,
            (
                subject_id,metric,dimension,result,reason,QUALITY_ID,VERSION,
                details,now,now,
            ),
        )
        for fact_id, role in inputs:
            db.execute(
                "INSERT INTO quality_assessment_inputs VALUES (?,?,?,?)",
                (cursor.lastrowid, fact_id, role, now),
            )
    return len(desired), stale


def materialize_resolutions(db, desired):
    expected = set(desired)
    current = current_resolution_signatures(db)
    if current == expected:
        return 0, 0
    now = utc_now()
    stale = db.execute(
        "UPDATE source_resolution_decisions SET status='STALE',updated_at_utc=? WHERE status='CURRENT'",
        (now,),
    ).rowcount
    for metric, fact_component, key, decision, outcome, reason, details, inputs in desired:
        cursor = db.execute(
            """
            INSERT INTO source_resolution_decisions (
                metric,component,grouping_key,decision,outcome,reason_code,
                definition_id,definition_version,status,details_json,
                created_at_utc,updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,'CURRENT',?,?,?)
            """,
            (
                metric,fact_component,key,decision,outcome,reason,
                RESOLUTION_ID,VERSION,details,now,now,
            ),
        )
        for fact_id, role in inputs:
            db.execute(
                "INSERT INTO source_resolution_inputs VALUES (?,?,?,?)",
                (cursor.lastrowid, fact_id, role, now),
            )
    return len(desired), stale


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("full", "incremental"), required=True)
    parser.add_argument("--l2", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--quality-definition", required=True)
    parser.add_argument("--resolution-definition", required=True)
    args = parser.parse_args()

    quality_definition, quality_hash = load_definition(
        args.quality_definition, QUALITY_ID
    )
    resolution_definition, resolution_hash = load_definition(
        args.resolution_definition, RESOLUTION_ID
    )
    l2 = sqlite3.connect(readonly_uri(args.l2), uri=True)
    l3 = sqlite3.connect(args.l3)
    l3.row_factory = sqlite3.Row
    l3.execute("PRAGMA foreign_keys = ON")
    run_id = (
        f"l3b-{args.mode}-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    try:
        if l3.execute("PRAGMA user_version").fetchone()[0] < 7:
            raise RuntimeError("L3B materializer requires schema >= 7")
        l2.execute("BEGIN")
        frontier = l2.execute(
            "SELECT COALESCE(MAX(id),0) FROM raw_record_observations"
        ).fetchone()[0]
        l3.execute("BEGIN IMMEDIATE")
        register_definition(l3, quality_definition, quality_hash)
        register_definition(l3, resolution_definition, resolution_hash)
        facts = current_facts(l3)
        quality = desired_quality(facts)
        resolutions = desired_resolutions(facts)
        quality_inserted, quality_stale = materialize_quality(l3, quality)
        resolution_inserted, resolution_stale = materialize_resolutions(
            l3, resolutions
        )
        now = utc_now()
        result = {
            "status": "PASS",
            "mode": args.mode.upper(),
            "run_id": run_id,
            "input_facts": len(facts),
            "quality_current": len(quality),
            "quality_inserted": quality_inserted,
            "quality_stale": quality_stale,
            "resolution_current": len(resolutions),
            "resolution_inserted": resolution_inserted,
            "resolution_stale": resolution_stale,
            "checkpoint": frontier,
        }
        l3.execute(
            """
            INSERT INTO pipeline_runs (
                run_id,mode,status,source_l2_path,source_schema_version,
                started_at_utc,finished_at_utc,details_json
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                "FULL_REBUILD" if args.mode == "full" else "INCREMENTAL",
                "PASS",
                str(Path(args.l2).resolve()),
                l2.execute("PRAGMA user_version").fetchone()[0],
                now,
                now,
                json.dumps(result, separators=(",", ":")),
            ),
        )
        l3.execute(
            """
            INSERT INTO processing_checkpoints (
                pipeline_name,last_l2_observation_id,last_successful_run_id,
                updated_at_utc
            ) VALUES (?,?,?,?)
            ON CONFLICT(pipeline_name) DO UPDATE SET
                last_l2_observation_id=excluded.last_l2_observation_id,
                last_successful_run_id=excluded.last_successful_run_id,
                updated_at_utc=excluded.updated_at_utc
            """,
            (PIPELINE, frontier, run_id, now),
        )
        if l3.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("L3B foreign key check failed")
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
