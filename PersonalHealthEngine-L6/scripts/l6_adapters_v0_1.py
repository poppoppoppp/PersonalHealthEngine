"""Layer 6 model adapters (model-independent).

ReasoningModelAdapter and MedicalModelAdapter are the seams behind which DeepSeek/MedGemma
(or any future model) can be swapped without changing L6 core. The mock adapters are fully
deterministic and are the only adapters exercised by acceptance/rebuild; the real adapters
are config-driven stubs that raise a clear error unless explicitly configured.
"""

from l6_core_v0_1 import extract_context_events


class ModelError(Exception):
    """A model call failed or is not configured."""


class ModelTimeoutError(ModelError):
    pass


class ModelInvalidOutputError(ModelError):
    pass


class ReasoningModelAdapter:
    model_id = "base-reasoning"

    def extract_context(self, text, today):
        raise NotImplementedError

    def reason_daily(self, bundle, candidates):
        raise NotImplementedError

    def answer_question(self, question, bundle, candidates):
        raise NotImplementedError


class MedicalModelAdapter:
    model_id = "base-medical"

    def review(self, bundle, hypothesis_types, question_text=None):
        raise NotImplementedError


def _action_templates(bundle, hypothesis_type):
    overall = bundle.get("overall_state")
    if hypothesis_type == "RECOVERY_STRAIN":
        return ["今天降低训练强度，避免再叠加高强度刺激", "优先保证睡眠", "继续观察 24-48 小时恢复情况"]
    if hypothesis_type == "SLEEP_DEFICIT":
        return ["今天优先补充睡眠", "暂缓高强度训练", "避免咖啡因在下午之后摄入"]
    if hypothesis_type == "STRESS_RESPONSE":
        return ["今天安排低强度恢复活动", "优先保证睡眠", "继续观察压力指标是否回落"]
    if hypothesis_type == "ACUTE_ILLNESS_SUSPECTED":
        return ["主动测量体温并观察症状", "如果症状持续或加重，考虑医学评估", "今天以休息为主"]
    if overall == "STABLE":
        return ["今天按原计划生活", "保持规律作息"]
    return ["继续观察 24-48 小时", "保持规律作息和充分睡眠"]


def _summary_template(bundle, hypothesis_type, secondary):
    if hypothesis_type == "NO_SIGNIFICANT_FINDING":
        return "今天整体状态比较稳定，主要指标都在最近自己的正常节奏里，按原计划生活就行。"
    if hypothesis_type == "RECOVERY_STRAIN":
        return "今天恢复状态可能比近期差一些，最可能是近期高强度训练后的恢复压力。"
    if hypothesis_type == "SLEEP_DEFICIT":
        return "最近可能因为睡眠不足，身体压力指标有些升高。"
    if hypothesis_type == "STRESS_RESPONSE":
        return "最近压力指标升高，可能与近期的心理或工作压力有关。"
    if hypothesis_type == "ACUTE_ILLNESS_SUSPECTED":
        return "近期指标变化也可能见于短期疾病或其他生理应激，建议观察症状并考虑体温等自测。"
    if secondary:
        return "目前更像" + hypothesis_type + "，但" + secondary + "还不能完全排除。"
    return "目前还不能可靠判断主要原因。"


class MockReasoningModelAdapter(ReasoningModelAdapter):
    model_id = "mock-reasoning-v0.1"

    def extract_context(self, text, today):
        return extract_context_events(text, today)

    def reason_daily(self, bundle, candidates):
        primary = candidates[0] if candidates else {"hypothesis_type": "UNKNOWN", "supporting": []}
        secondary = candidates[1] if len(candidates) > 1 else None
        return {
            "primary_hypothesis_type": primary["hypothesis_type"],
            "secondary_hypothesis_type": secondary["hypothesis_type"] if secondary else None,
            "confidence": "LOW",
            "recommended_actions": _action_templates(bundle, primary["hypothesis_type"]),
            "reasoning_summary": _summary_template(bundle, primary["hypothesis_type"], secondary["hypothesis_type"] if secondary else None),
        }

    def answer_question(self, question, bundle, candidates):
        primary = candidates[0] if candidates else {"hypothesis_type": "UNKNOWN"}
        overall = bundle.get("overall_state")
        q = question.lower()
        if any(k in q for k in ("练腿", "训练", "练", "train", "workout", "gym", "跑步", "run")):
            if overall == "STABLE":
                text = "今天状态稳定，主要指标都在你自己的正常节奏里，可以按计划训练。"
            elif overall == "NOTABLE_CHANGE":
                text = "今天身体变化比较明显，建议暂缓高强度训练，先观察恢复情况。"
            else:
                text = "今天有一些轻微变化，建议降低训练强度，别叠加太高强度的刺激。"
        elif any(k in q for k in ("心率", "heart rate")):
            text = "结合你的个人基线，最近静息心率相关指标存在相对自身历史的变化。"
        else:
            text = "结合你最近的数据和个人情况，" + _summary_template(bundle, primary["hypothesis_type"], None)
        return {
            "answer_text": text,
            "primary_hypothesis_type": primary["hypothesis_type"],
            "recommended_actions": _action_templates(bundle, primary["hypothesis_type"]),
        }


class DeepSeekReasoningModelAdapter(ReasoningModelAdapter):
    model_id = "deepseek-v4-flash"

    def __init__(self, api_key=None, base_url=None):
        self.api_key = api_key
        self.base_url = base_url

    def extract_context(self, text, today):
        raise ModelError("DeepSeek adapter is not configured; use the mock adapter for deterministic runs")

    def reason_daily(self, bundle, candidates):
        raise ModelError("DeepSeek adapter is not configured; use the mock adapter for deterministic runs")

    def answer_question(self, question, bundle, candidates):
        raise ModelError("DeepSeek adapter is not configured; use the mock adapter for deterministic runs")


class MockMedicalModelAdapter(MedicalModelAdapter):
    model_id = "mock-medical-v0.1"

    def review(self, bundle, hypothesis_types, question_text=None):
        contexts = {c.get("context_type") for c in bundle.get("recent_context", [])}
        findings = []
        escalation = False
        if "ACUTE_ILLNESS_SUSPECTED" in hypothesis_types or contexts & {"ILLNESS", "FEVER", "SORE_THROAT", "NASAL_CONGESTION"}:
            findings.append("wearable 变化不能用于确诊；应结合体温与症状自测")
            findings.append("如症状持续或加重，建议就医评估")
            escalation = True
        else:
            findings.append("未发现需要升级医学安全流程的明确证据")
        findings.append("本系统不提供临床诊断")
        return {"findings": findings, "escalation": escalation}


class MedGemmaMedicalModelAdapter(MedicalModelAdapter):
    model_id = "medgemma-1.5-4b"

    def __init__(self, endpoint=None):
        self.endpoint = endpoint

    def review(self, bundle, hypothesis_types, question_text=None):
        raise ModelError("MedGemma adapter is not configured (no endpoint); use the mock medical adapter")
