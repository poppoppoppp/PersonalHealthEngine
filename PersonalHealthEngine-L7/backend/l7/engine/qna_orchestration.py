"""Deterministic contracts and policies for PHE Q&A Orchestration V2.

Models may classify and phrase answers, but this module owns schema validation and routing.
It deliberately contains no database or transport code so every policy is unit-testable.
"""

from __future__ import annotations

from typing import Any


SEMANTIC_SCOPES = {
    "HEALTH_DECISION", "HEALTH_DATA", "HEALTH_CONTEXT", "PRODUCT_META", "OUT_OF_SCOPE",
}
MEDICAL_CONSEQUENCES = {"NONE", "LOW", "MODERATE", "HIGH"}
TIME_RANGES = {None, "CURRENT", "TODAY", "LAST_NIGHT", "LAST_7_DAYS", "LAST_30_DAYS", "RECENT"}
AGGREGATIONS = {None, "LATEST", "AVERAGE", "TREND"}
CONTEXT_WRITES = {"NONE", "AUTO_SAVE", "CONFIRM"}
CONFIDENCE_LEVELS = {"VERY_LOW", "LOW", "MODERATE", "HIGH"}

METRIC_PREFIXES = {
    "SLEEP_DURATION": {"sleep", "sleep_source_episode"},
    "STEPS": {"steps"},
    "RESTING_HEART_RATE": {"resting_heart_rate"},
    "HEART_RATE": {"heart_rate"},
    "SPO2": {"spo2"},
    "STRESS": {"xiaomi_stress_score"},
    "CALORIES": {"calories"},
}

DOMAIN_PREFIXES = {
    "SLEEP": {"sleep", "sleep_source_episode"},
    "ACTIVITY": {"steps", "calories"},
    "CARDIOVASCULAR": {"heart_rate", "resting_heart_rate", "spo2"},
    "RECOVERY": {"sleep", "sleep_source_episode", "resting_heart_rate", "xiaomi_stress_score"},
    "STRESS": {"xiaomi_stress_score", "heart_rate", "resting_heart_rate"},
}

DOMAIN_CONTEXTS = {
    "SLEEP": {"LATE_SLEEP", "CAFFEINE", "ALCOHOL_USE", "FATIGUE"},
    "ACTIVITY": {"HIGH_INTENSITY_TRAINING", "FATIGUE", "FEELING_GOOD"},
    "CARDIOVASCULAR": {
        "HIGH_INTENSITY_TRAINING", "STRESS", "MEDICATION", "ILLNESS", "FEVER", "HEADACHE",
    },
    "RECOVERY": {
        "HIGH_INTENSITY_TRAINING", "LATE_SLEEP", "STRESS", "FATIGUE", "FEELING_GOOD",
        "ILLNESS", "FEVER", "SORE_THROAT", "NASAL_CONGESTION", "HEADACHE", "MEDICATION",
    },
    "STRESS": {"STRESS", "LATE_SLEEP", "CAFFEINE", "FATIGUE"},
}

PRODUCT_META_TEXT = (
    "我是你的个人健康决策助手。我的回答基于 PHE 当前掌握的个人健康数据、个人基线、"
    "近期变化和你主动补充的情况。我不是通用聊天机器人，也不用于替代医疗诊断。"
)

REFUSAL_TEXT = (
    "这个问题超出了我的范围。我只回答与你身体状态和健康决策相关的问题，"
    "比如今天是否适合活动、睡眠情况或指标变化。"
)

SEMANTIC_UNAVAILABLE_TEXT = "暂时无法理解这个问题，请稍后再试或换一种说法。"


class QnAContractError(ValueError):
    """Raised when model output does not match a deterministic Q&A contract."""


def _string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise QnAContractError(f"{key} must be an array of non-empty strings")
    return value


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in value)


def validate_semantic_classification(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise QnAContractError("semantic classification must be an object")

    scope = payload.get("scope")
    consequence = payload.get("medical_consequence")
    if scope not in SEMANTIC_SCOPES:
        raise QnAContractError("invalid semantic scope")
    if consequence not in MEDICAL_CONSEQUENCES:
        raise QnAContractError("invalid medical consequence")
    if payload.get("time_range") not in TIME_RANGES:
        raise QnAContractError("invalid time range")
    if payload.get("aggregation") not in AGGREGATIONS:
        raise QnAContractError("invalid aggregation")
    if payload.get("context_write") not in CONTEXT_WRITES:
        raise QnAContractError("invalid context write policy")

    for key in ("intent", "reason"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise QnAContractError(f"{key} must be a non-empty string")
    for key in (
        "requires_personal_evidence", "needs_medical_review", "potential_context",
    ):
        if not isinstance(payload.get(key), bool):
            raise QnAContractError(f"{key} must be boolean")

    decision_type = payload.get("decision_type")
    if decision_type is not None and (not isinstance(decision_type, str) or not decision_type):
        raise QnAContractError("decision_type must be a string or null")

    result = dict(payload)
    result["relevant_domains"] = _string_list(payload, "relevant_domains")
    result["relevant_metrics"] = _string_list(payload, "relevant_metrics")
    result["intent"] = payload["intent"].strip()
    result["reason"] = payload["reason"].strip()
    return result


def _feature_prefix(item: dict[str, Any]) -> str:
    feature = item.get("feature_name") or item.get("metric") or ""
    prefix = feature.split(".", 1)[0]
    return "sleep" if prefix == "sleep_source_episode" else prefix


def select_question_evidence(
    bundle: dict[str, Any],
    classification: dict[str, Any],
    patterns: list[dict[str, Any]] | None = None,
    today_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select only evidence deterministically relevant to the semantic routing result."""
    prefixes: set[str] = set()
    for metric in classification["relevant_metrics"]:
        prefixes.update(METRIC_PREFIXES.get(metric, set()))
    if not prefixes:
        for domain in classification["relevant_domains"]:
            prefixes.update(DOMAIN_PREFIXES.get(domain, set()))
    normalized_prefixes = {"sleep" if p == "sleep_source_episode" else p for p in prefixes}

    context_types: set[str] = set()
    for domain in classification["relevant_domains"]:
        context_types.update(DOMAIN_CONTEXTS.get(domain, set()))

    def relevant_item(item: dict[str, Any]) -> bool:
        if not normalized_prefixes:
            return True
        return _feature_prefix(item) in normalized_prefixes

    selected: dict[str, Any] = {
        "schema": "phe.qna.evidence/v2",
        "analysis_date": bundle.get("analysis_date"),
        "data_date": bundle.get("data_date"),
        "overall_state": bundle.get("overall_state"),
        "intent": classification["intent"],
        "decision_type": classification.get("decision_type"),
        "relevant_domains": classification["relevant_domains"],
        "relevant_metrics": classification["relevant_metrics"],
        "today": today_snapshot,
        "deviations": [item for item in bundle.get("deviations", []) if relevant_item(item)],
        "recent_deviations": [item for item in bundle.get("recent_deviations", []) if relevant_item(item)],
        "persistence": [item for item in bundle.get("persistence", []) if relevant_item(item)],
        "trends": [item for item in bundle.get("trends", []) if relevant_item(item)],
        "change": [item for item in bundle.get("change", []) if relevant_item(item)],
        "relationships": [
            item for item in bundle.get("relationships", [])
            if not normalized_prefixes or any(
                ("sleep" if str(feature).split(".", 1)[0] == "sleep_source_episode"
                 else str(feature).split(".", 1)[0]) in normalized_prefixes
                for feature in item.get("pair", [])
            )
        ],
        "baseline_maturity_summary": bundle.get("baseline_maturity_summary", {}),
        "recent_context": [
            item for item in bundle.get("recent_context", [])
            if not context_types or item.get("context_type") in context_types
        ],
        "recent_feedback": list(bundle.get("recent_feedback", []))[:5],
        "missing_evidence": list(bundle.get("missing_evidence", [])),
    }

    selected_patterns = []
    for pattern in patterns or []:
        outcome = str(pattern.get("outcome_signal", ""))
        outcome_prefix = outcome.removesuffix("_UP").removesuffix("_DOWN")
        if pattern.get("maturity") != "ESTABLISHED" and pattern.get("support_count", 0) < 2:
            continue
        if normalized_prefixes and outcome_prefix not in normalized_prefixes:
            continue
        selected_patterns.append({
            key: pattern.get(key) for key in (
                "trigger_context_type", "outcome_signal", "support_count", "total_count",
                "maturity", "first_seen_date", "last_seen_date",
            )
        })
    selected["personal_patterns"] = selected_patterns

    catalog: dict[str, str] = {
        "today.overall_state": f"current deterministic overall state: {selected['overall_state']}",
    }
    for field in (
        "deviations", "recent_deviations", "persistence", "trends", "change",
        "relationships", "recent_context", "personal_patterns", "missing_evidence",
    ):
        for index, item in enumerate(selected[field]):
            catalog[f"{field}.{index}"] = str(item)
    selected["evidence_catalog"] = catalog
    return selected


def validate_candidate(
    payload: Any,
    evidence_bundle: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Validate candidate structure and return deterministic safety issues separately."""
    if not isinstance(payload, dict):
        raise QnAContractError("candidate must be an object")
    candidate = dict(payload)
    for key in ("direct_answer", "reason"):
        if not isinstance(candidate.get(key), str) or not candidate[key].strip():
            raise QnAContractError(f"candidate {key} must be a non-empty string")
        candidate[key] = candidate[key].strip()
        if not _contains_cjk(candidate[key]):
            raise QnAContractError(f"candidate {key} must be Simplified-Chinese product text")
    for key in ("recommended_actions", "evidence_refs", "medical_claims", "uncertainties"):
        candidate[key] = _string_list(candidate, key)
    if len(candidate["recommended_actions"]) > 3:
        raise QnAContractError("candidate has more than three actions")
    if any(not _contains_cjk(action) for action in candidate["recommended_actions"]):
        raise QnAContractError("candidate actions must be Simplified-Chinese product text")
    if candidate.get("confidence") not in CONFIDENCE_LEVELS:
        raise QnAContractError("candidate has invalid confidence")

    issues: list[str] = []
    catalog = evidence_bundle.get("evidence_catalog", {})
    if not candidate["evidence_refs"]:
        issues.append("missing_evidence_refs")
    if any(ref not in catalog for ref in candidate["evidence_refs"]):
        issues.append("unsupported_evidence_ref")

    combined = " ".join([
        candidate["direct_answer"], candidate["reason"],
        *candidate["recommended_actions"], *candidate["medical_claims"],
    ])
    contexts = {item.get("context_type") for item in evidence_bundle.get("recent_context", [])}
    if any(phrase in combined for phrase in ("肯定得了", "你得了", "确诊为", "就是心脏病")):
        issues.append("diagnosis_claim")
    if any(phrase in combined for phrase in ("你发烧了", "你正在发烧")) and "FEVER" not in contexts:
        issues.append("invented_fever")
    if any(phrase in combined for phrase in ("你喝了酒", "你昨晚喝酒")) and "ALCOHOL_USE" not in contexts:
        issues.append("invented_alcohol")
    if any(phrase in combined for phrase in ("你吃了药", "你正在服药")) and "MEDICATION" not in contexts:
        issues.append("invented_medication")
    return candidate, sorted(set(issues))


def medical_consequence_gate(
    classification: dict[str, Any],
    sealed_review_state: str,
    sealed_reasons: list[str],
    today_medical_state: str | None,
    candidate: dict[str, Any],
    candidate_issues: list[str],
) -> tuple[bool, list[str]]:
    """Combine model hints with deterministic consequence and sealed safety policy."""
    reasons = set(sealed_reasons)
    if sealed_review_state == "REQUIRED":
        reasons.add("sealed_medical_trigger")
    if classification["medical_consequence"] in {"MODERATE", "HIGH"}:
        reasons.add("moderate_or_high_consequence")
    if classification.get("needs_medical_review"):
        reasons.add("semantic_review_hint")
    if classification.get("decision_type") == "PHYSICAL_ACTIVITY":
        reasons.add("physical_activity_decision")
    if today_medical_state in {"REQUIRED", "PERFORMED", "UNAVAILABLE"}:
        reasons.add("current_today_medical_state")
    if candidate.get("medical_claims"):
        reasons.add("candidate_medical_claims")
    if candidate_issues:
        reasons.add("candidate_safety_validation")
    return bool(reasons), sorted(reasons)


def validate_medical_review(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise QnAContractError("medical review must be an object")
    status = payload.get("review_status")
    if status not in {"APPROVED", "APPROVED_WITH_CHANGES", "REJECTED", "ESCALATE", "UNAVAILABLE"}:
        raise QnAContractError("invalid medical review status")
    result = dict(payload)
    for key in (
        "medical_concerns", "causality_concerns", "missing_safety_considerations",
        "unsafe_actions", "required_changes",
    ):
        result[key] = _string_list(payload, key)
    if not isinstance(result.get("review_summary"), str):
        raise QnAContractError("medical review summary must be a string")
    escalation_reason = result.get("escalation_reason")
    if escalation_reason is not None and not isinstance(escalation_reason, str):
        raise QnAContractError("escalation reason must be a string or null")
    if status == "APPROVED_WITH_CHANGES" and not result["required_changes"]:
        raise QnAContractError("approved-with-changes requires changes")
    return result
