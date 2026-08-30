"""Import seam for the SEALED Layer 6 code.

L7 reuses L6 deterministic logic strictly by importing the sealed modules — never by
copying, editing, or re-implementing them. The L6 scripts directory is placed on
`sys.path` once; all imports are plain module imports.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path


def ensure_l6_on_path(l6_code_dir: str) -> None:
    p = str(Path(l6_code_dir).resolve())
    if p not in sys.path:
        sys.path.insert(0, p)


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in value)


def _is_chinese_product_text(value: str) -> bool:
    if not _contains_cjk(value) or "_" in value:
        return False
    allowed_latin = {"AI", "COVID", "HRV", "REM"}
    return all(
        word.upper() in allowed_latin
        for word in re.findall(r"[A-Za-z]{2,}", value)
    )


class ProductDeepSeekReasoningAdapter:
    """L7 product-language wrapper around the real sealed L6 DeepSeek transport."""

    contract_version = "l7-product-zh-CN-v2"

    def __init__(self, **kwargs):
        real = importlib.import_module("l6_real_adapters_v0_1")
        self._real_module = real
        self._base = real.RealDeepSeekReasoningModelAdapter(**kwargs)
        self.model_id = self._base.model_id

    def _chat(self, system, user, operation):
        return self._base._chat(system, user, operation=operation)

    def _daily_system(self, candidates):
        return self._base._daily_system(candidates) + (
            " All user-facing text MUST be written in natural Simplified Chinese (简体中文), "
            "including reasoning_summary and every recommended action. Do not output English "
            "sentences, raw enum names, statistical field names, or diagnostic claims in those fields."
        )

    def _validated_product_json(self, content, required_fields):
        result = self._real_module._extract_json(content)
        for field in required_fields:
            value = result.get(field)
            if not isinstance(value, str) or not value.strip() or not _is_chinese_product_text(value):
                raise self._real_module.RealModelError(
                    f"product output field {field} is not Simplified-Chinese text"
                )
        actions = result.get("recommended_actions", [])
        if not isinstance(actions, list) or len(actions) > 3 or any(
            not isinstance(action, str) or not _is_chinese_product_text(action) for action in actions
        ):
            raise self._real_module.RealModelError("product actions are not valid Chinese text")
        return result

    def reason_daily(self, bundle, candidates):
        user = self._real_module.canonical_json({
            "evidence_bundle": bundle,
            "hypothesis_candidates": candidates,
        })
        content = self._chat(
            self._daily_system(candidates), user, operation="today",
        )
        return self._validated_product_json(content, ("reasoning_summary",))

    def reason_daily_lenient(self, bundle, candidates):
        """Same request as reason_daily, but returns the raw parsed JSON without
        enforcing the product-Chinese contract. Callers that can salvage field by field
        (drop a non-Chinese action, retry on an off-contract summary) use this so one
        imperfect model sample does not have to become a degraded judgment."""
        user = self._real_module.canonical_json({
            "evidence_bundle": bundle,
            "hypothesis_candidates": candidates,
        })
        content = self._chat(
            self._daily_system(candidates), user, operation="today",
        )
        return self._real_module._extract_json(content)

    def answer_question(self, question, bundle, candidates):
        schema = {
            "answer_text": "string: direct answer first",
            "reasoning_summary": "string: concise evidence-grounded reason",
            "recommended_actions": ["0 to 3 actionable strings"],
        }
        system = (
            self._daily_system(candidates)
            + " Answer the user's health decision question using ONLY the evidence bundle. "
            + "Return STRICT JSON matching this Q&A schema: "
            + json.dumps(schema, ensure_ascii=False)
            + ". Keep answer_text and reasoning_summary separate."
        )
        user = self._real_module.canonical_json({
            "question": question,
            "evidence_bundle": bundle,
            "hypothesis_candidates": candidates,
        })
        content = self._chat(system, user, operation="qna")
        return self._validated_product_json(content, ("answer_text", "reasoning_summary"))

    def answer_question_candidate(self, question, bundle, candidates):
        schema = {
            "direct_answer": "Simplified-Chinese direct answer first",
            "reason": "Simplified-Chinese evidence-grounded reason",
            "recommended_actions": ["0 to 3 actionable Simplified-Chinese strings"],
            "confidence": "VERY_LOW | LOW | MODERATE | HIGH",
            "evidence_refs": ["one or more exact keys from evidence_catalog"],
            "medical_claims": ["explicit medical claims, or empty"],
            "uncertainties": ["material uncertainties, or empty"],
        }
        system = (
            self._daily_system(candidates)
            + " Answer the user's question using ONLY the question-specific evidence bundle. "
            + "direct_answer and reason MUST cite at least one specific personal value from the "
            + "bundle with its date (e.g. 昨晚睡眠 7 小时 53 分、清醒 16 分钟、静息心率 64) — never "
            + "answer with generic advice that could apply to anyone. Every important judgment must "
            + "cite exact evidence_catalog keys. Never invent symptoms, training, alcohol, medication, "
            + "disease, temperature, HRV, or history. Return STRICT JSON: "
            + json.dumps(schema, ensure_ascii=False)
        )
        content = self._chat(
            system,
            self._real_module.canonical_json({
                "question": question,
                "personal_evidence_bundle": bundle,
                "hypothesis_candidates": candidates,
            }),
            operation="qna_reasoning",
        )
        return self._real_module._extract_json(content)

    def revise_question_candidate(self, question, bundle, candidate, required_changes):
        schema = {
            "direct_answer": "Simplified-Chinese direct answer first",
            "reason": "Simplified-Chinese evidence-grounded reason",
            "recommended_actions": ["0 to 3 actionable Simplified-Chinese strings"],
            "confidence": "VERY_LOW | LOW | MODERATE | HIGH",
            "evidence_refs": ["exact keys from evidence_catalog"],
            "medical_claims": ["explicit medical claims, or empty"],
            "uncertainties": ["material uncertainties, or empty"],
        }
        system = (
            "Revise an existing personal-health candidate only to apply the supplied medical "
            "required_changes. Preserve the evidence conclusions and confidence; do not add facts, "
            "diagnosis, causality, or advice. Use only the supplied evidence catalog and return "
            "STRICT JSON matching: " + json.dumps(schema, ensure_ascii=False)
        )
        content = self._chat(
            system,
            self._real_module.canonical_json({
                "question": question,
                "personal_evidence_bundle": bundle,
                "candidate": candidate,
                "required_changes": required_changes,
            }),
            operation="qna_medical_revision",
        )
        return self._real_module._extract_json(content)

    def classify_question(self, question, conversation_semantics):
        schema = {
            "scope": "HEALTH_DECISION | HEALTH_DATA | HEALTH_CONTEXT | PRODUCT_META | OUT_OF_SCOPE",
            "intent": "short canonical string",
            "decision_type": "canonical string or null",
            "relevant_domains": ["canonical strings"],
            "relevant_metrics": ["canonical strings"],
            "requires_personal_evidence": True,
            "time_range": "CURRENT | TODAY | LAST_NIGHT | LAST_7_DAYS | LAST_30_DAYS | RECENT | null",
            "aggregation": "LATEST | AVERAGE | TREND | null",
            "medical_consequence": "NONE | LOW | MODERATE | HIGH",
            "needs_medical_review": False,
            "potential_context": False,
            "context_write": "NONE | AUTO_SAVE | CONFIRM",
            "reason": "short machine-readable explanation",
        }
        system = (
            "Classify one message for a personal-health decision assistant. Use prior turns only "
            "to resolve references; they are not health-fact evidence. PRODUCT_META covers identity "
            "and capability questions. Physical-activity advice is HEALTH_DECISION with decision_type "
            "PHYSICAL_ACTIVITY and at least MODERATE consequence. Set potential_context only when "
            "the user explicitly reports a personal fact; a question alone is never context. Use "
            "AUTO_SAVE only for a clear high-confidence fact, CONFIRM for materially ambiguous facts, "
            "and NONE otherwise. Return STRICT JSON matching: "
            + json.dumps(schema, ensure_ascii=False)
        )
        content = self._chat(
            system,
            json.dumps({
                "question": question,
                "recent_conversation_semantics": conversation_semantics,
            }, ensure_ascii=False),
            operation="qna_semantic",
        )
        return self._real_module._extract_json(content)

    def translate_product_copy(self, reasoning_summary, recommended_actions):
        schema = {
            "reasoning_summary": "Simplified-Chinese string",
            "recommended_actions": ["same number of Simplified-Chinese action strings"],
        }
        system = (
            "Translate existing personal-health product copy into natural Simplified Chinese. "
            "Preserve every fact, uncertainty, recommendation, and degree of confidence exactly. "
            "Do not add diagnosis, causality, evidence, or advice. Return STRICT JSON matching: "
            + json.dumps(schema, ensure_ascii=False)
        )
        content = self._chat(
            system,
            json.dumps({
                "reasoning_summary": reasoning_summary,
                "recommended_actions": recommended_actions,
            }, ensure_ascii=False),
            operation="product_translation",
        )
        result = self._validated_product_json(content, ("reasoning_summary",))
        if len(result["recommended_actions"]) != len(recommended_actions):
            raise self._real_module.RealModelError("translation changed the action count")
        return result

    def extract_context(self, text, today):
        return self._base.extract_context(text, today)


class ProductDeepSeekMedicalCriticAdapter:
    """DeepSeek-backed medical critic implementing the sealed review contract.

    The 2-vCPU host runs the local 4B reviewer at ~1.2 tokens/s — a real review takes
    minutes and always exceeds the adapter timeout, leaving medical-flagged days
    permanently fail-closed. The cloud transport reviews the compact candidate bundle
    in seconds and reads better than a quantized 4B. Same statuses, strict JSON, and
    user-facing text stays Simplified Chinese."""

    model_id = "deepseek-v4-flash-medical-critic"

    def __init__(self, **kwargs):
        real = importlib.import_module("l6_real_adapters_v0_1")
        self._real = real
        self._base = real.RealDeepSeekReasoningModelAdapter(**kwargs)

    def review(self, review_bundle, hypothesis_types, question_text=None):
        schema = {
            "review_status": (
                "APPROVED | APPROVED_WITH_CHANGES | REJECTED | ESCALATE | UNAVAILABLE"
            ),
            "medical_concerns": ["Simplified-Chinese concerns, or empty"],
            "causality_concerns": ["unfounded causal claims in the candidate, or empty"],
            "missing_safety_considerations": ["or empty"],
            "unsafe_actions": ["actions that are unsafe given the evidence, or empty"],
            "required_changes": [
                "concrete Simplified-Chinese edits when APPROVED_WITH_CHANGES, else empty"
            ],
            "escalation_reason": "Simplified-Chinese reason when ESCALATE, else null",
            "review_summary": "one Simplified-Chinese sentence",
        }
        system = (
            "You are a conservative medical safety reviewer for a personal-health "
            "decision assistant. Review the candidate answer strictly against the "
            "resolved evidence and safety context. You are not diagnosing the user. "
            "Escalate when the question or evidence suggests acute illness or a red "
            "flag. Reject or require changes when the candidate asserts unfounded "
            "causality, invents symptoms, or gives advice unsafe for the reported "
            "state. Every string field MUST be Simplified Chinese. Return STRICT JSON "
            "matching: " + json.dumps(schema, ensure_ascii=False)
        )
        user = self._real.canonical_json({
            "medical_review_bundle": review_bundle,
            "hypothesis_types": hypothesis_types,
            "question": question_text,
        })
        content = self._base._chat(system, user, operation="qna_medical_review")
        result = self._real._extract_json(content)
        status = result.get("review_status")
        if status not in (
            "APPROVED", "APPROVED_WITH_CHANGES", "REJECTED", "ESCALATE", "UNAVAILABLE",
        ):
            raise self._real.RealModelError(f"invalid review_status {status!r}")
        return result


class L6Bridge:
    """Lazily-imported handles to the sealed L6 modules."""

    def __init__(self, l6_code_dir: str):
        ensure_l6_on_path(l6_code_dir)
        self.core = importlib.import_module("l6_core_v0_1")
        self.evidence = importlib.import_module("l6_evidence_v0_1")
        self.adapters = importlib.import_module("l6_adapters_v0_1")
        self.materializer = importlib.import_module("l6_reasoning_materializer_v0_1")
        # Real adapters are imported lazily so an absent optional dependency or missing
        # credential never breaks the deterministic default path.
        self._real = None

    @property
    def real_adapters(self):
        if self._real is None:
            self._real = importlib.import_module("l6_real_adapters_v0_1")
        return self._real

    # Convenience proxies -------------------------------------------------
    def load_definition(self, path, expected_id):
        return self.core.load_definition(Path(path), expected_id)

    def assemble_evidence(self, l3, l4, l5, analysis_date, recent_context, recent_feedback, similar_cases):
        return self.evidence.assemble_evidence(
            l3, l4, l5, analysis_date, recent_context, recent_feedback, similar_cases
        )

    def bundle_sha256(self, bundle):
        return self.evidence.bundle_sha256(bundle)
