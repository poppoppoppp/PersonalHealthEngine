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

    def _chat(self, system, user, reasoning_effort=None):
        return self._base._chat(system, user, reasoning_effort=reasoning_effort)

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
            self._daily_system(candidates), user, reasoning_effort=self._base.reasoning_effort,
        )
        return self._validated_product_json(content, ("reasoning_summary",))

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
        content = self._chat(system, user, reasoning_effort="low")
        return self._validated_product_json(content, ("answer_text", "reasoning_summary"))

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
            reasoning_effort="low",
        )
        result = self._validated_product_json(content, ("reasoning_summary",))
        if len(result["recommended_actions"]) != len(recommended_actions):
            raise self._real_module.RealModelError("translation changed the action count")
        return result

    def extract_context(self, text, today):
        return self._base.extract_context(text, today)


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
