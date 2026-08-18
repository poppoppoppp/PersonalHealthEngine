"""Layer 6 deterministic Evidence Bundle assembler.

Reads sealed L3/L4/L5 (read-only) and produces a hash-addressed, structured bundle plus a
provenance list. The reasoning model only ever sees this bundle, never the databases.
"""

from collections import Counter
from datetime import date

from l6_core_v0_1 import add_days, canonical_json, parse_date


def metric_from_feature(feature_name):
    base = feature_name.split(".")[0]
    return "sleep" if base == "sleep_source_episode" else base


def assemble_evidence(l3, l4, l5, analysis_date, recent_context, recent_feedback, similar_cases, lookback_days=7):
    analysis_d = parse_date(analysis_date)
    window_start = add_days(analysis_d, -lookback_days).isoformat()

    series = {}
    for row in l5.execute("SELECT id, feature_name, source_class, observation_semantics FROM analytics_series WHERE status='CURRENT'"):
        series[row["id"]] = dict(row)

    # Current-day deviations (latest feature_date <= analysis_date)
    dev_rows = [
        dict(row)
        for row in l5.execute(
            """
            SELECT d.*, s.feature_name, s.source_class, s.source_sid
            FROM deviation_analytics d JOIN analytics_series s ON s.id=d.series_id
            WHERE d.status='CURRENT' AND d.feature_date <= ? AND d.window_days = 28
            ORDER BY d.feature_date DESC, d.id
            """,
            (analysis_date,),
        )
    ]
    data_date = dev_rows[0]["feature_date"] if dev_rows else analysis_date

    # Current snapshot: the latest deviation per (feature_name, source_sid) within the window.
    current_by_series = {}
    for d in dev_rows:
        if d["feature_date"] < window_start:
            continue
        key = (d["feature_name"], d["source_sid"])
        if key not in current_by_series:
            current_by_series[key] = d

    current = []
    provenance = []
    for d in current_by_series.values():
        current.append({
            "metric": metric_from_feature(d["feature_name"]),
            "feature_name": d["feature_name"],
            "source_class": d["source_class"],
            "feature_date": d["feature_date"],
            "window_days": d["window_days"],
            "deviation_class": d["deviation_class"],
            "baseline_maturity": d["baseline_maturity"],
            "evidence_status": d["evidence_status"],
        })
        provenance.append({"layer": "L5", "type": "DEVIATION", "id": d["id"]})
        provenance.append({"layer": "L3", "type": "FEATURE", "id": d["l3_feature_id"]})
        provenance.append({"layer": "L4", "type": "BASELINE", "id": d["l4_baseline_id"]})

    recent = [
        {
            "metric": metric_from_feature(d["feature_name"]),
            "feature_date": d["feature_date"],
            "deviation_class": d["deviation_class"],
        }
        for d in dev_rows if d["feature_date"] >= window_start
    ]

    persistence = []
    for row in l5.execute(
        """
        SELECT p.*, s.feature_name FROM persistence_analytics p
        JOIN analytics_series s ON s.id=p.series_id
        WHERE p.status='CURRENT' AND p.as_of_date <= ?
        """,
        (analysis_date,),
    ):
        persistence.append({
            "feature_name": row["feature_name"],
            "persistence_class": row["persistence_class"],
            "consecutive_above_typical": row["consecutive_above_typical"],
            "consecutive_below_typical": row["consecutive_below_typical"],
        })
        provenance.append({"layer": "L5", "type": "PERSISTENCE", "id": row["id"]})

    trends = []
    for row in l5.execute(
        """
        SELECT t.*, s.feature_name FROM trend_analytics t
        JOIN analytics_series s ON s.id=t.series_id
        WHERE t.status='CURRENT' AND t.as_of_date <= ?
        """,
        (analysis_date,),
    ):
        trends.append({"feature_name": row["feature_name"], "trend_class": row["trend_class"]})
        provenance.append({"layer": "L5", "type": "TREND", "id": row["id"]})

    change = []
    for row in l5.execute(
        """
        SELECT c.*, s.feature_name FROM change_point_analytics c
        JOIN analytics_series s ON s.id=c.series_id
        WHERE c.status='CURRENT' AND c.as_of_date <= ?
        """,
        (analysis_date,),
    ):
        change.append({"feature_name": row["feature_name"], "change_class": row["change_class"]})
        provenance.append({"layer": "L5", "type": "CHANGE_POINT", "id": row["id"]})

    relationships = []
    for row in l5.execute(
        """
        SELECT r.*, sa.feature_name fa, sb.feature_name fb FROM relationship_analytics r
        JOIN analytics_series sa ON sa.id=r.series_id_a
        JOIN analytics_series sb ON sb.id=r.series_id_b
        WHERE r.status='CURRENT' AND r.as_of_date <= ?
        """,
        (analysis_date,),
    ):
        relationships.append({
            "pair": [row["fa"], row["fb"]],
            "relationship_class": row["relationship_class"],
            "spearman_rho": row["spearman_rho"],
        })
        provenance.append({"layer": "L5", "type": "RELATIONSHIP", "id": row["id"]})

    maturity_summary = dict(Counter(d["baseline_maturity"] for d in current if d["baseline_maturity"]))

    missing = ["HRV data unavailable in upstream sources", "body temperature unavailable (self-report only)"]
    if not recent_context:
        missing.append("no user-reported context in the analysis window")

    bundle = {
        "analysis_date": analysis_date,
        "data_date": data_date,
        "deviations": current,
        "recent_deviations": recent,
        "persistence": persistence,
        "trends": trends,
        "change": change,
        "relationships": relationships,
        "baseline_maturity_summary": maturity_summary,
        "recent_context": [{"context_type": c.get("context_type"), "context_date": c.get("context_date")} for c in recent_context],
        "recent_feedback": recent_feedback,
        "similar_cases": similar_cases,
        "missing_evidence": missing,
    }
    return bundle, provenance


def bundle_sha256(bundle):
    return __import__("hashlib").sha256(canonical_json(bundle).encode("utf-8")).hexdigest()
