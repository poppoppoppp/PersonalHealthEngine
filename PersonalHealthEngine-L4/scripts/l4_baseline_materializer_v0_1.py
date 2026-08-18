"""Layer 4 Personal Baseline materializer (full and incremental).

Both modes share the same deterministic core. Full rebuild computes the complete
baseline set and reconciles the whole as-of range. Incremental detects the L3
input-state delta, recomputes only the affected as-of region, and reconciles it.
Both are idempotent and produce semantically identical CURRENT state.
"""

import argparse
import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from l4_baseline_core_v0_1 import (
    ELIGIBILITY_DEFINITION_ID,
    MATURITY_DEFINITION_ID,
    SERIES_DEFINITION_ID,
    WINDOW_DEFINITION_ID,
    add_days,
    baseline_identity,
    baseline_signature,
    build_series,
    canonical_json,
    compute_all_baselines,
    date_range,
    feature_input_signature,
    load_definition,
    parse_date,
    utc_now,
)

PIPELINE = "l4.baseline"


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def register_definition(db, payload, checksum, definition_type, notes):
    definition_id = payload["definition_id"]
    version = payload["definition_version"]
    row = db.execute(
        "SELECT definition_sha256 FROM definition_registry WHERE definition_id=? AND definition_version=?",
        (definition_id, version),
    ).fetchone()
    if row is None:
        db.execute(
            """
            INSERT INTO definition_registry (
                definition_id,definition_version,definition_type,status,
                definition_sha256,registered_at_utc,notes
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (definition_id, version, definition_type, "ACTIVE", checksum, utc_now(), notes),
        )
    elif row["definition_sha256"] != checksum:
        raise ValueError(f"definition checksum mismatch for {definition_id}")


def load_all_definitions(args):
    return (
        load_definition(Path(args.eligibility), ELIGIBILITY_DEFINITION_ID),
        load_definition(Path(args.series), SERIES_DEFINITION_ID),
        load_definition(Path(args.windows), WINDOW_DEFINITION_ID),
        load_definition(Path(args.maturity), MATURITY_DEFINITION_ID),
    )


def fetch_l3_features(l3):
    rows = l3.execute(
        """
        SELECT id,feature_name,scope_type,scope_key,local_date,value_num,value_code,
               unit,provider,source_sid,source_class,timezone_name,
               timezone_offset_seconds,coverage_status,attributes_json,
               definition_id,definition_version
        FROM derived_features WHERE status='CURRENT' ORDER BY id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def reconcile_series(db, series, now):
    """Upsert current series and retire removed ones; return {series_key: series_id}."""
    current = {
        row["series_key"]: row["id"]
        for row in db.execute(
            """
            SELECT id,series_key FROM baseline_series
            WHERE status='CURRENT' AND definition_id=? AND definition_version=?
            """,
            (SERIES_DEFINITION_ID, "0.1"),
        )
    }
    series_id_map = {}
    for key, entry in series.items():
        if key in current:
            series_id_map[key] = current[key]
            continue
        components = entry["components"]
        cursor = db.execute(
            """
            INSERT INTO baseline_series (
                series_key,feature_name,scope_type,provider,source_sid,source_class,
                timezone_name,timezone_offset_seconds,unit,observation_semantics,
                definition_id,definition_version,status,created_at_utc,updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'CURRENT',?,?)
            """,
            (
                key,
                components["feature_name"],
                components["scope_type"],
                components["provider"],
                components["source_sid"],
                components["source_class"],
                components["timezone_name"],
                components["timezone_offset_seconds"],
                components["unit"],
                "SOURCE_EPISODE_VALUE" if components["scope_type"] == "SOURCE_EPISODE" else "DAILY_VALUE",
                SERIES_DEFINITION_ID,
                "0.1",
                now,
                now,
            ),
        )
        series_id_map[key] = cursor.lastrowid

    for key, series_id in current.items():
        if key not in series:
            db.execute(
                "UPDATE baseline_series SET status='STALE',updated_at_utc=? WHERE id=?",
                (now, series_id),
            )
            db.execute(
                "UPDATE rolling_baselines SET status='STALE',updated_at_utc=? "
                "WHERE series_id=? AND status='CURRENT'",
                (now, series_id),
            )
    return series_id_map


def current_baseline_map(db, region_start, region_end):
    """{(series_key, window_days, as_of_date): (id, signature)} for CURRENT rows in region."""
    inputs = defaultdict(list)
    for row in db.execute(
        """
        SELECT bfi.baseline_id,bfi.l3_feature_id
        FROM baseline_feature_inputs bfi
        JOIN rolling_baselines rb ON rb.id=bfi.baseline_id
        WHERE rb.status='CURRENT' AND rb.as_of_date BETWEEN ? AND ?
        ORDER BY bfi.l3_feature_id
        """,
        (region_start, region_end),
    ):
        inputs[row["baseline_id"]].append(row["l3_feature_id"])

    result = {}
    for row in db.execute(
        """
        SELECT rb.*,bs.series_key FROM rolling_baselines rb
        JOIN baseline_series bs ON bs.id=rb.series_id
        WHERE rb.status='CURRENT' AND rb.as_of_date BETWEEN ? AND ?
        """,
        (region_start, region_end),
    ):
        baseline = {
            "series_key": row["series_key"],
            "window_days": row["window_days"],
            "as_of_date": row["as_of_date"],
            "observation_count": row["observation_count"],
            "distinct_observation_dates": row["distinct_observation_dates"],
            "history_span_days": row["history_span_days"],
            "calendar_coverage": row["calendar_coverage"],
            "mean": row["mean"],
            "median": row["median"],
            "mad": row["mad"],
            "q10": row["q10"],
            "q25": row["q25"],
            "q50": row["q50"],
            "q75": row["q75"],
            "q90": row["q90"],
            "unit": row["unit"],
            "maturity": row["maturity"],
            "maturity_definition_id": row["maturity_definition_id"],
            "maturity_definition_version": row["maturity_definition_version"],
            "window_definition_id": row["window_definition_id"],
            "window_definition_version": row["window_definition_version"],
            "attributes": json.loads(row["attributes_json"]),
            "inputs": tuple(sorted(inputs[row["id"]])),
        }
        key = (row["series_key"], row["window_days"], row["as_of_date"])
        result[key] = (row["id"], baseline_signature(baseline))
    return result


def insert_baseline(db, baseline, series_id_map, feature_meta, now):
    attributes = canonical_json(baseline["attributes"])
    cursor = db.execute(
        """
        INSERT INTO rolling_baselines (
            series_id,window_days,as_of_date,observation_count,
            distinct_observation_dates,history_span_days,calendar_coverage,
            mean,median,mad,q10,q25,q50,q75,q90,unit,maturity,
            maturity_definition_id,maturity_definition_version,
            window_definition_id,window_definition_version,status,attributes_json,
            created_at_utc,updated_at_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'CURRENT',?,?,?)
        """,
        (
            series_id_map[baseline["series_key"]],
            baseline["window_days"],
            baseline["as_of_date"],
            baseline["observation_count"],
            baseline["distinct_observation_dates"],
            baseline["history_span_days"],
            baseline["calendar_coverage"],
            baseline["mean"],
            baseline["median"],
            baseline["mad"],
            baseline["q10"],
            baseline["q25"],
            baseline["q50"],
            baseline["q75"],
            baseline["q90"],
            baseline["unit"],
            baseline["maturity"],
            baseline["maturity_definition_id"],
            baseline["maturity_definition_version"],
            baseline["window_definition_id"],
            baseline["window_definition_version"],
            attributes,
            now,
            now,
        ),
    )
    baseline_id = cursor.lastrowid
    for feature_id in baseline["inputs"]:
        name, local_date = feature_meta.get(feature_id, (None, None))
        db.execute(
            """
            INSERT INTO baseline_feature_inputs (
                baseline_id,l3_feature_id,l3_feature_name,l3_local_date,created_at_utc
            ) VALUES (?,?,?,?,?)
            """,
            (baseline_id, feature_id, name, local_date, now),
        )
    return baseline_id


def reconcile(db, desired, series_id_map, feature_meta, region_start, region_end, now):
    current = current_baseline_map(db, region_start, region_end)
    desired_map = {baseline_identity(b): baseline_signature(b) for b in desired}

    to_stale = []
    to_insert_keys = set()
    for key, (row_id, sig) in current.items():
        if key not in desired_map or desired_map[key] != sig:
            to_stale.append(row_id)
    for key, sig in desired_map.items():
        if key not in current or current[key][1] != sig:
            to_insert_keys.add(key)

    if to_stale:
        db.executemany(
            "UPDATE rolling_baselines SET status='STALE',updated_at_utc=? WHERE id=?",
            [(now, row_id) for row_id in to_stale],
        )

    inserted = 0
    for baseline in desired:
        if baseline_identity(baseline) in to_insert_keys:
            insert_baseline(db, baseline, series_id_map, feature_meta, now)
            inserted += 1
    return inserted, len(to_stale)


def input_state(features):
    return {
        feature["id"]: {
            "feature_name": feature["feature_name"],
            "local_date": feature["local_date"],
            "signature": feature_input_signature(feature),
        }
        for feature in features
    }


def read_input_state(db):
    state = {}
    for row in db.execute(
        "SELECT l3_feature_id,l3_feature_name,l3_local_date,signature FROM baseline_input_state"
    ):
        state[row["l3_feature_id"]] = {
            "feature_name": row["l3_feature_name"],
            "local_date": row["l3_local_date"],
            "signature": row["signature"],
        }
    return state


def write_input_state(db, state, now):
    db.execute("DELETE FROM baseline_input_state")
    db.executemany(
        """
        INSERT INTO baseline_input_state (
            l3_feature_id,l3_feature_name,l3_local_date,signature,updated_at_utc
        ) VALUES (?,?,?,?,?)
        """,
        [
            (fid, meta["feature_name"], meta["local_date"], meta["signature"], now)
            for fid, meta in state.items()
        ],
    )


def feature_meta_map(features):
    return {feature["id"]: (feature["feature_name"], feature["local_date"]) for feature in features}


def dirty_local_dates(old_state, new_state):
    dirty = set()
    old_ids = set(old_state)
    new_ids = set(new_state)
    for fid in old_ids - new_ids:
        dirty.add(old_state[fid]["local_date"])
    for fid in new_ids - old_ids:
        dirty.add(new_state[fid]["local_date"])
    for fid in old_ids & new_ids:
        if old_state[fid]["signature"] != new_state[fid]["signature"]:
            dirty.add(old_state[fid]["local_date"])
            dirty.add(new_state[fid]["local_date"])
    return dirty


def compute_frontier(l3):
    return l3.execute("SELECT COALESCE(MAX(id),0) FROM derived_features").fetchone()[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("full", "incremental"), required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--eligibility", required=True)
    parser.add_argument("--series", required=True)
    parser.add_argument("--windows", required=True)
    parser.add_argument("--maturity", required=True)
    args = parser.parse_args()

    (eligibility, eligibility_sha), (series_def, series_sha), (windows_def, windows_sha), (
        maturity_def,
        maturity_sha,
    ) = load_all_definitions(args)
    windows = list(windows_def["windows"])
    maturity_thresholds = maturity_def["thresholds"]

    l3 = sqlite3.connect(readonly_uri(args.l3), uri=True)
    l3.row_factory = sqlite3.Row
    l4 = sqlite3.connect(args.l4)
    l4.row_factory = sqlite3.Row
    l4.execute("PRAGMA foreign_keys = ON")

    run_id = (
        f"l4-{args.mode}-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    try:
        if l4.execute("PRAGMA user_version").fetchone()[0] < 2:
            raise RuntimeError("L4 materializer requires schema >= 2")

        features = fetch_l3_features(l3)
        frontier = compute_frontier(l3)
        l3_schema_version = l3.execute("PRAGMA user_version").fetchone()[0]

        l4.execute("BEGIN IMMEDIATE")
        now = utc_now()
        register_definition(l4, eligibility, eligibility_sha, "ELIGIBILITY", "L4A baseline eligibility")
        register_definition(l4, series_def, series_sha, "SERIES", "L4B baseline series identity")
        register_definition(l4, windows_def, windows_sha, "WINDOW", "L4C rolling baseline windows")
        register_definition(l4, maturity_def, maturity_sha, "MATURITY", "L4D baseline maturity")

        series = build_series(features)
        series_id_map = reconcile_series(l4, series, now)

        eligible_dates = sorted(
            {feature["local_date"] for feature in features if feature["value_num"] is not None}
        )
        min_date = parse_date(eligible_dates[0]) if eligible_dates else None
        max_date = parse_date(eligible_dates[-1]) if eligible_dates else None
        meta = feature_meta_map(features)

        new_state = input_state(features)
        old_state = read_input_state(l4)
        desired_count = 0

        if args.mode == "full":
            affected_from = min_date.isoformat() if min_date else None
            affected_to = add_days(max_date, 1).isoformat() if max_date else None
            as_of_dates = date_range(min_date, add_days(max_date, 1)) if min_date else []
            desired = compute_all_baselines(series, as_of_dates, windows, maturity_thresholds)
            desired_count = len(desired)
            if as_of_dates:
                inserted, stale = reconcile(
                    l4, desired, series_id_map, meta, as_of_dates[0], as_of_dates[-1], now
                )
            else:
                cursor = l4.execute(
                    "UPDATE rolling_baselines SET status='STALE',updated_at_utc=? WHERE status='CURRENT'",
                    (now,),
                )
                inserted, stale = 0, cursor.rowcount
            write_input_state(l4, new_state, now)
        else:
            if old_state == new_state:
                inserted = stale = 0
                desired_count = 0
                affected_from = affected_to = None
            else:
                if not old_state:
                    # First build via incremental: no prior input state, so reconcile the
                    # full as-of range to remain equivalent to a full rebuild.
                    affected_from = min_date.isoformat() if min_date else None
                    affected_to = add_days(max_date, 1).isoformat() if max_date else None
                else:
                    dirty = dirty_local_dates(old_state, new_state)
                    old_max = max((v["local_date"] for v in old_state.values()), default=None)
                    new_max = max((v["local_date"] for v in new_state.values()), default=None)
                    affected_from = add_days(parse_date(min(dirty)), 1).isoformat() if dirty else None
                    peak = None
                    for candidate in (old_max, new_max):
                        if candidate is not None and (peak is None or candidate > peak):
                            peak = candidate
                    affected_to = add_days(parse_date(peak), 1).isoformat() if peak else None

                if affected_from is None or affected_to is None:
                    as_of_dates = []
                else:
                    as_of_dates = date_range(parse_date(affected_from), parse_date(affected_to))
                desired = compute_all_baselines(series, as_of_dates, windows, maturity_thresholds)
                desired_count = len(desired)
                if as_of_dates:
                    inserted, stale = reconcile(
                        l4, desired, series_id_map, meta, as_of_dates[0], as_of_dates[-1], now
                    )
                else:
                    inserted = stale = 0
                write_input_state(l4, new_state, now)

        result = {
            "status": "PASS",
            "mode": args.mode.upper(),
            "run_id": run_id,
            "input_features": len(features),
            "series_count": len(series),
            "desired_baselines": desired_count,
            "inserted": inserted,
            "stale": stale,
            "affected_from": affected_from,
            "affected_to": affected_to,
            "checkpoint": frontier,
        }
        l4.execute(
            """
            INSERT INTO pipeline_runs (
                run_id,mode,status,source_l3_path,source_schema_version,
                started_at_utc,finished_at_utc,details_json
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                "FULL_REBUILD" if args.mode == "full" else "INCREMENTAL",
                "PASS",
                str(Path(args.l3).resolve()),
                l3_schema_version,
                now,
                now,
                canonical_json(result),
            ),
        )
        l4.execute(
            """
            INSERT INTO processing_checkpoints (
                pipeline_name,last_l3_feature_id,last_successful_run_id,updated_at_utc
            ) VALUES (?,?,?,?)
            ON CONFLICT(pipeline_name) DO UPDATE SET
                last_l3_feature_id=excluded.last_l3_feature_id,
                last_successful_run_id=excluded.last_successful_run_id,
                updated_at_utc=excluded.updated_at_utc
            """,
            (PIPELINE, frontier, run_id, now),
        )
        if l4.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("L4 foreign key check failed")
        l4.commit()
        print(json.dumps(result, indent=2))
    except Exception:
        l4.rollback()
        raise
    finally:
        l3.close()
        l4.close()


if __name__ == "__main__":
    main()
