"""Tests for the DeepSeek-backed medical critic adapter (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from l7.engine.qna_orchestration import validate_medical_review
from l7.upstream.l6_bridge import ProductDeepSeekMedicalCriticAdapter


class FakeTransport:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def _chat(self, system, user, operation):
        self.calls.append({"system": system, "user": user, "operation": operation})
        return self.content


def make_adapter(content):
    adapter = ProductDeepSeekMedicalCriticAdapter.__new__(
        ProductDeepSeekMedicalCriticAdapter,
    )
    adapter._real = pytest.importorskip("l6_real_adapters_v0_1")
    adapter.model_id = ProductDeepSeekMedicalCriticAdapter.model_id
    adapter._chat_transport = FakeTransport(content)
    adapter._base = type(
        "Base", (), {"_chat": lambda self, s, u, operation: adapter._chat_transport._chat(s, u, operation)},
    )()
    return adapter


APPROVED_JSON = (
    '{"review_status": "APPROVED", "medical_concerns": [], "causality_concerns": [],'
    ' "missing_safety_considerations": [], "unsafe_actions": [], "required_changes": [],'
    ' "escalation_reason": null, "review_summary": "候选回答与证据一致，未发现安全问题。"}'
)


def test_review_parses_strict_json_and_passes_contract():
    adapter = make_adapter(APPROVED_JSON)
    raw = adapter.review({"resolved_evidence": {"a": "睡眠低于基线"}}, ["SLEEP_DEFICIT"], "今天能练腿吗")
    validated = validate_medical_review(raw)
    assert validated["review_status"] == "APPROVED"
    call = adapter._chat_transport.calls[0]
    assert call["operation"] == "qna_medical_review"
    assert "medical_review_bundle" in call["user"]


def test_review_rejects_invalid_status_instead_of_passing_garbage():
    adapter = make_adapter('{"review_status": "SURE_FINE"}')
    with pytest.raises(Exception):
        adapter.review({}, [], "q")


def test_escalation_payload_survives_validation():
    content = (
        '{"review_status": "ESCALATE", "medical_concerns": ["提示急性疾病可能"],'
        ' "causality_concerns": [], "missing_safety_considerations": ["发热未确认"],'
        ' "unsafe_actions": ["高强度训练"], "required_changes": [],'
        ' "escalation_reason": "存在需要及时就医的信号", "review_summary": "建议尽快就医。"}'
    )
    adapter = make_adapter(content)
    validated = validate_medical_review(adapter.review({}, [], "我发烧39度能跑步吗"))
    assert validated["review_status"] == "ESCALATE"
