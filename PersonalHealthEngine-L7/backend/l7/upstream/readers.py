"""Read-only access to the sealed layer databases.

Every function here opens upstream data with `mode=ro` semantics (connections are passed
in already opened read-only by the caller). No write ever happens through this module.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

SYMPTOM_CONTEXT_TYPES = ("ILLNESS", "FEVER", "SORE_THROAT", "NASAL_CONGESTION", "MEDICATION")


def latest_analysis_date(l5: sqlite3.Connection) -> str | None:
    row = l5.execute(
        "SELECT MAX(feature_date) FROM deviation_analytics WHERE status='CURRENT'"
    ).fetchone()
    return row[0]


def upstream_signature(
    l3: sqlite3.Connection,
    l4: sqlite3.Connection,
    l5: sqlite3.Connection,
    l6: sqlite3.Connection,
    local_date: str,
) -> dict:
    """Deterministic fingerprint of everything that can change the Today judgment.

    Used by the Recompute Threshold: if this signature is unchanged, no bundle assembly,
    no model call, no re-rendering is warranted at all.
    """
    return {
        "local_date": local_date,
        "l3_max_feature_id": l3.execute("SELECT COALESCE(MAX(id),0) FROM derived_features").fetchone()[0],
        "l4_max_baseline_id": l4.execute("SELECT COALESCE(MAX(id),0) FROM rolling_baselines").fetchone()[0],
        "l5_max_deviation_id": l5.execute("SELECT COALESCE(MAX(id),0) FROM deviation_analytics").fetchone()[0],
        "l5_max_feature_date": l5.execute(
            "SELECT COALESCE(MAX(feature_date),'') FROM deviation_analytics WHERE status='CURRENT'"
        ).fetchone()[0],
        "l6_context_max_id": l6.execute(
            "SELECT COALESCE(MAX(id),0) FROM personal_context WHERE status='CURRENT'"
        ).fetchone()[0],
        "l6_feedback_max_id": l6.execute("SELECT COALESCE(MAX(id),0) FROM user_feedback").fetchone()[0],
        "l6_daily_max_id": l6.execute("SELECT COALESCE(MAX(id),0) FROM daily_reasoning").fetchone()[0],
    }


def read_recent_context(l6: sqlite3.Connection, analysis_date: str) -> list[dict]:
    rows = l6.execute(
        "SELECT context_type, context_date, body_part, severity FROM personal_context "
        "WHERE status='CURRENT' AND context_date <= ? ORDER BY context_date DESC LIMIT 20",
        (analysis_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def similar_cases(l6: sqlite3.Connection, recent_context: list[dict], analysis_date: str) -> list[dict]:
    types = [c["context_type"] for c in recent_context]
    if not types:
        return []
    ph = ",".join("?" for _ in types)
    rows = l6.execute(
        "SELECT context_type, context_date FROM personal_context WHERE status='CURRENT' "
        f"AND context_type IN ({ph}) AND context_date < ? ORDER BY context_date DESC LIMIT 10",
        (*types, analysis_date),
    ).fetchall()
    return [dict(r) for r in rows]


def read_current_bundle(l6: sqlite3.Connection, analysis_date: str) -> dict | None:
    row = l6.execute(
        "SELECT id, bundle_sha256, bundle_json FROM evidence_bundles "
        "WHERE analysis_date=? AND status='CURRENT' ORDER BY id DESC LIMIT 1",
        (analysis_date,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "bundle_sha256": row["bundle_sha256"], "bundle": json.loads(row["bundle_json"])}


def read_current_daily_reasoning(l6: sqlite3.Connection, analysis_date: str) -> dict | None:
    row = l6.execute(
        "SELECT * FROM daily_reasoning WHERE analysis_date=? AND status='CURRENT' ORDER BY id DESC LIMIT 1",
        (analysis_date,),
    ).fetchone()
    return dict(row) if row else None


def read_hypotheses(l6: sqlite3.Connection, bundle_id: int) -> list[dict]:
    rows = l6.execute(
        "SELECT * FROM hypotheses WHERE evidence_bundle_id=? AND status='CURRENT' ORDER BY rank",
        (bundle_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def read_latest_daily_medical_review(l6: sqlite3.Connection) -> dict | None:
    row = l6.execute(
        "SELECT * FROM medical_reviews WHERE subject_type='DAILY_REASONING' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def symptom_context_active(l6: sqlite3.Connection, analysis_date: str, lookback_days: int = 7) -> bool:
    """True when a CURRENT user-reported symptom context exists in the analysis window."""
    ph = ",".join("?" for _ in SYMPTOM_CONTEXT_TYPES)
    row = l6.execute(
        "SELECT COUNT(*) FROM personal_context WHERE status='CURRENT' "
        f"AND context_type IN ({ph}) AND context_date <= ? AND date(context_date) >= date(?, ?)",
        (*SYMPTOM_CONTEXT_TYPES, analysis_date, analysis_date, f"-{lookback_days} days"),
    ).fetchone()
    return row[0] > 0


def read_patterns(l6: sqlite3.Connection) -> list[dict]:
    rows = l6.execute("SELECT * FROM personal_patterns ORDER BY support_count DESC, id").fetchall()
    return [dict(r) for r in rows]


def read_daily_reasoning_history(l6: sqlite3.Connection, analysis_date: str | None = None) -> list[dict]:
    if analysis_date:
        rows = l6.execute(
            "SELECT * FROM daily_reasoning WHERE analysis_date=? ORDER BY id DESC", (analysis_date,)
        ).fetchall()
    else:
        rows = l6.execute("SELECT * FROM daily_reasoning ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Evidence Level-3 drill-down (L3/L4/L5 detail for charts and provenance)
# ---------------------------------------------------------------------------

def deviation_detail(l5: sqlite3.Connection, feature_name: str, limit: int = 28) -> list[dict]:
    rows = l5.execute(
        """
        SELECT d.feature_date, d.current_value, d.baseline_median, d.baseline_mad,
               d.robust_standardized_deviation, d.deviation_class, d.baseline_maturity,
               d.evidence_status, s.feature_name, s.unit
        FROM deviation_analytics d JOIN analytics_series s ON s.id = d.series_id
        WHERE d.status='CURRENT' AND s.feature_name=?
        ORDER BY d.feature_date DESC LIMIT ?
        """,
        (feature_name, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def feature_series(l3: sqlite3.Connection, feature_name: str, limit: int = 28) -> list[dict]:
    rows = l3.execute(
        """
        SELECT local_date, value_num, value_code, unit, sample_count, coverage_status,
               provider, source_class
        FROM derived_features
        WHERE feature_name=? AND status='CURRENT'
        ORDER BY local_date DESC LIMIT ?
        """,
        (feature_name, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def baseline_detail(l4: sqlite3.Connection, feature_name: str, as_of_date: str | None = None) -> list[dict]:
    sql = (
        """
        SELECT b.as_of_date, b.window_days, b.observation_count, b.calendar_coverage,
               b.mean, b.median, b.mad, b.q10, b.q25, b.q50, b.q75, b.q90, b.maturity,
               s.feature_name, s.unit
        FROM rolling_baselines b JOIN baseline_series s ON s.id = b.series_id
        WHERE s.feature_name=? AND b.status='CURRENT'
        """
    )
    params: list[Any] = [feature_name]
    if as_of_date:
        sql += " AND b.as_of_date <= ? ORDER BY b.as_of_date DESC LIMIT 3"
        params.append(as_of_date)
    else:
        sql += " ORDER BY b.as_of_date DESC LIMIT 3"
    rows = l4.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
