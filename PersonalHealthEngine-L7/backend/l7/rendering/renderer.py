"""Deterministic Today renderer (semantic stability guarantee).

Presentation-only: this module never alters a judgment. For identical inputs
(daily_reasoning row + product state) it produces byte-identical output, so a Today
screen can never "rephrase itself" between refreshes. Wording churn is structurally
impossible: there is no sampling, no synonym pool, no model in this path.

Contract rules implemented here:
- five user-facing states (A-E), E precedence (§11);
- information order: normal = conclusion→cause→action; medical safety = conclusion→action→cause (§12);
- no scores of any kind (§13);
- at most one primary cause, one secondary only when L6 judged them indistinguishable (§14);
- at most 3 actions; stable state renders 0 actions; no filler advice (§15);
- Evidence Level 2 is plain language (§16).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

STATE_LABELS = {
    "A": "整体稳定",
    "B": "有变化，但无需特别处理",
    "C": "今天值得调整",
    "D": "目前无法可靠判断",
    "E": "健康安全关注",
}

STATE_HEADLINES = {
    "A": "今天整体稳定，主要指标都在你自己的正常范围内。",
    "B": "今天有一些变化，但还不需要特别处理。",
    "C": "今天的身体状态值得调整一下安排。",
    "D": "目前证据不足，无法可靠判断今天的状态。",
    "E": "出现需要关注的健康安全信号。",
}

CONFIDENCE_LABELS = {"VERY_LOW": "很低", "LOW": "较低", "MODERATE": "中等", "HIGH": "较高"}

HYPOTHESIS_FALLBACK_CAUSE = {
    "RECOVERY_STRAIN": "最可能是近期高强度活动后的恢复压力。",
    "SLEEP_DEFICIT": "最可能是近期睡眠不足或睡眠节律变化。",
    "STRESS_RESPONSE": "可能与近期的心理或工作压力有关。",
    "ACUTE_ILLNESS_SUSPECTED": "指标变化也可能见于短期疾病或其他生理应激，需要结合症状观察。",
    "NO_SIGNIFICANT_FINDING": "没有发现明显的异常变化。",
    "UNKNOWN": "目前还不能可靠判断主要原因。",
}

METRIC_LABELS = {
    "heart_rate": "心率",
    "resting_heart_rate": "静息心率",
    "spo2": "血氧",
    "sleep": "睡眠",
    "steps": "步数",
    "calories": "卡路里消耗",
    "xiaomi_stress_score": "压力",
}


def metric_label(metric: str | None) -> str:
    return METRIC_LABELS.get(metric or "", metric or "该指标")


def _metric_from_feature(feature_name: str) -> str:
    base = feature_name.split(".")[0]
    return "sleep" if base == "sleep_source_episode" else base


def map_product_state(dr: dict, symptom_context_active: bool) -> str:
    """Deterministic mapping from sealed L6 output to the five product states (§11).

    Precedence: E (safety) > D (insufficient) > C/B/A. This never upgrades a medical
    concern into a diagnosis — state E is an attention state, and its payload keeps the
    conclusion→action→cause order.
    """
    medical_state = dr.get("medical_review_state")
    if (
        dr.get("primary_hypothesis_type") == "ACUTE_ILLNESS_SUSPECTED"
        or medical_state in ("REQUIRED", "PERFORMED", "UNAVAILABLE")
        or symptom_context_active
    ):
        return "E"
    overall = dr.get("overall_state")
    if overall == "INSUFFICIENT_EVIDENCE":
        return "D"
    if dr.get("primary_hypothesis_type") == "UNKNOWN" and dr.get("confidence") == "VERY_LOW":
        return "D"
    return {"STABLE": "A", "MILD_CHANGE": "B", "NOTABLE_CHANGE": "C"}[overall]


def parse_actions(dr: dict, product_state: str) -> list[str]:
    if product_state == "A":
        return []  # stable day: 0 actions allowed and preferred (§15)
    try:
        raw = json.loads(dr.get("recommended_actions_json") or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return [a.strip() for a in raw if isinstance(a, str) and a.strip()][:3]


def judgment_signature(dr: dict, product_state: str) -> str:
    """UI Change Threshold key: everything the user actually sees as 'the judgment'."""
    payload = {
        "product_state": product_state,
        "primary": dr.get("primary_hypothesis_type"),
        "secondary": dr.get("secondary_hypothesis_type"),
        "confidence": dr.get("confidence"),
        "actions": parse_actions(dr, product_state),
        "medical_review_state": dr.get("medical_review_state"),
        "cause_text": dr.get("reasoning_summary"),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evidence_level2(bundle: dict, max_items: int = 6) -> list[str]:
    """Plain-language evidence sentences (Evidence Level 2). No statistical jargon."""
    items: list[str] = []
    seen: set[tuple] = set()
    for d in bundle.get("deviations", []):
        cls = d.get("deviation_class")
        if cls not in ("ABOVE_TYPICAL_RANGE", "BELOW_TYPICAL_RANGE"):
            continue
        label = metric_label(d.get("metric"))
        key = (label, cls)
        if key in seen:
            continue
        seen.add(key)
        direction = "高" if cls == "ABOVE_TYPICAL_RANGE" else "低"
        items.append(f"{label}比你最近自己的通常水平{direction}一些")
    for p in bundle.get("persistence", []):
        cls = p.get("persistence_class")
        if cls not in ("PERSISTENT_ABOVE_TYPICAL", "PERSISTENT_BELOW_TYPICAL"):
            continue
        days = max(p.get("consecutive_above_typical") or 0, p.get("consecutive_below_typical") or 0)
        if days >= 2:
            label = metric_label(_metric_from_feature(p.get("feature_name", "")))
            key = ("persistence", label)
            if key not in seen:
                seen.add(key)
                items.append(f"{label}的这种变化已经持续 {days} 天")
    ctx = bundle.get("recent_context") or []
    if ctx:
        items.append("你最近补充的个人情况已纳入分析")
    return items[:max_items]


def render_today_payload(
    *,
    dr: dict,
    bundle: dict,
    product_state: str,
    analysis_date: str,
    generated_at_utc: str,
    timezone_name: str,
    judgment_updated: bool,
    change_note: str | None,
) -> dict:
    """Pure function: identical inputs -> byte-identical payload (except version_id, which
    the service layer attaches)."""
    actions = parse_actions(dr, product_state)
    cause_text = (dr.get("reasoning_summary") or "").strip() or HYPOTHESIS_FALLBACK_CAUSE.get(
        dr.get("primary_hypothesis_type") or "UNKNOWN", HYPOTHESIS_FALLBACK_CAUSE["UNKNOWN"]
    )
    secondary = dr.get("secondary_hypothesis_type")
    confidence = dr.get("confidence", "VERY_LOW")
    information_order = (
        ["conclusion", "action", "cause"] if product_state == "E" else ["conclusion", "cause", "action"]
    )
    local_dt = datetime.fromisoformat(generated_at_utc).astimezone(ZoneInfo(timezone_name))

    feedback_prompt = None
    if product_state in ("C", "E") or judgment_updated or confidence == "VERY_LOW":
        feedback_prompt = {
            "prompt": "这个判断准确吗？",
            "options": ["准确", "不太准确", "补充情况"],
        }

    return {
        "schema": "l7.today/v1",
        "product_state": product_state,
        "product_state_label": STATE_LABELS[product_state],
        "headline": STATE_HEADLINES[product_state],
        "information_order": information_order,
        "cause": {
            "hypothesis_type": dr.get("primary_hypothesis_type"),
            "text": cause_text,
            "secondary": {"hypothesis_type": secondary} if secondary else None,
        },
        "actions": actions,
        "confidence": confidence,
        "confidence_label": CONFIDENCE_LABELS[confidence],
        "medical_attention": product_state == "E",
        "analysis_date": analysis_date,
        "data_as_of": bundle.get("data_date") or analysis_date,
        "updated_at_utc": generated_at_utc,
        "updated_at_local_hhmm": local_dt.strftime("%H:%M"),
        "judgment_updated": judgment_updated,
        "change_note": change_note,
        "evidence_level2": evidence_level2(bundle),
        "feedback_prompt": feedback_prompt,
        "version_id": None,
    }
