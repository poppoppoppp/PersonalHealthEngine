"""Layer 5 Health Analytics materializer (full and incremental).

Both modes share the same deterministic core. Full rebuild computes the complete analytics
set and reconciles the whole database. Incremental detects the L3/L4 input-state delta and
materializes only changed rows (delta reconciliation), so full and incremental produce
semantically identical CURRENT state.
"""

import argparse
import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from l5_analytics_core_v0_1 import (
    CHANGE_POINT_DEFINITION_ID,
    DEVIATION_DEFINITION_ID,
    EVIDENCE_DEFINITION_ID,
    PERSISTENCE_DEFINITION_ID,
    RELATIONSHIP_DEFINITION_ID,
    TREND_DEFINITION_ID,
    canonical_json,
    classify_persistence,
    classify_relationship,
    classify_trend,
    detect_change,
    deviation_metrics,
    load_definition,
    parse_date,
    series_component_tuple,
    utc_now,
)

PIPELINE = "l5.analytics"
ANALYTIC_TYPES = ("DEVIATION", "PERSISTENCE", "TREND", "CHANGE_POINT", "RELATIONSHIP")


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


def fetch_l4(l4):
    series = [dict(row) for row in l4.execute("SELECT * FROM baseline_series WHERE status='CURRENT' ORDER BY id")]
    baselines = [dict(row) for row in l4.execute("SELECT * FROM rolling_baselines WHERE status='CURRENT' ORDER BY id")]
    return series, baselines


def build_indexes(l4_series, l4_baselines):
    series_by_component = {}
    series_key_by_id = {}
    for s in l4_series:
        series_by_component[series_component_tuple(s)] = s
        series_key_by_id[s["id"]] = s["series_key"]
    baselines = {}
    for b in l4_baselines:
        key = (series_key_by_id.get(b["series_id"]), b["window_days"], b["as_of_date"])
        if key[0] is not None:
            baselines[key] = b
    return series_by_component, series_key_by_id, baselines


def build_desired(features, l4_series, defs):
    """Compute all desired analytics. Returns dict of lists by type."""
    series_by_component, series_key_by_id, baselines = build_indexes(l4_series["series"], l4_series["baselines"])
    windows = defs["deviation"]["windows"]
    relative_units = set(defs["deviation"]["relative_deviation_units"])
    min_consecutive = defs["persistence"]["min_consecutive"]
    min_trend = defs["trend"]["min_trend_points"]
    max_trend = defs["trend"]["max_trend_points"]
    rho_trend = defs["trend"]["rho_threshold"]
    min_change = defs["change"]["min_change_points"]
    min_segment = defs["change"]["min_segment_points"]
    shift_threshold = defs["change"]["shift_threshold"]
    min_paired = defs["relationship"]["min_paired"]
    rho_rel = defs["relationship"]["rho_threshold"]
    pairs = defs["relationship"]["pairs"]

    numeric_features = [f for f in features if f["value_num"] is not None]

    # --- map each feature to its series ---
    feature_series = []  # (feature, series_row) for features with a matching series
    for f in numeric_features:
        s = series_by_component.get(series_component_tuple(f))
        if s is not None:
            feature_series.append((f, s))

    # --- group by series_key ---
    by_series = defaultdict(list)
    for f, s in feature_series:
        by_series[s["series_key"]].append((f, s))

    deviations = []
    for f, s in feature_series:
        series_key = s["series_key"]
        attrs = {
            "personal_deviation": True,
            "not_clinical": True,
            "robust": True,
            "coverage_status": f["coverage_status"],
            "observation_semantics": s["observation_semantics"],
        }
        for window in windows:
            baseline = baselines.get((series_key, window, f["local_date"]))
            if baseline is None:
                continue
            m = deviation_metrics(f["value_num"], f["unit"], baseline, relative_units)
            record = {
                "series_key": series_key,
                "window_days": window,
                "feature_date": f["local_date"],
                "l3_feature_id": f["id"],
                "l4_baseline_id": baseline["id"],
                "baseline_maturity": m["baseline_maturity"],
                "baseline_median": m["baseline_median"],
                "baseline_mad": m["baseline_mad"],
                "current_value": m["current_value"],
                "absolute_deviation": m["absolute_deviation"],
                "relative_deviation": m["relative_deviation"],
                "relative_deviation_applicable": m["relative_deviation_applicable"],
                "robust_standardized_deviation": m["robust_standardized_deviation"],
                "robust_z_unavailable_reason": m["robust_z_unavailable_reason"],
                "quantile_position": m["quantile_position"],
                "deviation_side": m["deviation_side"],
                "deviation_class": m["deviation_class"],
                "evidence_status": m["evidence_status"],
                "attributes": attrs,
                "l3_inputs": [(f["id"], f["feature_name"], f["local_date"], "CURRENT_VALUE")],
                "baseline_inputs": [(baseline["id"], baseline["as_of_date"], window)],
            }
            record["key"] = ("DEVIATION", series_key, window, f["local_date"], f["id"])
            deviations.append(record)

    persistence = []
    trend = []
    change = []
    for series_key in sorted(by_series):
        items = sorted(by_series[series_key], key=lambda x: (x[0]["local_date"], x[0]["id"]))
        s = items[0][1]
        if s["observation_semantics"] != "DAILY_VALUE":
            continue  # persistence/trend/change apply to DAILY series only
        dates = [f["local_date"] for f, _ in items]
        latest = dates[-1]

        # Per-window persistence over the deviation classes for this series.
        for window in windows:
            classes = []
            maturities = []
            l3_inputs = []
            baseline_inputs = []
            for f, _s in items:
                baseline = baselines.get((series_key, window, f["local_date"]))
                if baseline is None:
                    continue
                m = deviation_metrics(f["value_num"], f["unit"], baseline, relative_units)
                classes.append(m["deviation_class"])
                maturities.append(m["baseline_maturity"])
                l3_inputs.append((f["id"], f["feature_name"], f["local_date"], "VALUE_INPUT"))
                baseline_inputs.append((baseline["id"], baseline["as_of_date"], window))
            p = classify_persistence(classes, maturities, min_consecutive)
            record = {
                "series_key": series_key,
                "window_days": window,
                "as_of_date": latest,
                "trailing_observation_count": p["trailing_observation_count"],
                "consecutive_above_typical": p["consecutive_above_typical"],
                "consecutive_below_typical": p["consecutive_below_typical"],
                "persistence_class": p["persistence_class"],
                "evidence_status": p["evidence_status"],
                "attributes": {"consecutive_semantics": "observation_sequence_not_calendar"},
                "l3_inputs": l3_inputs,
                "baseline_inputs": baseline_inputs,
            }
            record["key"] = ("PERSISTENCE", series_key, window)
            persistence.append(record)

        # Trend over trailing observations (up to max_trend_points).
        trailing = items[-max_trend:]
        points = [(parse_date(f["local_date"]).toordinal(), f["value_num"]) for f, _ in trailing]
        t = classify_trend(points, min_trend, rho_trend)
        record = {
            "series_key": series_key,
            "as_of_date": latest,
            "trend_point_count": t["trend_point_count"],
            "trend_start_date": trailing[0][0]["local_date"] if trailing else None,
            "trend_end_date": trailing[-1][0]["local_date"] if trailing else None,
            "theil_sen_slope": t["theil_sen_slope"],
            "spearman_rho": t["spearman_rho"],
            "trend_class": t["trend_class"],
            "evidence_status": t["evidence_status"],
            "attributes": {"slope_method": "theil_sen", "monotonicity": "spearman"},
            "l3_inputs": [(f["id"], f["feature_name"], f["local_date"], "VALUE_INPUT") for f, _ in trailing],
            "baseline_inputs": [],
        }
        record["key"] = ("TREND", series_key)
        trend.append(record)

        # Change point over trailing observations.
        trailing_all = items[-min_change:]
        change_points = [(f["local_date"], f["value_num"]) for f, _ in trailing_all]
        c = detect_change(change_points, min_change, min_segment, shift_threshold)
        record = {
            "series_key": series_key,
            "as_of_date": latest,
            "observation_count": c["observation_count"],
            "candidate_split_date": c["candidate_split_date"],
            "shift_magnitude": c["shift_magnitude"],
            "change_class": c["change_class"],
            "evidence_status": c["evidence_status"],
            "attributes": {"method": "robust_median_level_shift"},
            "l3_inputs": [(f["id"], f["feature_name"], f["local_date"], "VALUE_INPUT") for f, _ in trailing_all],
            "baseline_inputs": [],
        }
        record["key"] = ("CHANGE_POINT", series_key)
        change.append(record)

    # --- relationships ---
    relationship = []
    # Index DAILY series by feature_name within source context.
    daily_by_feature = defaultdict(list)
    for f, s in feature_series:
        if s["observation_semantics"] == "DAILY_VALUE":
            daily_by_feature[f["feature_name"]].append((f, s))
    seen = set()
    for pair in pairs:
        fa, fb = pair["feature_a"], pair["feature_b"]
        series_a = daily_by_feature.get(fa, [])
        series_b = daily_by_feature.get(fb, [])
        # group by source context
        contexts = {}
        for f, s in series_a:
            ctx = (s["provider"], s["source_class"], s["source_sid"], s["timezone_name"], s["timezone_offset_seconds"])
            contexts.setdefault(ctx, {})["a"] = {ff["local_date"]: ff["value_num"] for ff, _ in series_a if (s["provider"], s["source_class"], s["source_sid"], s["timezone_name"], s["timezone_offset_seconds"]) == ctx}
        for f, s in series_b:
            ctx = (s["provider"], s["source_class"], s["source_sid"], s["timezone_name"], s["timezone_offset_seconds"])
            contexts.setdefault(ctx, {})["b"] = {ff["local_date"]: ff["value_num"] for ff, _ in series_b if (s["provider"], s["source_class"], s["source_sid"], s["timezone_name"], s["timezone_offset_seconds"]) == ctx}
        for ctx, sides in sorted(contexts.items(), key=lambda kv: json.dumps(kv[0], sort_keys=True)):
            if "a" not in sides or "b" not in sides:
                continue
            a_map = sides["a"]
            b_map = sides["b"]
            common = sorted(set(a_map) & set(b_map))
            xs = [a_map[d] for d in common]
            ys = [b_map[d] for d in common]
            r = classify_relationship(xs, ys, min_paired, rho_rel)
            # find the two series rows for key identity
            key_a = None
            key_b = None
            for f, s in series_a:
                if (s["provider"], s["source_class"], s["source_sid"], s["timezone_name"], s["timezone_offset_seconds"]) == ctx:
                    key_a = s["series_key"]; break
            for f, s in series_b:
                if (s["provider"], s["source_class"], s["source_sid"], s["timezone_name"], s["timezone_offset_seconds"]) == ctx:
                    key_b = s["series_key"]; break
            if key_a is None or key_b is None:
                continue
            if key_a == key_b:
                continue
            pair_key = tuple(sorted((key_a, key_b)))
            if pair_key in seen:
                continue
            seen.add(pair_key)
            # collect paired input features
            l3_inputs = []
            for f, s in series_a:
                if f["local_date"] in common and (s["provider"], s["source_class"], s["source_sid"], s["timezone_name"], s["timezone_offset_seconds"]) == ctx:
                    l3_inputs.append((f["id"], f["feature_name"], f["local_date"], "VALUE_INPUT"))
            for f, s in series_b:
                if f["local_date"] in common and (s["provider"], s["source_class"], s["source_sid"], s["timezone_name"], s["timezone_offset_seconds"]) == ctx:
                    l3_inputs.append((f["id"], f["feature_name"], f["local_date"], "VALUE_INPUT"))
            record = {
                "series_key_a": pair_key[0],
                "series_key_b": pair_key[1],
                "as_of_date": common[-1] if common else None,
                "paired_count": r["paired_count"],
                "spearman_rho": r["spearman_rho"],
                "relationship_class": r["relationship_class"],
                "evidence_status": r["evidence_status"],
                "attributes": {
                    "association_only": True,
                    "causal_inference": False,
                    "metric_a": pair["metric_a"],
                    "metric_b": pair["metric_b"],
                    "feature_a": pair["feature_a"],
                    "feature_b": pair["feature_b"],
                },
                "l3_inputs": l3_inputs,
                "baseline_inputs": [],
            }
            record["key"] = ("RELATIONSHIP", pair_key[0], pair_key[1])
            relationship.append(record)

    return {
        "deviation": deviations,
        "persistence": persistence,
        "trend": trend,
        "change": change,
        "relationship": relationship,
    }


# ---------------------------------------------------------------------------
# Reconciliation helpers (per table): load CURRENT signature maps, then diff.
# ---------------------------------------------------------------------------

def l3_ids(inputs):
    return tuple(sorted(x[0] for x in inputs))


def baseline_ids(inputs):
    return tuple(sorted(x[0] for x in inputs))


def _sig_deviation(row, inputs, baseline_inputs):
    return (
        row["series_key"], row["window_days"], row["feature_date"], row["l3_feature_id"],
        row["l4_baseline_id"], row["baseline_maturity"], row["baseline_median"],
        row["baseline_mad"], row["current_value"], row["absolute_deviation"],
        row["relative_deviation"], row["relative_deviation_applicable"],
        row["robust_standardized_deviation"], row["robust_z_unavailable_reason"],
        row["quantile_position"], row["deviation_side"], row["deviation_class"],
        row["evidence_status"], canonical_json(json.loads(row["attributes_json"])),
        tuple(sorted(inputs)), tuple(sorted(baseline_inputs)),
    )


def _load_current_deviation(db):
    l3_inputs = defaultdict(list)
    for r in db.execute("SELECT analytic_id,l3_feature_id FROM analytics_l3_inputs WHERE analytic_type='DEVIATION'"):
        l3_inputs[r["analytic_id"]].append(r["l3_feature_id"])
    b_inputs = defaultdict(list)
    for r in db.execute("SELECT analytic_id,l4_baseline_id FROM analytics_baseline_inputs WHERE analytic_type='DEVIATION'"):
        b_inputs[r["analytic_id"]].append(r["l4_baseline_id"])
    result = {}
    for row in db.execute(
        "SELECT d.*, s.series_key FROM deviation_analytics d JOIN analytics_series s ON s.id=d.series_id WHERE d.status='CURRENT'"
    ):
        key = ("DEVIATION", row["series_key"], row["window_days"], row["feature_date"], row["l3_feature_id"])
        result[key] = (row["id"], _sig_deviation(row, l3_inputs[row["id"]], b_inputs[row["id"]]))
    return result


def _sig_trend(row, inputs):
    return (
        row["series_key"], row["as_of_date"], row["trend_point_count"],
        row["trend_start_date"], row["trend_end_date"], row["theil_sen_slope"],
        row["spearman_rho"], row["trend_class"], row["evidence_status"],
        canonical_json(json.loads(row["attributes_json"])),
        tuple(sorted(inputs)),
    )


def _load_current_trend(db):
    l3_inputs = defaultdict(list)
    for r in db.execute("SELECT analytic_id,l3_feature_id FROM analytics_l3_inputs WHERE analytic_type='TREND'"):
        l3_inputs[r["analytic_id"]].append(r["l3_feature_id"])
    result = {}
    for row in db.execute(
        "SELECT t.*, s.series_key FROM trend_analytics t JOIN analytics_series s ON s.id=t.series_id WHERE t.status='CURRENT'"
    ):
        key = ("TREND", row["series_key"])
        result[key] = (row["id"], _sig_trend(row, l3_inputs[row["id"]]))
    return result


def _sig_change(row, inputs):
    return (
        row["series_key"], row["as_of_date"], row["observation_count"],
        row["candidate_split_date"], row["shift_magnitude"], row["change_class"],
        row["evidence_status"], canonical_json(json.loads(row["attributes_json"])),
        tuple(sorted(inputs)),
    )


def _load_current_change(db):
    l3_inputs = defaultdict(list)
    for r in db.execute("SELECT analytic_id,l3_feature_id FROM analytics_l3_inputs WHERE analytic_type='CHANGE_POINT'"):
        l3_inputs[r["analytic_id"]].append(r["l3_feature_id"])
    result = {}
    for row in db.execute(
        "SELECT t.*, s.series_key FROM change_point_analytics t JOIN analytics_series s ON s.id=t.series_id WHERE t.status='CURRENT'"
    ):
        key = ("CHANGE_POINT", row["series_key"])
        result[key] = (row["id"], _sig_change(row, l3_inputs[row["id"]]))
    return result


def _load_current_relationship(db):
    l3_inputs = defaultdict(list)
    for r in db.execute("SELECT analytic_id,l3_feature_id FROM analytics_l3_inputs WHERE analytic_type='RELATIONSHIP'"):
        l3_inputs[r["analytic_id"]].append(r["l3_feature_id"])
    result = {}
    for row in db.execute(
        """
        SELECT r.*, sa.series_key ka, sb.series_key kb
        FROM relationship_analytics r
        JOIN analytics_series sa ON sa.id=r.series_id_a
        JOIN analytics_series sb ON sb.id=r.series_id_b
        WHERE r.status='CURRENT'
        """
    ):
        key = ("RELATIONSHIP", row["ka"], row["kb"])
        sig = (
            row["ka"], row["kb"], row["as_of_date"], row["paired_count"],
            row["spearman_rho"], row["relationship_class"], row["evidence_status"],
            canonical_json(json.loads(row["attributes_json"])),
            tuple(sorted(l3_inputs[row["id"]])),
        )
        result[key] = (row["id"], sig)
    return result


def _sig_persistence(row, inputs, baseline_inputs):
    return (
        row["series_key"], row["window_days"], row["as_of_date"],
        row["trailing_observation_count"], row["consecutive_above_typical"],
        row["consecutive_below_typical"], row["persistence_class"], row["evidence_status"],
        canonical_json(json.loads(row["attributes_json"])),
        tuple(sorted(inputs)), tuple(sorted(baseline_inputs)),
    )


def _load_current_persistence(db):
    l3_inputs = defaultdict(list)
    for r in db.execute("SELECT analytic_id,l3_feature_id FROM analytics_l3_inputs WHERE analytic_type='PERSISTENCE'"):
        l3_inputs[r["analytic_id"]].append(r["l3_feature_id"])
    b_inputs = defaultdict(list)
    for r in db.execute("SELECT analytic_id,l4_baseline_id FROM analytics_baseline_inputs WHERE analytic_type='PERSISTENCE'"):
        b_inputs[r["analytic_id"]].append(r["l4_baseline_id"])
    result = {}
    for row in db.execute(
        "SELECT p.*, s.series_key FROM persistence_analytics p JOIN analytics_series s ON s.id=p.series_id WHERE p.status='CURRENT'"
    ):
        key = ("PERSISTENCE", row["series_key"], row["window_days"])
        result[key] = (row["id"], _sig_persistence(row, l3_inputs[row["id"]], b_inputs[row["id"]]))
    return result


def reconcile_table(db, table, desired, current_loader, insert_fn, now, analytic_type):
    current = current_loader(db)
    desired_map = {r["key"]: r for r in desired}
    to_stale = []
    for key, (row_id, sig) in current.items():
        rec = desired_map.get(key)
        if rec is None or rec["sig"] != sig:
            to_stale.append(row_id)
    if to_stale:
        db.executemany(
            f"UPDATE {table} SET status='STALE',updated_at_utc=? WHERE id=?",
            [(now, i) for i in to_stale],
        )
    inserted = 0
    for rec in desired:
        cur = current.get(rec["key"])
        if cur is None or cur[1] != rec["sig"]:
            insert_fn(db, rec, now)
            inserted += 1
    return inserted, len(to_stale)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("full", "incremental"), required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--l5", required=True)
    parser.add_argument("--deviation", required=True)
    parser.add_argument("--persistence", required=True)
    parser.add_argument("--trend", required=True)
    parser.add_argument("--change", required=True)
    parser.add_argument("--relationship", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()

    deviation_def, deviation_sha = load_definition(Path(args.deviation), DEVIATION_DEFINITION_ID)
    persistence_def, persistence_sha = load_definition(Path(args.persistence), PERSISTENCE_DEFINITION_ID)
    trend_def, trend_sha = load_definition(Path(args.trend), TREND_DEFINITION_ID)
    change_def, change_sha = load_definition(Path(args.change), CHANGE_POINT_DEFINITION_ID)
    relationship_def, relationship_sha = load_definition(Path(args.relationship), RELATIONSHIP_DEFINITION_ID)
    evidence_def, evidence_sha = load_definition(Path(args.evidence), EVIDENCE_DEFINITION_ID)

    defs = {
        "deviation": deviation_def,
        "persistence": persistence_def,
        "trend": trend_def,
        "change": change_def,
        "relationship": relationship_def,
        "evidence": evidence_def,
    }

    l3 = sqlite3.connect(readonly_uri(args.l3), uri=True)
    l3.row_factory = sqlite3.Row
    l4 = sqlite3.connect(readonly_uri(args.l4), uri=True)
    l4.row_factory = sqlite3.Row
    l5 = sqlite3.connect(args.l5)
    l5.row_factory = sqlite3.Row
    l5.execute("PRAGMA foreign_keys = ON")

    run_id = (
        f"l5-{args.mode}-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-" + uuid.uuid4().hex[:8]
    )
    try:
        if l5.execute("PRAGMA user_version").fetchone()[0] < 2:
            raise RuntimeError("L5 materializer requires schema >= 2")

        features = fetch_l3_features(l3)
        l4_series, l4_baselines = fetch_l4(l4)
        l3_frontier = l3.execute("SELECT COALESCE(MAX(id),0) FROM derived_features").fetchone()[0]
        l4_frontier = l4.execute("SELECT COALESCE(MAX(id),0) FROM rolling_baselines").fetchone()[0]
        l3_schema = l3.execute("PRAGMA user_version").fetchone()[0]
        l4_schema = l4.execute("PRAGMA user_version").fetchone()[0]

        l5.execute("BEGIN IMMEDIATE")
        now = utc_now()
        register_definition(l5, deviation_def, deviation_sha, "DEVIATION", "L5A robust deviation")
        register_definition(l5, persistence_def, persistence_sha, "PERSISTENCE", "L5B persistence")
        register_definition(l5, trend_def, trend_sha, "TREND", "L5B robust trend")
        register_definition(l5, change_def, change_sha, "CHANGE_POINT", "L5C change detection")
        register_definition(l5, relationship_def, relationship_sha, "RELATIONSHIP", "L5D relationship")
        register_definition(l5, evidence_def, evidence_sha, "EVIDENCE", "L5E evidence strength")

        desired = build_desired(features, {"series": l4_series, "baselines": l4_baselines}, defs)

        # --- reconcile analytics_series ---
        series_key_to_id = {}
        current_series = {row["series_key"]: row["id"] for row in l5.execute("SELECT id,series_key FROM analytics_series WHERE status='CURRENT'")}
        desired_series_keys = set()
        for s in l4_series:
            desired_series_keys.add(s["series_key"])
            if s["series_key"] in current_series:
                series_key_to_id[s["series_key"]] = current_series[s["series_key"]]
            else:
                cursor = l5.execute(
                    """
                    INSERT INTO analytics_series (
                        series_key,l4_series_id,feature_name,scope_type,provider,source_sid,
                        source_class,timezone_name,timezone_offset_seconds,unit,
                        observation_semantics,status,created_at_utc,updated_at_utc
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'CURRENT',?,?)
                    """,
                    (
                        s["series_key"], s["id"], s["feature_name"], s["scope_type"], s["provider"],
                        s["source_sid"], s["source_class"], s["timezone_name"],
                        s["timezone_offset_seconds"], s["unit"], s["observation_semantics"], now, now,
                    ),
                )
                series_key_to_id[s["series_key"]] = cursor.lastrowid
        for key, sid in current_series.items():
            if key not in desired_series_keys:
                l5.execute("UPDATE analytics_series SET status='STALE',updated_at_utc=? WHERE id=?", (now, sid))

        # --- attach sig to desired records and compute series ids for inserts ---
        for rec in desired["deviation"]:
            rec["sig"] = (
                rec["series_key"], rec["window_days"], rec["feature_date"], rec["l3_feature_id"],
                rec["l4_baseline_id"], rec["baseline_maturity"], rec["baseline_median"],
                rec["baseline_mad"], rec["current_value"], rec["absolute_deviation"],
                rec["relative_deviation"], rec["relative_deviation_applicable"],
                rec["robust_standardized_deviation"], rec["robust_z_unavailable_reason"],
                rec["quantile_position"], rec["deviation_side"], rec["deviation_class"],
                rec["evidence_status"], canonical_json(rec["attributes"]),
                l3_ids(rec["l3_inputs"]), baseline_ids(rec["baseline_inputs"]),
            )
        for rec in desired["persistence"]:
            rec["sig"] = (
                rec["series_key"], rec["window_days"], rec["as_of_date"],
                rec["trailing_observation_count"], rec["consecutive_above_typical"],
                rec["consecutive_below_typical"], rec["persistence_class"], rec["evidence_status"],
                canonical_json(rec["attributes"]),
                l3_ids(rec["l3_inputs"]), baseline_ids(rec["baseline_inputs"]),
            )
        for rec in desired["trend"]:
            rec["sig"] = (
                rec["series_key"], rec["as_of_date"], rec["trend_point_count"],
                rec["trend_start_date"], rec["trend_end_date"], rec["theil_sen_slope"],
                rec["spearman_rho"], rec["trend_class"], rec["evidence_status"],
                canonical_json(rec["attributes"]), l3_ids(rec["l3_inputs"]),
            )
        for rec in desired["change"]:
            rec["sig"] = (
                rec["series_key"], rec["as_of_date"], rec["observation_count"],
                rec["candidate_split_date"], rec["shift_magnitude"], rec["change_class"],
                rec["evidence_status"], canonical_json(rec["attributes"]),
                l3_ids(rec["l3_inputs"]),
            )
        for rec in desired["relationship"]:
            rec["sig"] = (
                rec["series_key_a"], rec["series_key_b"], rec["as_of_date"], rec["paired_count"],
                rec["spearman_rho"], rec["relationship_class"], rec["evidence_status"],
                canonical_json(rec["attributes"]), l3_ids(rec["l3_inputs"]),
            )

        # --- insert functions ---
        def ins_deviation(db, rec, now_):
            c = db.execute(
                """
                INSERT INTO deviation_analytics (
                    series_id,window_days,feature_date,l3_feature_id,l4_baseline_id,
                    baseline_maturity,baseline_median,baseline_mad,current_value,
                    absolute_deviation,relative_deviation,relative_deviation_applicable,
                    robust_standardized_deviation,robust_z_unavailable_reason,quantile_position,
                    deviation_side,deviation_class,evidence_status,attributes_json,status,
                    created_at_utc,updated_at_utc
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'CURRENT',?,?)
                """,
                (
                    series_key_to_id[rec["series_key"]], rec["window_days"], rec["feature_date"],
                    rec["l3_feature_id"], rec["l4_baseline_id"], rec["baseline_maturity"],
                    rec["baseline_median"], rec["baseline_mad"], rec["current_value"],
                    rec["absolute_deviation"], rec["relative_deviation"],
                    rec["relative_deviation_applicable"], rec["robust_standardized_deviation"],
                    rec["robust_z_unavailable_reason"], rec["quantile_position"],
                    rec["deviation_side"], rec["deviation_class"], rec["evidence_status"],
                    canonical_json(rec["attributes"]), now_, now_,
                ),
            )
            return c.lastrowid

        def ins_persistence(db, rec, now_):
            c = db.execute(
                """
                INSERT INTO persistence_analytics (
                    series_id,window_days,as_of_date,trailing_observation_count,
                    consecutive_above_typical,consecutive_below_typical,persistence_class,
                    evidence_status,attributes_json,status,created_at_utc,updated_at_utc
                ) VALUES (?,?,?,?,?,?,?,?,?,'CURRENT',?,?)
                """,
                (
                    series_key_to_id[rec["series_key"]], rec["window_days"], rec["as_of_date"],
                    rec["trailing_observation_count"], rec["consecutive_above_typical"],
                    rec["consecutive_below_typical"], rec["persistence_class"],
                    rec["evidence_status"], canonical_json(rec["attributes"]), now_, now_,
                ),
            )
            return c.lastrowid

        def ins_trend(db, rec, now_):
            c = db.execute(
                """
                INSERT INTO trend_analytics (
                    series_id,as_of_date,trend_point_count,trend_start_date,trend_end_date,
                    theil_sen_slope,spearman_rho,trend_class,evidence_status,attributes_json,
                    status,created_at_utc,updated_at_utc
                ) VALUES (?,?,?,?,?,?,?,?,?,?,'CURRENT',?,?)
                """,
                (
                    series_key_to_id[rec["series_key"]], rec["as_of_date"], rec["trend_point_count"],
                    rec["trend_start_date"], rec["trend_end_date"], rec["theil_sen_slope"],
                    rec["spearman_rho"], rec["trend_class"], rec["evidence_status"],
                    canonical_json(rec["attributes"]), now_, now_,
                ),
            )
            return c.lastrowid

        def ins_change(db, rec, now_):
            c = db.execute(
                """
                INSERT INTO change_point_analytics (
                    series_id,as_of_date,observation_count,candidate_split_date,shift_magnitude,
                    change_class,evidence_status,attributes_json,status,created_at_utc,updated_at_utc
                ) VALUES (?,?,?,?,?,?,?,?,'CURRENT',?,?)
                """,
                (
                    series_key_to_id[rec["series_key"]], rec["as_of_date"], rec["observation_count"],
                    rec["candidate_split_date"], rec["shift_magnitude"], rec["change_class"],
                    rec["evidence_status"], canonical_json(rec["attributes"]), now_, now_,
                ),
            )
            return c.lastrowid

        def ins_relationship(db, rec, now_):
            c = db.execute(
                """
                INSERT INTO relationship_analytics (
                    series_id_a,series_id_b,as_of_date,paired_count,spearman_rho,
                    relationship_class,evidence_status,attributes_json,status,created_at_utc,updated_at_utc
                ) VALUES (?,?,?,?,?,?,?,?,'CURRENT',?,?)
                """,
                (
                    series_key_to_id[rec["series_key_a"]], series_key_to_id[rec["series_key_b"]],
                    rec["as_of_date"], rec["paired_count"], rec["spearman_rho"],
                    rec["relationship_class"], rec["evidence_status"],
                    canonical_json(rec["attributes"]), now_, now_,
                ),
            )
            return c.lastrowid

        def ins_inputs(db, analytic_type, analytic_id, rec, now_):
            for (fid, fname, fdate, role) in rec["l3_inputs"]:
                db.execute(
                    "INSERT INTO analytics_l3_inputs VALUES (?,?,?,?,?,?,?)",
                    (analytic_type, analytic_id, fid, fname, fdate, role, now_),
                )
            for (bid, basof, bwin) in rec["baseline_inputs"]:
                db.execute(
                    "INSERT INTO analytics_baseline_inputs VALUES (?,?,?,?,?,?)",
                    (analytic_type, analytic_id, bid, basof, bwin, now_),
                )

        # Wrap insert functions to also write provenance inputs.
        def make_inserter(insert_fn, analytic_type):
            def wrapped(db, rec, now_):
                aid = insert_fn(db, rec, now_)
                ins_inputs(db, analytic_type, aid, rec, now_)
                return aid
            return wrapped

        counts = {}
        counts["deviation"] = reconcile_table(
            l5, "deviation_analytics", desired["deviation"], _load_current_deviation,
            make_inserter(ins_deviation, "DEVIATION"), now, "DEVIATION"
        )
        counts["persistence"] = reconcile_table(
            l5, "persistence_analytics", desired["persistence"], _load_current_persistence,
            make_inserter(ins_persistence, "PERSISTENCE"), now, "PERSISTENCE"
        )
        counts["trend"] = reconcile_table(
            l5, "trend_analytics", desired["trend"], _load_current_trend,
            make_inserter(ins_trend, "TREND"), now, "TREND"
        )
        counts["change"] = reconcile_table(
            l5, "change_point_analytics", desired["change"], _load_current_change,
            make_inserter(ins_change, "CHANGE_POINT"), now, "CHANGE_POINT"
        )
        counts["relationship"] = reconcile_table(
            l5, "relationship_analytics", desired["relationship"], _load_current_relationship,
            make_inserter(ins_relationship, "RELATIONSHIP"), now, "RELATIONSHIP"
        )

        result = {
            "status": "PASS",
            "mode": args.mode.upper(),
            "run_id": run_id,
            "input_features": len(features),
            "l4_series": len(l4_series),
            "l4_baselines": len(l4_baselines),
            "desired": {k: len(v) for k, v in desired.items()},
            "inserted": {k: v[0] for k, v in counts.items()},
            "stale": {k: v[1] for k, v in counts.items()},
            "l3_checkpoint": l3_frontier,
            "l4_checkpoint": l4_frontier,
        }
        l5.execute(
            """
            INSERT INTO pipeline_runs (
                run_id,mode,status,source_l3_path,source_l4_path,source_l3_schema,
                source_l4_schema,started_at_utc,finished_at_utc,details_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, "FULL_REBUILD" if args.mode == "full" else "INCREMENTAL", "PASS",
                str(Path(args.l3).resolve()), str(Path(args.l4).resolve()),
                l3_schema, l4_schema, now, now, canonical_json(result),
            ),
        )
        l5.execute(
            """
            INSERT INTO processing_checkpoints (
                pipeline_name,last_l3_feature_id,last_l4_baseline_id,last_successful_run_id,updated_at_utc
            ) VALUES (?,?,?,?,?)
            ON CONFLICT(pipeline_name) DO UPDATE SET
                last_l3_feature_id=excluded.last_l3_feature_id,
                last_l4_baseline_id=excluded.last_l4_baseline_id,
                last_successful_run_id=excluded.last_successful_run_id,
                updated_at_utc=excluded.updated_at_utc
            """,
            (PIPELINE, l3_frontier, l4_frontier, run_id, now),
        )
        if l5.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("L5 foreign key check failed")
        l5.commit()
        print(json.dumps(result, indent=2))
    except Exception:
        l5.rollback()
        raise
    finally:
        l3.close()
        l4.close()
        l5.close()


if __name__ == "__main__":
    main()
