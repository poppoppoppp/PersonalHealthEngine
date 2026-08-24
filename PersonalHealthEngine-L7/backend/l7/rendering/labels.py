"""Canonical Simplified-Chinese labels for product presentation.

Raw L6/L5 vocabulary remains in API machine fields. Product surfaces use the
explicit labels from this module and never fall back to displaying an enum.
"""

from __future__ import annotations

import re

STATE_LABELS = {
    "A": "整体稳定",
    "B": "有变化，但无需特别处理",
    "C": "今天值得调整",
    "D": "目前无法可靠判断",
    "E": "健康安全关注",
}

CONFIDENCE_LABELS = {
    "VERY_LOW": "很低",
    "LOW": "较低",
    "MODERATE": "中等",
    "HIGH": "较高",
}

HYPOTHESIS_LABELS = {
    "RECOVERY_STRAIN": "恢复压力",
    "SLEEP_DEFICIT": "睡眠不足",
    "STRESS_RESPONSE": "压力反应",
    "ACUTE_ILLNESS_SUSPECTED": "需要排查短期疾病或生理应激",
    "NO_SIGNIFICANT_FINDING": "未发现明显变化",
    "UNKNOWN": "暂无法确定原因",
}

CONTEXT_LABELS = {
    "HIGH_INTENSITY_TRAINING": "高强度训练",
    "ALCOHOL_USE": "饮酒",
    "LATE_SLEEP": "晚睡",
    "CAFFEINE": "咖啡因摄入",
    "STRESS": "近期压力",
    "TRAVEL": "旅行或出差",
    "ILLNESS": "身体不适",
    "SORE_THROAT": "咽喉不适",
    "FEVER": "发热",
    "NASAL_CONGESTION": "鼻塞",
    "MEDICATION": "用药",
    "FATIGUE": "疲劳",
    "FEELING_GOOD": "感觉良好",
    "DIET_CHANGE": "饮食变化",
    "SCHEDULE_CHANGE": "作息变化",
}

BODY_PART_LABELS = {
    "HEAD": "头部",
    "THROAT": "咽喉",
    "CHEST": "胸部",
    "ABDOMEN": "腹部",
    "BACK": "背部",
    "ARM": "手臂",
    "LEG": "腿部",
    "FULL_BODY": "全身",
}

FEEDBACK_STATUS_LABELS = {
    "ACCURATE": "准确",
    "INACCURATE": "不太准确",
    "CORRECTED": "已补充情况",
}

STATUS_LABELS = {
    "CURRENT": "当前有效",
    "SUPERSEDED": "已被更新",
    "STALE": "历史版本",
}

BASELINE_MATURITY_LABELS = {
    "IMMATURE": "个人基线仍在建立",
    "PROVISIONAL": "个人基线初步可用",
    "MATURE": "个人基线较稳定",
}

EVIDENCE_STATUS_LABELS = {
    "INSUFFICIENT": "证据不足",
    "PROVISIONAL": "初步证据",
    "SUFFICIENT": "证据较充分",
}

PATTERN_STATUS_LABELS = {
    "OBSERVING": "正在观察",
    "ESTABLISHED": "较稳定规律",
    "WEAKENED": "规律减弱",
    "INVALIDATED": "暂不成立",
}

TRIGGER_LABELS = {
    "app_open": "打开应用",
    "manual_refresh": "手动刷新",
    "scheduled": "定时更新",
    "context_added": "补充情况后复核",
    "context_corrected": "纠正情况后复核",
    "context_deleted": "删除情况后复核",
    "feedback": "提交反馈后复核",
    "feedback_submitted": "提交反馈后复核",
    "repair-presentation": "展示修复",
}

METRIC_LABELS = {
    "heart_rate": "心率",
    "resting_heart_rate": "静息心率",
    "spo2": "血氧",
    "sleep": "睡眠",
    "steps": "步数",
    "calories": "活动消耗",
    "xiaomi_stress_score": "压力指标",
}

FEATURE_LABELS = {
    "heart_rate.daily.count": "心率记录数量",
    "heart_rate.daily.max": "最高心率",
    "heart_rate.daily.mean": "平均心率",
    "heart_rate.daily.median": "心率中位数",
    "heart_rate.daily.min": "最低心率",
    "resting_heart_rate.daily.value": "静息心率",
    "spo2.daily.count": "血氧记录数量",
    "spo2.daily.max": "最高血氧",
    "spo2.daily.mean": "平均血氧",
    "spo2.daily.median": "血氧中位数",
    "spo2.daily.min": "最低血氧",
    "steps.daily.bucket_count": "活动记录覆盖时段",
    "steps.daily.sum": "记录到的累计步数",
    "calories.daily.bucket_count": "活动消耗记录覆盖时段",
    "calories.daily.sum": "记录到的活动消耗",
    "xiaomi_stress_score.daily.count": "压力记录数量",
    "xiaomi_stress_score.daily.max": "最高压力指标",
    "xiaomi_stress_score.daily.mean": "平均压力指标",
    "xiaomi_stress_score.daily.median": "压力指标中位数",
    "xiaomi_stress_score.daily.min": "最低压力指标",
    "sleep_source_episode.duration_seconds": "本次睡眠时长",
    "sleep_source_episode.vendor_awake_duration_seconds": "睡眠中清醒时长",
    "sleep_source_episode.vendor_sleep_like_duration_seconds": "本次有效睡眠时长",
    "sleep_source_episode.vendor_stage_segment_count": "睡眠阶段记录段数",
}

_SLEEP_STAGE_LABELS = {
    "awake": "清醒",
    "deep": "深睡",
    "light": "浅睡",
    "rem": "REM 睡眠",
    "sleep": "睡眠",
}


def hypothesis_label(value: str | None) -> str:
    return HYPOTHESIS_LABELS.get(value or "", HYPOTHESIS_LABELS["UNKNOWN"])


def context_label(value: str | None) -> str:
    return CONTEXT_LABELS.get(value or "", "其他个人情况")


def body_part_label(value: str | None) -> str | None:
    if not value:
        return None
    return BODY_PART_LABELS.get(value, "其他部位")


def confidence_label(value: str | None) -> str:
    return CONFIDENCE_LABELS.get(value or "", CONFIDENCE_LABELS["VERY_LOW"])


def status_label(value: str | None) -> str:
    return STATUS_LABELS.get(value or "", "状态未知")


def feedback_status_label(value: str | None) -> str:
    return FEEDBACK_STATUS_LABELS.get(value or "", "已记录反馈")


def baseline_maturity_label(value: str | None) -> str:
    return BASELINE_MATURITY_LABELS.get(value or "", "个人基线状态未知")


def evidence_status_label(value: str | None) -> str:
    return EVIDENCE_STATUS_LABELS.get(value or "", "证据状态未知")


def pattern_status_label(value: str | None) -> str:
    return PATTERN_STATUS_LABELS.get(value or "", "正在观察")


def trigger_label(value: str | None) -> str:
    return TRIGGER_LABELS.get(value or "", "系统更新")


def metric_label(value: str | None) -> str:
    return METRIC_LABELS.get(value or "", "该指标")


def feature_label(value: str | None) -> str:
    name = value or ""
    if name in FEATURE_LABELS:
        return FEATURE_LABELS[name]
    match = re.fullmatch(
        r"sleep_source_episode\.vendor_stage\.(awake|deep|light|rem|sleep)\."
        r"(duration_seconds|proportion)",
        name,
    )
    if match:
        stage = _SLEEP_STAGE_LABELS[match.group(1)]
        suffix = "时长" if match.group(2) == "duration_seconds" else "占比"
        return f"{stage}{suffix}"
    return metric_label(name.split(".", 1)[0].replace("sleep_source_episode", "sleep"))


def deviation_direction_label(value: str | None) -> str:
    return {
        "ABOVE_TYPICAL_RANGE": "高于个人近期基线",
        "BELOW_TYPICAL_RANGE": "低于个人近期基线",
        "WITHIN_TYPICAL_RANGE": "在个人近期范围内",
    }.get(value or "", "与个人近期基线有变化")


def format_health_value(feature_name: str, value: float | int | None, unit: str | None) -> str:
    if value is None:
        return "暂无数值"
    number = float(value)
    if unit == "seconds":
        minutes = max(round(number / 60), 0)
        hours, remainder = divmod(minutes, 60)
        return f"{hours} 小时 {remainder} 分钟" if hours else f"{remainder} 分钟"
    if unit == "ratio":
        return f"{number * 100:.1f}%"
    if unit == "percent":
        return f"{number:.1f}%"
    if unit == "bpm":
        return f"{number:.1f} 次/分"
    if unit == "steps":
        return f"{round(number):,} 步"
    if unit == "count":
        return f"{round(number):,} 个"
    if unit == "vendor_score":
        return f"{number:.1f} 分"
    if unit == "vendor_calories":
        return f"{number:.1f}（设备活动消耗值）"
    return f"{number:.2f}"


def outcome_label(value: str | None) -> str:
    raw = value or ""
    if raw.endswith("_UP"):
        return f"{metric_label(raw[:-3])}偏高"
    if raw.endswith("_DOWN"):
        return f"{metric_label(raw[:-5])}偏低"
    return "相关指标发生变化"
