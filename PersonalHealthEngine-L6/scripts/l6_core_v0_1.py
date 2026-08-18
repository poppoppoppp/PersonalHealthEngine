"""Layer 6 AI Reasoning — deterministic core.

Contains the deterministic logic that the LLM must never take over: context extraction
rules (used by the mock adapter), overall-state derivation, hypothesis candidate
generation, base confidence, medical-review trigger policy, and structured-output
validation. All model-facing code consumes these, never the database directly.
"""

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

CONTEXT_DEFINITION_ID = "l6.context.extraction"
EVIDENCE_DEFINITION_ID = "l6.evidence.assembly"
HYPOTHESIS_DEFINITION_ID = "l6.hypothesis"
CONFIDENCE_DEFINITION_ID = "l6.confidence"
DAILY_DEFINITION_ID = "l6.daily.reasoning"
MEDICAL_DEFINITION_ID = "l6.medical.review"
PATTERN_DEFINITION_ID = "l6.personal.pattern"

CONFIDENCE_LEVELS = ("VERY_LOW", "LOW", "MODERATE", "HIGH")
OVERALL_STATES = ("STABLE", "MILD_CHANGE", "NOTABLE_CHANGE", "INSUFFICIENT_EVIDENCE")
HYPOTHESIS_TYPES = (
    "RECOVERY_STRAIN", "SLEEP_DEFICIT", "STRESS_RESPONSE",
    "ACUTE_ILLNESS_SUSPECTED", "NO_SIGNIFICANT_FINDING", "UNKNOWN",
)

CONTEXT_KEYWORDS = {
    "HIGH_INTENSITY_TRAINING": ["训练", "练腿", "练背", "练胸", "练手臂", "练肩", "高强度", "workout", "training", "leg day", "gym", "深蹲", "硬拉"],
    "ALCOHOL_USE": ["喝酒", "饮酒", "酒", "alcohol", "drink", "beer", "wine"],
    "LATE_SLEEP": ["熬夜", "晚睡", "两点", "三点", "凌晨", "late sleep", "stayed up", "睡太晚"],
    "CAFFEINE": ["咖啡", "咖啡因", "caffeine", "coffee"],
    "STRESS": ["压力", "考试", "加班", "工作压力", "stress", "exam", "deadline"],
    "TRAVEL": ["旅行", "出差", "travel", "trip", "flight", "航班"],
    "FEVER": ["发烧", "发热", "fever"],
    "SORE_THROAT": ["咽痛", "喉咙痛", "嗓子痛", "sore throat"],
    "NASAL_CONGESTION": ["鼻塞", "鼻塞", "congestion", "stuffy"],
    "MEDICATION": ["吃药", "用药", "服药", "medication", "medicine", "drug"],
    "FATIGUE": ["疲劳", "乏力", "很累", "tired", "fatigue", "exhausted"],
    "FEELING_GOOD": ["状态好", "感觉好", "精神好", "feeling good", "energetic"],
    "DIET_CHANGE": ["饮食", "吃多了", "吃少了", "diet"],
    "SCHEDULE_CHANGE": ["作息", "schedule", "routine"],
    "ILLNESS": ["生病", "感冒", "不舒服", "sick", "ill", "cold", "flu"],
}

BODY_PARTS = {
    "腿": "legs", "leg": "legs", "legs": "legs",
    "背": "back", "back": "back",
    "胸": "chest", "chest": "chest",
    "手臂": "arms", "arm": "arms", "arms": "arms",
    "肩": "shoulders", "shoulder": "shoulders", "shoulders": "shoulders",
    "全身": "full_body", "full body": "full_body",
}

PAST_KEYWORDS = ("昨天", "昨晚", "yesterday", "last night", "前天")
TODAY_KEYWORDS = ("今天", "today", "刚刚", "now")

SYMPTOM_TYPES = ("ILLNESS", "FEVER", "SORE_THROAT", "NASAL_CONGESTION", "MEDICATION")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_date(value):
    return date.fromisoformat(value)


def add_days(value, days):
    return value + timedelta(days=days)


def load_definition(path, expected_id):
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    if payload.get("definition_id") != expected_id:
        raise ValueError(f"unexpected definition_id in {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def extract_context_events(text, today):
    """Deterministic keyword extractor. Returns a list of context-event dicts.

    This is the mock adapter's context understanding; a real reasoning model would replace
    the keyword matching with actual language understanding. Fields are only emitted when the
    user actually said them.
    """
    lowered = text.lower()
    relative_day = 0
    if any(k in lowered for k in PAST_KEYWORDS):
        relative_day = -1
    context_date = add_days(parse_date(today), relative_day).isoformat()

    events = []
    matched = []
    for context_type, keywords in CONTEXT_KEYWORDS.items():
        if any(k.lower() in lowered for k in keywords):
            matched.append(context_type)

    # Generic ILLNESS is suppressed when a more specific symptom is present.
    if "ILLNESS" in matched and any(t in matched for t in ("FEVER", "SORE_THROAT", "NASAL_CONGESTION")):
        matched = [m for m in matched if m != "ILLNESS"]

    body_part = None
    for key, part in BODY_PARTS.items():
        if key.lower() in lowered:
            body_part = part
            break

    for context_type in matched:
        event = {"context_type": context_type, "context_date": context_date}
        if context_type == "HIGH_INTENSITY_TRAINING" and body_part:
            event["body_part"] = body_part
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Deterministic reasoning logic
# ---------------------------------------------------------------------------

def overall_state(bundle):
    """Derive overall_state from the assembled evidence bundle (current-day deviations)."""
    deviations = bundle.get("deviations", [])
    if not deviations or all(d.get("deviation_class") in (None, "INSUFFICIENT_BASELINE") for d in deviations):
        return "INSUFFICIENT_EVIDENCE"
    above = [d for d in deviations if d.get("deviation_class") == "ABOVE_TYPICAL_RANGE"]
    below = [d for d in deviations if d.get("deviation_class") == "BELOW_TYPICAL_RANGE"]
    persistence = [p for p in bundle.get("persistence", []) if p.get("persistence_class") in ("PERSISTENT_ABOVE_TYPICAL", "PERSISTENT_BELOW_TYPICAL")]
    change = [c for c in bundle.get("change", []) if c.get("change_class") == "CHANGE_DETECTED"]
    trends = [t for t in bundle.get("trends", []) if t.get("trend_class") in ("RISING", "FALLING")]
    if persistence or change:
        return "NOTABLE_CHANGE"
    if len(above) >= 2 or len(below) >= 2:
        return "NOTABLE_CHANGE"
    if above or below or trends:
        return "MILD_CHANGE"
    return "STABLE"


def _signal_set(bundle):
    signals = set()
    for d in bundle.get("deviations", []):
        metric = d.get("metric", "")
        cls = d.get("deviation_class")
        if cls == "ABOVE_TYPICAL_RANGE":
            signals.add(f"{metric}_UP")
        elif cls == "BELOW_TYPICAL_RANGE":
            signals.add(f"{metric}_DOWN")
    for p in bundle.get("persistence", []):
        if p.get("persistence_class") == "PERSISTENT_ABOVE_TYPICAL":
            signals.add("PERSISTENT_HIGH")
        elif p.get("persistence_class") == "PERSISTENT_BELOW_TYPICAL":
            signals.add("PERSISTENT_LOW")
    return signals


def _context_types(bundle):
    return {c.get("context_type") for c in bundle.get("recent_context", [])}


def generate_candidates(bundle):
    """Deterministic hypothesis candidates with supporting evidence."""
    signals = _signal_set(bundle)
    contexts = _context_types(bundle)
    candidates = []

    rhr_up = "resting_heart_rate_UP" in signals or "heart_rate_UP" in signals
    stress_up = "xiaomi_stress_score_UP" in signals
    sleep_down = "sleep_DOWN" in signals
    has_training = "HIGH_INTENSITY_TRAINING" in contexts
    has_late_sleep = "LATE_SLEEP" in contexts
    has_stress_ctx = "STRESS" in contexts
    symptom_contexts = contexts & set(SYMPTOM_TYPES)

    if rhr_up and has_training:
        support = sorted(set([s for s in signals if "UP" in s] + ["HIGH_INTENSITY_TRAINING"]))
        candidates.append({"hypothesis_type": "RECOVERY_STRAIN", "supporting": support})
    if sleep_down:
        support = ["sleep_like_duration_below_typical"] + (["LATE_SLEEP"] if has_late_sleep else [])
        candidates.append({"hypothesis_type": "SLEEP_DEFICIT", "supporting": sorted(set(support))})
    elif has_late_sleep and (stress_up or rhr_up or "FATIGUE" in contexts):
        support = ["LATE_SLEEP"] + ([s for s in signals if "UP" in s] or ["FATIGUE" if "FATIGUE" in contexts else "xiaomi_stress_score_UP"])
        candidates.append({"hypothesis_type": "SLEEP_DEFICIT", "supporting": sorted(set(support))})
    if stress_up and (has_stress_ctx or rhr_up):
        support = ["xiaomi_stress_score_UP"] + (["STRESS"] if has_stress_ctx else []) + (["heart_rate_UP"] if rhr_up else [])
        candidates.append({"hypothesis_type": "STRESS_RESPONSE", "supporting": sorted(set(support))})
    if symptom_contexts:
        support = sorted(symptom_contexts) + (["heart_rate_UP"] if rhr_up else [])
        candidates.append({"hypothesis_type": "ACUTE_ILLNESS_SUSPECTED", "supporting": support})

    if not candidates:
        if bundle.get("overall_state") == "STABLE":
            candidates.append({"hypothesis_type": "NO_SIGNIFICANT_FINDING", "supporting": ["overall_state_STABLE"]})
        else:
            candidates.append({"hypothesis_type": "UNKNOWN", "supporting": ["insufficient_distinguishing_evidence"]})
    return candidates


def base_confidence(support_count, counter_count, has_insufficient_baseline, has_context):
    """Deterministic base confidence. The model may only explain or downgrade."""
    if counter_count > support_count:
        return "VERY_LOW"
    if counter_count > 0:
        return "LOW"
    if has_insufficient_baseline and not has_context and support_count < 2:
        return "VERY_LOW"
    if support_count >= 3:
        return "HIGH"
    if support_count >= 2:
        return "MODERATE"
    if support_count >= 1 or has_context:
        return "LOW"
    return "VERY_LOW"


def medical_trigger(question_text, bundle, hypothesis_types):
    """Deterministic medical-review trigger policy. Returns (review_state, reasons)."""
    contexts = _context_types(bundle)
    reasons = []
    if contexts & set(SYMPTOM_TYPES):
        reasons.append("user_reported_symptom")
    if "ACUTE_ILLNESS_SUSPECTED" in hypothesis_types:
        reasons.append("high_risk_hypothesis")
    if any(p.get("persistence_class") in ("PERSISTENT_ABOVE_TYPICAL", "PERSISTENT_BELOW_TYPICAL") for p in bundle.get("persistence", [])):
        reasons.append("persistent_notable_anomaly")
    if question_text:
        q = question_text.lower()
        if any(k in q for k in ("病", "发烧", "感冒", "药", "doctor", "sick", "ill", "disease", "医院", "就医")):
            reasons.append("disease_or_doctor_question")
    if reasons:
        return "REQUIRED", sorted(set(reasons))
    return "BYPASSED", []


DAILY_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["primary_hypothesis_type", "confidence", "recommended_actions", "reasoning_summary"],
    "properties": {
        "primary_hypothesis_type": {"type": "string"},
        "secondary_hypothesis_type": {"type": ["string", "null"]},
        "confidence": {"type": "string"},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "reasoning_summary": {"type": "string"},
    },
}


def validate_daily_output(payload, hypothesis_types, base_confidence_value):
    """Validate model daily-reasoning output. Returns (ok, errors)."""
    errors = []
    if not isinstance(payload, dict):
        return False, ["output is not an object"]
    for key in DAILY_OUTPUT_SCHEMA["required"]:
        if key not in payload:
            errors.append(f"missing {key}")
    primary = payload.get("primary_hypothesis_type")
    if primary not in hypothesis_types:
        errors.append(f"invalid primary_hypothesis_type {primary!r}")
    if not isinstance(payload.get("recommended_actions"), list):
        errors.append("recommended_actions is not a list")
    if payload.get("confidence") not in CONFIDENCE_LEVELS:
        errors.append(f"invalid confidence {payload.get('confidence')!r}")
    return (not errors), errors
