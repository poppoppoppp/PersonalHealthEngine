import argparse
import hashlib
import json
import sqlite3
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFINITION_ID = "l3c.features.daily"
VERSION = "0.1"
PIPELINE = "l3c.derived_features"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def load_definition(path):
    raw = Path(path).read_bytes()
    definition = json.loads(raw.decode("utf-8-sig"))
    if definition.get("definition_id") != DEFINITION_ID:
        raise ValueError("unexpected L3C feature definition_id")
    if definition.get("definition_version") != VERSION:
        raise ValueError("unexpected L3C feature definition_version")
    return definition, hashlib.sha256(raw).hexdigest()


def register_definition(db, definition, checksum):
    row = db.execute(
        """
        SELECT definition_sha256 FROM definition_registry
        WHERE definition_id=? AND definition_version=?
        """,
        (DEFINITION_ID, VERSION),
    ).fetchone()
    if row is None:
        db.execute(
            """
            INSERT INTO definition_registry (
                definition_id,definition_version,definition_type,status,
                definition_sha256,registered_at_utc,notes
            ) VALUES (?,?,'FEATURE','ACTIVE',?,?,?)
            """,
            (
                DEFINITION_ID,
                VERSION,
                checksum,
                utc_now(),
                "Transparent source-scoped daily and Sleep episode features",
            ),
        )
    elif row[0] != checksum:
        raise ValueError("L3C feature definition checksum mismatch")


def fetch_current_facts(db):
    specs = (
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
    for table, primary_time, secondary_time, duration in specs:
        rows = db.execute(
            f"""
            SELECT fr.id fact_id,fr.metric,fr.fact_kind,fr.evidence_type,
                   x.provider,x.source_sid,x.source_class,x.timezone_name,
                   x.timezone_offset_seconds,{primary_time} primary_time,
                   {secondary_time} secondary_time,{duration} duration_seconds,
                   x.value_num,x.value_code,x.unit,x.attributes_json,
                   MIN(fp.l2_logical_record_id) logical_record_id
            FROM fact_registry fr JOIN {table} x ON x.fact_id=fr.id
            JOIN fact_provenance fp ON fp.fact_id=fr.id
            WHERE fr.status='CURRENT'
              AND NOT EXISTS (
                  SELECT 1 FROM quality_assessments qa
                  WHERE qa.subject_fact_id=fr.id AND qa.status='CURRENT'
                    AND qa.quality_dimension IN (
                        'STRUCTURAL_VALIDITY','PROVENANCE_COMPLETENESS'
                    ) AND qa.result='FLAGGED'
              )
            GROUP BY fr.id ORDER BY fr.id
            """
        ).fetchall()
        facts.extend(dict(row) for row in rows)
    facts.sort(key=lambda fact: fact["fact_id"])
    return facts


def dependency_maps(db):
    quality = defaultdict(list)
    for row in db.execute(
        """
        SELECT subject_fact_id,id FROM quality_assessments
        WHERE status='CURRENT' ORDER BY id
        """
    ):
        quality[row[0]].append(row[1])
    resolution = defaultdict(list)
    for row in db.execute(
        """
        SELECT i.fact_id,i.decision_id FROM source_resolution_inputs i
        JOIN source_resolution_decisions d ON d.id=i.decision_id
        WHERE d.status='CURRENT' ORDER BY i.decision_id
        """
    ):
        resolution[row[0]].append(row[1])
    return quality, resolution


def local_date(timestamp_text, timezone_name, offset_seconds):
    timestamp = datetime.fromisoformat(timestamp_text)
    if offset_seconds is not None:
        zone = timezone(timedelta(seconds=int(offset_seconds)))
        return timestamp.astimezone(zone).date().isoformat()
    if timezone_name:
        try:
            return timestamp.astimezone(ZoneInfo(timezone_name)).date().isoformat()
        except ZoneInfoNotFoundError:
            pass
    return timestamp.astimezone(timezone.utc).date().isoformat()


def canonical_json(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def feature_record(
    name,
    scope_type,
    scope_key,
    date,
    value,
    unit,
    sample_count,
    source,
    coverage,
    attributes,
    inputs,
    quality_map,
    resolution_map,
):
    fact_inputs = tuple(sorted(inputs))
    fact_ids = [fact_id for fact_id, _ in fact_inputs]
    quality_ids = tuple(
        sorted({item for fact_id in fact_ids for item in quality_map[fact_id]})
    )
    resolution_ids = tuple(
        sorted({item for fact_id in fact_ids for item in resolution_map[fact_id]})
    )
    if not fact_inputs or not quality_ids or not resolution_ids:
        raise ValueError(f"feature {name} has incomplete dependencies")
    value_num = float(value) if value is not None else None
    return (
        name,
        scope_type,
        canonical_json(scope_key),
        date,
        value_num,
        None,
        unit,
        int(sample_count),
        source["provider"],
        source["source_sid"],
        source["source_class"],
        source["timezone_name"],
        source["timezone_offset_seconds"],
        coverage,
        canonical_json(attributes),
        fact_inputs,
        quality_ids,
        resolution_ids,
    )


def base_scope(fact, date):
    return {
        "local_date": date,
        "metric": fact["metric"],
        "provider": fact["provider"],
        "source_class": fact["source_class"],
        "source_sid": fact["source_sid"],
        "timezone_name": fact["timezone_name"],
        "timezone_offset_seconds": fact["timezone_offset_seconds"],
    }


def build_daily_features(facts, quality_map, resolution_map):
    desired = []
    point_groups = defaultdict(list)
    bucket_groups = defaultdict(list)
    daily_groups = defaultdict(list)
    for fact in facts:
        if fact["fact_kind"] == "DAILY":
            date = fact["primary_time"]
        elif fact["fact_kind"] == "INTERVAL":
            continue
        else:
            date = local_date(
                fact["primary_time"],
                fact["timezone_name"],
                fact["timezone_offset_seconds"],
            )
        key = (
            fact["metric"],
            date,
            fact["provider"],
            fact["source_sid"],
            fact["source_class"],
            fact["timezone_name"],
            fact["timezone_offset_seconds"],
            fact["unit"],
        )
        if fact["fact_kind"] == "POINT":
            point_groups[key].append(fact)
        elif fact["fact_kind"] == "BUCKET":
            bucket_groups[key].append(fact)
        elif fact["fact_kind"] == "DAILY":
            daily_groups[key].append(fact)

    for group in (point_groups[key] for key in sorted(point_groups)):
        values = [fact["value_num"] for fact in group if fact["value_num"] is not None]
        if not values:
            continue
        first = group[0]
        date = local_date(
            first["primary_time"], first["timezone_name"], first["timezone_offset_seconds"]
        )
        scope = base_scope(first, date)
        inputs = [(fact["fact_id"], "VALUE_INPUT") for fact in group]
        stats = {
            "count": (len(values), "count"),
            "mean": (statistics.fmean(values), first["unit"]),
            "median": (statistics.median(values), first["unit"]),
            "min": (min(values), first["unit"]),
            "max": (max(values), first["unit"]),
        }
        for statistic, (value, unit) in stats.items():
            desired.append(
                feature_record(
                    f"{first['metric']}.daily.{statistic}",
                    "DAILY",
                    scope,
                    date,
                    value,
                    unit,
                    len(values),
                    first,
                    "OBSERVED_ONLY",
                    {
                        "aggregation": statistic,
                        "missing_behavior": "NO_GROUP_NO_FEATURE",
                        "source_scoped": True,
                    },
                    inputs,
                    quality_map,
                    resolution_map,
                )
            )

    for group in (daily_groups[key] for key in sorted(daily_groups)):
        for fact in group:
            if fact["metric"] != "resting_heart_rate" or fact["value_num"] is None:
                continue
            scope = base_scope(fact, fact["primary_time"])
            desired.append(
                feature_record(
                    "resting_heart_rate.daily.value",
                    "DAILY",
                    scope,
                    fact["primary_time"],
                    fact["value_num"],
                    fact["unit"],
                    1,
                    fact,
                    "OBSERVED_ONLY",
                    {"aggregation": "passthrough", "vendor_daily_fact": True},
                    [(fact["fact_id"], "VALUE_INPUT")],
                    quality_map,
                    resolution_map,
                )
            )

    for group in (bucket_groups[key] for key in sorted(bucket_groups)):
        values = [fact["value_num"] for fact in group if fact["value_num"] is not None]
        if not values:
            continue
        first = group[0]
        date = local_date(
            first["primary_time"], first["timezone_name"], first["timezone_offset_seconds"]
        )
        scope = base_scope(first, date)
        inputs = [(fact["fact_id"], "VALUE_INPUT") for fact in group]
        for statistic, value, unit in (
            ("sum", sum(values), first["unit"]),
            ("bucket_count", len(values), "count"),
        ):
            desired.append(
                feature_record(
                    f"{first['metric']}.daily.{statistic}",
                    "DAILY",
                    scope,
                    date,
                    value,
                    unit,
                    len(values),
                    first,
                    "VENDOR_BUCKET_WIDTH_UNRESOLVED",
                    {
                        "aggregation": statistic,
                        "bucket_width_seconds": None,
                        "missing_behavior": "NO_GROUP_NO_FEATURE",
                        "source_scoped": True,
                    },
                    inputs,
                    quality_map,
                    resolution_map,
                )
            )
    return desired


def build_sleep_features(facts, quality_map, resolution_map):
    desired = []
    episodes = {
        fact["logical_record_id"]: fact
        for fact in facts
        if fact["metric"] == "sleep_source_episode"
    }
    segments = defaultdict(list)
    for fact in facts:
        if fact["metric"] == "sleep_vendor_stage_segment":
            segments[fact["logical_record_id"]].append(fact)
    for logical_id, episode in sorted(episodes.items()):
        date = local_date(
            episode["secondary_time"],
            episode["timezone_name"],
            episode["timezone_offset_seconds"],
        )
        scope = {
            **base_scope(episode, date),
            "episode_start_utc": episode["primary_time"],
            "episode_end_utc": episode["secondary_time"],
            "l2_logical_record_id": logical_id,
        }
        episode_input = [(episode["fact_id"], "EPISODE_CONTEXT")]
        desired.append(
            feature_record(
                "sleep_source_episode.duration_seconds",
                "SOURCE_EPISODE",
                scope,
                date,
                episode["duration_seconds"],
                "seconds",
                1,
                episode,
                "VENDOR_INFERENCE",
                {"vendor_inferred": True, "canonical_night": False},
                episode_input,
                quality_map,
                resolution_map,
            )
        )
        episode_segments = sorted(segments.get(logical_id, []), key=lambda x: x["fact_id"])
        all_inputs = episode_input + [
            (fact["fact_id"], "VALUE_INPUT") for fact in episode_segments
        ]
        desired.append(
            feature_record(
                "sleep_source_episode.vendor_stage_segment_count",
                "SOURCE_EPISODE",
                scope,
                date,
                len(episode_segments),
                "count",
                max(1, len(episode_segments)),
                episode,
                "VENDOR_INFERENCE",
                {
                    "explicit_empty_items_is_zero": len(episode_segments) == 0,
                    "vendor_inferred": True,
                },
                all_inputs,
                quality_map,
                resolution_map,
            )
        )
        if not episode_segments:
            continue
        stage_durations = defaultdict(int)
        for segment in episode_segments:
            stage_durations[segment["value_code"]] += segment["duration_seconds"]
        awake = stage_durations.get("AWAKE", 0)
        sleep_like = sum(
            stage_durations.get(stage, 0) for stage in ("DEEP", "LIGHT", "REM", "SLEEP")
        )
        for name, value in (
            ("vendor_awake_duration_seconds", awake),
            ("vendor_sleep_like_duration_seconds", sleep_like),
        ):
            desired.append(
                feature_record(
                    f"sleep_source_episode.{name}",
                    "SOURCE_EPISODE",
                    scope,
                    date,
                    value,
                    "seconds",
                    len(episode_segments),
                    episode,
                    "VENDOR_INFERENCE",
                    {"vendor_inferred": True, "canonical_night": False},
                    all_inputs,
                    quality_map,
                    resolution_map,
                )
            )
        total_stage_duration = sum(stage_durations.values())
        for stage, duration in sorted(stage_durations.items()):
            stage_scope = {**scope, "vendor_stage": stage}
            desired.append(
                feature_record(
                    f"sleep_source_episode.vendor_stage.{stage.lower()}.duration_seconds",
                    "SOURCE_EPISODE",
                    stage_scope,
                    date,
                    duration,
                    "seconds",
                    len(episode_segments),
                    episode,
                    "VENDOR_INFERENCE",
                    {"vendor_inferred": True, "xiaomi_stage": stage},
                    all_inputs,
                    quality_map,
                    resolution_map,
                )
            )
            if total_stage_duration > 0:
                desired.append(
                    feature_record(
                        f"sleep_source_episode.vendor_stage.{stage.lower()}.proportion",
                        "SOURCE_EPISODE",
                        stage_scope,
                        date,
                        duration / total_stage_duration,
                        "ratio",
                        len(episode_segments),
                        episode,
                        "VENDOR_INFERENCE",
                        {
                            "denominator": "sum_vendor_segment_duration",
                            "vendor_inferred": True,
                            "xiaomi_stage": stage,
                        },
                        all_inputs,
                        quality_map,
                        resolution_map,
                    )
                )
    return desired


def current_signatures(db):
    fact_inputs = defaultdict(list)
    for row in db.execute(
        """
        SELECT i.feature_id,i.fact_id,i.input_role FROM derived_feature_fact_inputs i
        JOIN derived_features f ON f.id=i.feature_id WHERE f.status='CURRENT'
        """
    ):
        fact_inputs[row[0]].append((row[1], row[2]))
    quality_inputs = defaultdict(list)
    for row in db.execute(
        """
        SELECT i.feature_id,i.assessment_id FROM derived_feature_quality_inputs i
        JOIN derived_features f ON f.id=i.feature_id WHERE f.status='CURRENT'
        """
    ):
        quality_inputs[row[0]].append(row[1])
    resolution_inputs = defaultdict(list)
    for row in db.execute(
        """
        SELECT i.feature_id,i.decision_id FROM derived_feature_resolution_inputs i
        JOIN derived_features f ON f.id=i.feature_id WHERE f.status='CURRENT'
        """
    ):
        resolution_inputs[row[0]].append(row[1])
    return {
        (
            row["feature_name"],row["scope_type"],row["scope_key"],row["local_date"],
            row["value_num"],row["value_code"],row["unit"],row["sample_count"],
            row["provider"],row["source_sid"],row["source_class"],
            row["timezone_name"],row["timezone_offset_seconds"],
            row["coverage_status"],row["attributes_json"],
            tuple(sorted(fact_inputs[row["id"]])),
            tuple(sorted(quality_inputs[row["id"]])),
            tuple(sorted(resolution_inputs[row["id"]])),
        )
        for row in db.execute("SELECT * FROM derived_features WHERE status='CURRENT'")
    }


def materialize(db, desired):
    expected = set(desired)
    current = current_signatures(db)
    if expected == current:
        return 0, 0
    now = utc_now()
    stale = db.execute(
        "UPDATE derived_features SET status='STALE',updated_at_utc=? WHERE status='CURRENT'",
        (now,),
    ).rowcount
    for feature in desired:
        (
            name,scope_type,scope_key,date,value_num,value_code,unit,sample_count,
            provider,source_sid,source_class,timezone_name,offset,coverage,attributes,
            fact_inputs,quality_inputs,resolution_inputs,
        ) = feature
        cursor = db.execute(
            """
            INSERT INTO derived_features (
                feature_name,scope_type,scope_key,local_date,value_num,value_code,
                unit,sample_count,provider,source_sid,source_class,timezone_name,
                timezone_offset_seconds,coverage_status,definition_id,
                definition_version,status,attributes_json,created_at_utc,updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'CURRENT',?,?,?)
            """,
            (
                name,scope_type,scope_key,date,value_num,value_code,unit,sample_count,
                provider,source_sid,source_class,timezone_name,offset,coverage,
                DEFINITION_ID,VERSION,attributes,now,now,
            ),
        )
        feature_id = cursor.lastrowid
        for fact_id, role in fact_inputs:
            db.execute(
                "INSERT INTO derived_feature_fact_inputs VALUES (?,?,?,?)",
                (feature_id, fact_id, role, now),
            )
        for assessment_id in quality_inputs:
            db.execute(
                "INSERT INTO derived_feature_quality_inputs VALUES (?,?,?)",
                (feature_id, assessment_id, now),
            )
        for decision_id in resolution_inputs:
            db.execute(
                "INSERT INTO derived_feature_resolution_inputs VALUES (?,?,?)",
                (feature_id, decision_id, now),
            )
    return len(desired), stale


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("full", "incremental"), required=True)
    parser.add_argument("--l2", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--definition", required=True)
    args = parser.parse_args()

    definition, checksum = load_definition(args.definition)
    l2 = sqlite3.connect(readonly_uri(args.l2), uri=True)
    l3 = sqlite3.connect(args.l3)
    l3.row_factory = sqlite3.Row
    l3.execute("PRAGMA foreign_keys = ON")
    run_id = (
        f"l3c-{args.mode}-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    try:
        if l3.execute("PRAGMA user_version").fetchone()[0] < 8:
            raise RuntimeError("L3C materializer requires schema >= 8")
        l2.execute("BEGIN")
        frontier = l2.execute(
            "SELECT COALESCE(MAX(id),0) FROM raw_record_observations"
        ).fetchone()[0]
        l3.execute("BEGIN IMMEDIATE")
        register_definition(l3, definition, checksum)
        facts = fetch_current_facts(l3)
        quality_map, resolution_map = dependency_maps(l3)
        desired = build_daily_features(facts, quality_map, resolution_map)
        desired.extend(build_sleep_features(facts, quality_map, resolution_map))
        inserted, stale = materialize(l3, desired)
        now = utc_now()
        result = {
            "status": "PASS",
            "mode": args.mode.upper(),
            "run_id": run_id,
            "input_facts": len(facts),
            "current_features": len(desired),
            "inserted": inserted,
            "stale": stale,
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
                now,now,canonical_json(result),
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
            (PIPELINE,frontier,run_id,now),
        )
        if l3.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("L3C foreign key check failed")
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
