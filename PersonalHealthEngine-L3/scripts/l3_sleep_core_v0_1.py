import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFINITION_ID = "normalize.sleep"
DEFINITION_VERSION = "0.1"
EPISODE_METRIC = "sleep_source_episode"
SEGMENT_METRIC = "sleep_vendor_stage_segment"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def epoch_iso(timestamp):
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def source_class(source_sid):
    source_sid = str(source_sid)
    if source_sid.startswith("hlth.gen_"):
        return "XIAOMI_GENERATED"
    return "NUMERIC_SOURCE"


def decode(raw_json):
    outer = json.loads(raw_json)
    value = outer.get("value")
    if isinstance(value, str):
        return outer, json.loads(value)
    if isinstance(value, dict):
        return outer, value
    raise ValueError("sleep value is not JSON")


def load_definition(path):
    definition_bytes = Path(path).read_bytes()
    definition = json.loads(definition_bytes.decode("utf-8-sig"))
    if definition.get("definition_id") != DEFINITION_ID:
        raise ValueError("unexpected Sleep definition_id")
    if definition.get("definition_version") != DEFINITION_VERSION:
        raise ValueError("unexpected Sleep definition_version")
    return definition, hashlib.sha256(definition_bytes).hexdigest()


def normalize_sleep_row(row, definition):
    _, inner = decode(row["raw_json"])
    logical_id = row["logical_record_id"]
    sid = str(row["raw_sid"])
    classification = source_class(sid)

    bedtime = inner.get("bedtime")
    wake = inner.get("wake_up_time")
    if bedtime is None or wake is None:
        missing = "bedtime" if bedtime is None else "wake_up_time"
        raise ValueError(f"logical {logical_id}: missing {missing}")
    bedtime = int(bedtime)
    wake = int(wake)
    if wake <= bedtime:
        raise ValueError(f"logical {logical_id}: invalid episode interval")
    if int(row["raw_time"]) != wake:
        raise ValueError(f"logical {logical_id}: raw_time != wake_up_time")

    items = inner.get("items", [])
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ValueError(f"logical {logical_id}: items is not list")

    vendor_fields = {
        key: value
        for key, value in inner.items()
        if key != "items" and not isinstance(value, (list, dict))
    }
    facts = [
        {
            "metric": EPISODE_METRIC,
            "start": epoch_iso(bedtime),
            "end": epoch_iso(wake),
            "duration": wake - bedtime,
            "semantics": "XIAOMI_SOURCE_EPISODE",
            "value_code": None,
            "unit": None,
            "provider": row["provider"],
            "source_sid": sid,
            "source_class": classification,
            "timezone_name": row["zone_name"],
            "timezone_offset": row["zone_offset"],
            "attributes_json": json.dumps(
                {"item_count": len(items), "vendor_fields": vendor_fields},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "segment_index": -1,
        }
    ]

    mapping = definition["stage_mapping"].get(classification)
    if not mapping:
        raise ValueError(f"logical {logical_id}: missing mapping for {classification}")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"logical {logical_id}: item {index} is not object")
        if any(item.get(name) is None for name in ("start_time", "end_time", "state")):
            raise ValueError(f"logical {logical_id}: item {index} missing fields")
        start = int(item["start_time"])
        end = int(item["end_time"])
        state = int(item["state"])
        if end < start:
            raise ValueError(f"logical {logical_id}: item {index} invalid interval")
        stage = mapping.get(str(state))
        if stage is None:
            raise ValueError(
                f"logical {logical_id}: unknown state {state} for {classification}"
            )
        facts.append(
            {
                "metric": SEGMENT_METRIC,
                "start": epoch_iso(start),
                "end": epoch_iso(end),
                "duration": end - start,
                "semantics": "XIAOMI_VENDOR_STAGE_SEGMENT",
                "value_code": stage,
                "unit": "vendor_stage",
                "provider": row["provider"],
                "source_sid": sid,
                "source_class": classification,
                "timezone_name": row["zone_name"],
                "timezone_offset": row["zone_offset"],
                "attributes_json": json.dumps(
                    {
                        "segment_index": index,
                        "stage_mapping_version": "sleep-stage-map-v0.1",
                        "xiaomi_state_code": state,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "segment_index": index,
            }
        )
    return facts


def register_definition(db, definition_sha256):
    row = db.execute(
        """
        SELECT definition_sha256 FROM definition_registry
        WHERE definition_id=? AND definition_version=?
        """,
        (DEFINITION_ID, DEFINITION_VERSION),
    ).fetchone()
    if row is None:
        db.execute(
            """
            INSERT INTO definition_registry (
                definition_id, definition_version, definition_type, status,
                definition_sha256, registered_at_utc, notes
            ) VALUES (?,?,'NORMALIZER','ACTIVE',?,?,?)
            """,
            (
                DEFINITION_ID,
                DEFINITION_VERSION,
                definition_sha256,
                utc_now(),
                "Sleep source episode and vendor segment normalizer",
            ),
        )
    elif row[0] != definition_sha256:
        raise ValueError("Sleep definition checksum mismatch")


def _expected_signature(fact, raw_version_id):
    return (
        fact["metric"],
        fact["start"],
        fact["end"],
        fact["duration"],
        fact["semantics"],
        fact["value_code"],
        fact["unit"],
        fact["provider"],
        fact["source_sid"],
        fact["source_class"],
        fact["timezone_name"],
        fact["timezone_offset"],
        fact["attributes_json"],
        raw_version_id,
    )


def materialize_fact_set(db, logical_id, raw_version_id, facts):
    existing = db.execute(
        """
        SELECT fr.id, fr.metric, inf.start_time_utc, inf.end_time_utc,
               inf.duration_seconds, inf.interval_semantics, inf.value_code,
               inf.unit, inf.provider, inf.source_sid, inf.source_class,
               inf.timezone_name, inf.timezone_offset_seconds,
               inf.attributes_json, fp.l2_raw_version_id
        FROM fact_registry fr
        JOIN normalized_interval_facts inf ON inf.fact_id=fr.id
        JOIN fact_provenance fp ON fp.fact_id=fr.id
        WHERE fr.status='CURRENT' AND fr.definition_id=?
          AND fr.definition_version=? AND fp.l2_logical_record_id=?
        ORDER BY fr.id
        """,
        (DEFINITION_ID, DEFINITION_VERSION, logical_id),
    ).fetchall()
    existing_signatures = {
        tuple(row[key] for key in row.keys() if key != "id") for row in existing
    }
    expected_signatures = {_expected_signature(fact, raw_version_id) for fact in facts}
    if len(existing) == len(facts) and existing_signatures == expected_signatures:
        return {"inserted": 0, "superseded": 0, "skipped": len(facts)}

    now = utc_now()
    existing_ids = [row["id"] for row in existing]
    if existing_ids:
        marks = ",".join("?" for _ in existing_ids)
        db.execute(
            f"UPDATE fact_registry SET status='STALE', updated_at_utc=? WHERE id IN ({marks})",
            (now, *existing_ids),
        )

    for fact in facts:
        cursor = db.execute(
            """
            INSERT INTO fact_registry (
                fact_kind, metric, evidence_type, definition_id,
                definition_version, status, created_at_utc, updated_at_utc
            ) VALUES ('INTERVAL',?,'VENDOR_INFERRED',?,?,'CURRENT',?,?)
            """,
            (
                fact["metric"],
                DEFINITION_ID,
                DEFINITION_VERSION,
                now,
                now,
            ),
        )
        fact_id = cursor.lastrowid
        db.execute(
            """
            INSERT INTO normalized_interval_facts (
                fact_id, start_time_utc, end_time_utc, duration_seconds,
                interval_semantics, value_num, value_code, unit, provider,
                source_sid, source_class, timezone_name,
                timezone_offset_seconds, attributes_json
            ) VALUES (?,?,?,?,?,NULL,?,?,?,?,?,?,?,?)
            """,
            (
                fact_id,
                fact["start"],
                fact["end"],
                fact["duration"],
                fact["semantics"],
                fact["value_code"],
                fact["unit"],
                fact["provider"],
                fact["source_sid"],
                fact["source_class"],
                fact["timezone_name"],
                fact["timezone_offset"],
                fact["attributes_json"],
            ),
        )
        db.execute(
            """
            INSERT INTO fact_provenance (
                fact_id, l2_logical_record_id, l2_raw_version_id,
                provenance_role, created_at_utc
            ) VALUES (?,?,?,'SOURCE',?)
            """,
            (fact_id, logical_id, raw_version_id, now),
        )
    return {
        "inserted": len(facts),
        "superseded": len(existing_ids),
        "skipped": 0,
    }
