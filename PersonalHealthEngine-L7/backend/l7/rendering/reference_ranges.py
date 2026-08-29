"""科学参考区间（L7 展示层与安全底座共用）。

设计原则：
- "判断"仍然完全由个人基线驱动（L4/L5 封印层不变）——回答"你和以前的你比怎么样"。
- 本表只做两件事：
  1. display：在数据回看的图表里画出"一般成人常见范围"参考带（仅展示坐标，不构成诊断）；
  2. safety：极少数硬危险信号的绝对下限/上限。越线时无论个人基线如何，今日状态
     直接升级为「健康安全关注」，避免"个人基线对绝对危险值麻木"的盲区。
- 单位与各指标 L3 序列的自然单位一致（睡眠为秒，血氧为百分数，心率为 bpm）。
- 数值来源：通用成人常模（静息心率 60-100 bpm；SpO2 正常 95-100%、<90% 为低氧血症；
  成人睡眠建议 7-9 小时）。按年龄/性别细化时直接改这张表。
"""

SAFETY_FEATURES = {
    "heart_rate.daily.mean": "heart_rate",
    "resting_heart_rate.daily.value": "resting_heart_rate",
    "spo2.daily.mean": "spo2",
}

REFERENCE_RANGES = {
    "resting_heart_rate": {
        "display": (60, 100),
        "safety_low": 40,
        "safety_high": 120,
    },
    "heart_rate": {
        "display": (60, 100),
        "safety_high": 125,
    },
    "spo2": {
        "display": (95, 100),
        "safety_low": 90,
    },
    "sleep": {
        "display": (7 * 3600, 9 * 3600),  # 成人建议 7-9 小时（仅参考带）
    },
}


def reference_for(metric: str, age: int | None = None, sex: str | None = None) -> dict | None:
    """返回展示参考带（low/high，指标自然单位）；无临床标准的指标返回 None。

    age/sex 用于按人群细化：当前 22 岁男性落在通用成人档（数值相同），分支结构
    为将来年龄增长/女性档位预留——例如 65 岁以上睡眠建议收窄为 7-8 小时。"""
    entry = REFERENCE_RANGES.get(metric)
    if entry is None or "display" not in entry:
        return None
    lo, hi = entry["display"]

    if metric == "sleep" and age is not None and age >= 65:
        lo, hi = 7 * 3600, 8 * 3600

    return {"low": lo, "high": hi}


def safety_breach(metric: str, value: float) -> str | None:
    """返回越界方向（"low"/"high"），未越界返回 None。"""
    entry = REFERENCE_RANGES.get(metric)
    if entry is None:
        return None
    if "safety_low" in entry and value < entry["safety_low"]:
        return "low"
    if "safety_high" in entry and value > entry["safety_high"]:
        return "high"
    return None
