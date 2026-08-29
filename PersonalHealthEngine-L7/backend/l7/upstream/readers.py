"""Read-only access to the sealed layer databases.

Every function here opens upstream data with `mode=ro` semantics (connections are passed
in already opened read-only by the caller). No write ever happens through this module.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from l7.rendering.reference_ranges import SAFETY_FEATURES, reference_for, safety_breach
from l7.rendering.labels import (
    baseline_maturity_label,
    deviation_direction_label,
    evidence_status_label,
    feature_label,
    format_health_value,
    metric_label,
)

SYMPTOM_CONTEXT_TYPES = (
    "ILLNESS", "FEVER", "SORE_THROAT", "NASAL_CONGESTION", "HEADACHE", "MEDICATION",
)

HEALTH_DATA_METRICS = {
    "SLEEP_DURATION": ("sleep_source_episode.vendor_sleep_like_duration_seconds", "睡眠时长"),
    "STEPS": ("steps.daily.sum", "步数"),
    "RESTING_HEART_RATE": ("resting_heart_rate.daily.value", "静息心率"),
}

SOURCE_LABELS = {
    "XIAOMI_GENERATED": "小米生成来源",
    "NUMERIC_SOURCE": "数值来源",
}

HEALTH_METRIC_OVERVIEW = (
    ("steps", "步数", "steps.daily.sum"),
    ("active_calories", "活动消耗", "calories.daily.sum"),
    ("sleep", "睡眠", "sleep_source_episode.vendor_sleep_like_duration_seconds"),
    ("heart_rate", "心率", "heart_rate.daily.mean"),
    ("resting_heart_rate", "静息心率", "resting_heart_rate.daily.value"),
    ("spo2", "血氧", "spo2.daily.mean"),
    ("stress", "压力", "xiaomi_stress_score.daily.mean"),
    ("workouts", "运动记录", None),
)

# The canonical per-metric feature that represents the metric to the user. Diagnostic
# sub-features (coverage bucket counts, sleep-stage segment counts, stage proportions)
# stay in the sealed bundle for reasoning but never surface as user-facing evidence.
# Sleep awake duration is the one sub-feature users genuinely care about (night-time
# wake-ups), so it is surfaced as a secondary line beneath the sleep primary.
PRIMARY_FEATURE_NAMES = {
    feature_name for _, _, feature_name in HEALTH_METRIC_OVERVIEW if feature_name is not None
}
SECONDARY_FEATURE_NAMES = {
    "sleep_source_episode.vendor_awake_duration_seconds",
}
USER_FACING_FEATURE_NAMES = PRIMARY_FEATURE_NAMES | SECONDARY_FEATURE_NAMES


def latest_analysis_date(l5: sqlite3.Connection) -> str | None:
    row = l5.execute(
        "SELECT MAX(feature_date) FROM deviation_analytics WHERE status='CURRENT'"
    ).fetchone()
    return row[0]


def _health_value_text(value: float, unit: str) -> str:
    if unit == "seconds":
        total_minutes = round(value / 60)
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours}小时{minutes}分钟"
    if unit == "steps":
        return f"{round(value):,}步"
    if unit == "bpm":
        return f"{value:.1f}".rstrip("0").rstrip(".") + "次/分钟"
    if unit == "percent":
        return f"{value:.1f}".rstrip("0").rstrip(".") + "%"
    if unit == "vendor_calories":
        return f"{value:.0f}小米卡路里单位"
    if unit == "vendor_score":
        return f"{value:.1f}".rstrip("0").rstrip(".") + "（小米原始指标）"
    return f"{value:g} {unit}"


def evidence_freshness(feature_date: str, reference_date: str) -> tuple[int, str]:
    age_days = max(
        (date.fromisoformat(reference_date) - date.fromisoformat(feature_date)).days,
        0,
    )
    label = "当日数据" if age_days == 0 else f"{age_days} 天前的数据"
    return age_days, label


def health_metric_overviews(
    l3: sqlite3.Connection,
    reference_date: str,
    used_facts: dict[str, dict],
    limit: int = 28,
) -> list[dict]:
    """Return the fixed user-facing health metric catalogue without coverage counts."""
    result = []
    for key, label, feature_name in HEALTH_METRIC_OVERVIEW:
        used = used_facts.get(feature_name or "")
        if feature_name is None:
            result.append({
                "key": key,
                "label": label,
                "feature_name": None,
                "reference": None,
                "value_display": "暂无数据",
                "data_date": None,
                "freshness_days": None,
                "freshness_status": "UNAVAILABLE",
                "freshness_label": "暂无可用数据",
                "used_in_judgment": False,
                "deviation_label": None,
                "baseline_median": None,
                "baseline_value_display": None,
                "availability_note": "当前数据源尚未提供可用的运动记录。",
                "unit": None,
                "series": [],
            })
            continue

        latest = l3.execute(
            "SELECT local_date, value_num, unit, source_sid FROM derived_features "
            "WHERE feature_name=? AND status='CURRENT' AND value_num IS NOT NULL "
            "ORDER BY local_date DESC, id DESC LIMIT 1",
            (feature_name,),
        ).fetchone()
        if latest is None:
            result.append({
                "key": key,
                "label": label,
                "feature_name": feature_name,
                "reference": reference_for(key),
                "value_display": "暂无数据",
                "data_date": None,
                "freshness_days": None,
                "freshness_status": "UNAVAILABLE",
                "freshness_label": "暂无可用数据",
                "used_in_judgment": used is not None,
                "deviation_label": used.get("deviation_label") if used else None,
                "baseline_median": used.get("baseline_median") if used else None,
                "baseline_value_display": (
                    used.get("baseline_value_display") if used else None
                ),
                "availability_note": "尚未采集到这项健康数据。",
                "unit": None,
                "series": [],
            })
            continue

        latest = dict(latest)
        age_days, age_label = evidence_freshness(
            latest["local_date"], reference_date,
        )
        freshness_status = (
            "TODAY" if age_days == 0 else "RECENT" if age_days <= 2 else "STALE"
        )
        freshness_label = (
            "今日数据"
            if freshness_status == "TODAY"
            else age_label
            if freshness_status == "RECENT"
            else f"数据需更新 · 最后记录 {latest['local_date']}"
        )
        rows = l3.execute(
            "SELECT local_date, value_num FROM derived_features "
            "WHERE feature_name=? AND source_sid=? AND status='CURRENT' "
            "AND value_num IS NOT NULL ORDER BY local_date DESC, id DESC LIMIT ?",
            (feature_name, latest["source_sid"], limit),
        ).fetchall()
        result.append({
            "key": key,
            "label": label,
            "feature_name": feature_name,
            "reference": reference_for(key),
            "value_display": format_health_value(
                feature_name, latest["value_num"], latest["unit"],
            ),
            "data_date": latest["local_date"],
            "freshness_days": age_days,
            "freshness_status": freshness_status,
            "freshness_label": freshness_label,
            "used_in_judgment": used is not None,
            "deviation_label": used.get("deviation_label") if used else None,
            "baseline_median": used.get("baseline_median") if used else None,
            "baseline_value_display": (
                used.get("baseline_value_display") if used else None
            ),
            "availability_note": None,
            "unit": latest["unit"],
            "series": [
                {"local_date": row["local_date"], "value_num": row["value_num"]}
                for row in reversed(rows)
            ],
        })
    return result


def deterministic_health_data_query(
    l3: sqlite3.Connection,
    metric: str,
    time_range: str | None,
    aggregation: str | None,
    as_of_date: str,
) -> dict | None:
    """Answer a canonical metric query without letting a model calculate engine facts.

    L3 source series remain isolated. Values from NUMERIC_SOURCE and XIAOMI_GENERATED are
    never combined into one synthetic number.
    """
    metric_def = HEALTH_DATA_METRICS.get(metric)
    if metric_def is None:
        return None
    feature_name, label = metric_def
    window_days = {
        "LAST_7_DAYS": 7,
        "LAST_30_DAYS": 30,
        "RECENT": 7,
    }.get(time_range)
    params: list[Any] = [feature_name, as_of_date]
    window_sql = ""
    if window_days:
        window_sql = " AND local_date >= date(?, ?)"
        params.extend([as_of_date, f"-{window_days - 1} days"])
    rows = [
        dict(row)
        for row in l3.execute(
            "SELECT id, local_date, value_num, unit, provider, source_class, source_sid, "
            "coverage_status FROM derived_features WHERE feature_name=? AND status='CURRENT' "
            "AND value_num IS NOT NULL AND local_date <= ?" + window_sql +
            " ORDER BY local_date, id",
            params,
        ).fetchall()
    ]
    if not rows:
        return None

    if aggregation in (None, "LATEST"):
        data_date = max(row["local_date"] for row in rows)
        selected = [row for row in rows if row["local_date"] == data_date]
        values = [{
            "source_class": row["source_class"],
            "source_key": f"{row['source_class']}:{row['source_sid']}",
            "source_label": SOURCE_LABELS.get(row["source_class"], "独立数据来源"),
            "value": row["value_num"],
            "value_text": _health_value_text(row["value_num"], row["unit"]),
            "unit": row["unit"],
            "date": row["local_date"],
            "count": 1,
            "row_ids": [row["id"]],
        } for row in selected]
        aggregation = "LATEST"
    else:
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            grouped.setdefault((row["source_class"], row["source_sid"]), []).append(row)
        values = []
        for (source_class, _source_sid), source_rows in sorted(grouped.items()):
            first = source_rows[0]
            last = source_rows[-1]
            if aggregation == "AVERAGE":
                value = sum(row["value_num"] for row in source_rows) / len(source_rows)
                value_text = _health_value_text(value, first["unit"])
            elif aggregation == "TREND":
                value = last["value_num"] - first["value_num"]
                direction = "上升" if value > 0 else "下降" if value < 0 else "持平"
                value_text = (
                    f"从{_health_value_text(first['value_num'], first['unit'])}"
                    f"到{_health_value_text(last['value_num'], last['unit'])}，{direction}"
                    f"{_health_value_text(abs(value), first['unit'])}"
                )
            else:
                return None
            values.append({
                "source_class": source_class,
                "source_key": f"{source_class}:{_source_sid}",
                "source_label": SOURCE_LABELS.get(source_class, "独立数据来源"),
                "value": value,
                "value_text": value_text,
                "unit": first["unit"],
                "date": last["local_date"],
                "start_date": first["local_date"],
                "count": len(source_rows),
                "row_ids": [row["id"] for row in source_rows],
            })
        data_date = max(row["local_date"] for row in rows)

    source_count = len({v["source_key"] for v in values})
    if aggregation == "LATEST" and len(values) == 1:
        direct = f"PHE 记录到你在 {data_date} 的{label}是{values[0]['value_text']}。"
    elif aggregation == "LATEST":
        detail = "；".join(f"{v['source_label']} {v['value_text']}" for v in values)
        direct = f"PHE 在 {data_date} 记录到多个独立来源，按来源隔离规则分别是：{detail}。"
    elif aggregation == "AVERAGE":
        detail = "；".join(
            f"{v['source_label']}平均{v['value_text']}（{v['count']}条记录）" for v in values
        )
        direct = f"在所查时间窗内，PHE 的独立来源分别记录为：{detail}。这些来源未被合并。"
    else:
        detail = "；".join(f"{v['source_label']}{v['value_text']}" for v in values)
        direct = f"在所查时间窗内，PHE 记录的{label}趋势分别是：{detail}。"

    public_values = [{key: value for key, value in item.items()
                      if key not in {"row_ids", "value", "source_key"}}
                     for item in values]
    return {
        "direct_answer": direct,
        "label": label,
        "feature_name": feature_name,
        "aggregation": aggregation,
        "data_date": data_date,
        "source_count": source_count,
        "values": public_values,
        "row_ids": [row_id for item in values for row_id in item["row_ids"]],
    }


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


def _rows_by_ids(connection: sqlite3.Connection, sql: str, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return [dict(r) for r in connection.execute(sql.format(ids=placeholders), ids).fetchall()]


def exact_bundle_evidence(
    l6: sqlite3.Connection,
    l5: sqlite3.Connection,
    l4: sqlite3.Connection,
    l3: sqlite3.Connection,
    bundle_id: int,
    bundle: dict,
    analysis_date: str,
    *,
    freshness_date: str,
) -> list[dict]:
    """Resolve bundle evidence through its exact L5 -> L3/L4 provenance chain."""
    provenance = l6.execute(
        "SELECT upstream_id FROM reasoning_provenance WHERE subject_type='EVIDENCE_BUNDLE'"
        " AND subject_id=? AND upstream_layer='L5' AND upstream_type='DEVIATION'",
        (bundle_id,),
    ).fetchall()
    l5_ids = [int(r[0]) for r in provenance]
    deviations = _rows_by_ids(
        l5,
        """
        SELECT d.*, s.feature_name, s.unit, s.source_sid, s.source_class,
               s.provider, s.observation_semantics
        FROM deviation_analytics d JOIN analytics_series s ON s.id=d.series_id
        WHERE d.id IN ({ids}) ORDER BY d.id
        """,
        l5_ids,
    )
    buckets: dict[tuple, list[dict]] = {}
    for row in deviations:
        key = (
            row["feature_name"], row["feature_date"], row["window_days"],
            row["source_class"], row["deviation_class"],
        )
        buckets.setdefault(key, []).append(row)

    facts: list[dict] = []
    for item in bundle.get("deviations", []):
        if item.get("deviation_class") not in (
            "ABOVE_TYPICAL_RANGE", "BELOW_TYPICAL_RANGE",
        ):
            continue
        if item.get("feature_name") not in USER_FACING_FEATURE_NAMES:
            continue
        key = (
            item.get("feature_name"), item.get("feature_date"), item.get("window_days"),
            item.get("source_class"), item.get("deviation_class"),
        )
        matches = buckets.get(key) or []
        if not matches:
            continue
        deviation = matches.pop(0)
        l3_row = l3.execute(
            "SELECT * FROM derived_features WHERE id=?", (deviation["l3_feature_id"],)
        ).fetchone()
        l4_row = l4.execute(
            """
            SELECT b.*, s.feature_name, s.source_sid, s.unit
            FROM rolling_baselines b JOIN baseline_series s ON s.id=b.series_id
            WHERE b.id=?
            """,
            (deviation["l4_baseline_id"],),
        ).fetchone()
        if l3_row is None or l4_row is None:
            continue
        feature_date = deviation["feature_date"]
        age_days, freshness = evidence_freshness(feature_date, freshness_date)
        current_display = format_health_value(
            deviation["feature_name"], deviation["current_value"], deviation["unit"],
        )
        baseline_display = format_health_value(
            deviation["feature_name"], deviation["baseline_median"], deviation["unit"],
        )
        label = feature_label(deviation["feature_name"])
        direction_label = deviation_direction_label(deviation["deviation_class"])
        facts.append({
            "metric": item.get("metric"),
            "feature_name": deviation["feature_name"],
            "feature_label": label,
            "feature_date": feature_date,
            "freshness_days": age_days,
            "freshness_label": freshness,
            "deviation_class": deviation["deviation_class"],
            "deviation_label": direction_label,
            "baseline_maturity": deviation["baseline_maturity"],
            "baseline_maturity_label": baseline_maturity_label(deviation["baseline_maturity"]),
            "evidence_status": deviation["evidence_status"],
            "evidence_status_label": evidence_status_label(deviation["evidence_status"]),
            "current_value": deviation["current_value"],
            "baseline_median": deviation["baseline_median"],
            "current_value_display": current_display,
            "baseline_value_display": baseline_display,
            "unit": deviation["unit"],
            "text": (
                f"{label}{direction_label}：{current_display}，个人近期基线约 "
                f"{baseline_display}（{feature_date}，{freshness}）"
            ),
            "source_sid": deviation["source_sid"],
            "l5_deviation_id": deviation["id"],
            "l3_feature_id": deviation["l3_feature_id"],
            "l4_baseline_id": deviation["l4_baseline_id"],
            "deviation": deviation,
            "feature": dict(l3_row),
            "baseline": dict(l4_row),
        })
    # Primary metrics lead; secondary sub-features (awake duration) follow. The display
    # label keeps secondaries self-explanatory while primaries keep the metric name.
    facts.sort(key=lambda f: 0 if f["feature_name"] in PRIMARY_FEATURE_NAMES else 1)
    for fact in facts:
        if fact["feature_name"] in PRIMARY_FEATURE_NAMES:
            metric = str(fact.get("metric") or "").strip()
            fact["display_label"] = metric_label(metric) if metric else fact["feature_label"]
        else:
            fact["display_label"] = fact["feature_label"]
    return facts


def exact_feature_series(
    l3: sqlite3.Connection, feature_name: str, source_sid: str, limit: int = 28,
) -> list[dict]:
    rows = l3.execute(
        """
        SELECT id, local_date, value_num, value_code, unit, sample_count, coverage_status,
               provider, source_sid, source_class
        FROM derived_features
        WHERE feature_name=? AND source_sid=? AND status='CURRENT'
        ORDER BY local_date DESC LIMIT ?
        """,
        (feature_name, source_sid, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def safety_floor_breaches(l3: sqlite3.Connection) -> list[dict]:
    """安全底座：检查带硬危险阈值指标的最新 CURRENT 值（与个人基线无关）。

    返回越界列表（空 = 安全），用于把今日状态升级为「健康安全关注」。"""
    breaches: list[dict] = []
    for feature_name, metric in SAFETY_FEATURES.items():
        row = l3.execute(
            "SELECT value_num, local_date FROM derived_features"
            " WHERE feature_name=? AND status='CURRENT' AND value_num IS NOT NULL"
            " ORDER BY local_date DESC, id DESC LIMIT 1",
            (feature_name,),
        ).fetchone()
        if row is None:
            continue
        direction = safety_breach(metric, row["value_num"])
        if direction:
            breaches.append({
                "metric": metric,
                "feature_name": feature_name,
                "value": row["value_num"],
                "local_date": row["local_date"],
                "direction": direction,
            })
    return breaches
