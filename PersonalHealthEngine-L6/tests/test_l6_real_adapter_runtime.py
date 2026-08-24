from pathlib import Path
import json
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from l6_real_adapters_v0_1 import (  # noqa: E402
    DEEPSEEK_MODEL_DEFAULT,
    MEDICAL_MODEL_TIMEOUT_S,
    RealDeepSeekReasoningModelAdapter,
    RealMedGemmaMedicalModelAdapter,
    RealModelUnavailable,
)


def test_medgemma_default_timeout_supports_cpu_vps_inference():
    assert MEDICAL_MODEL_TIMEOUT_S >= 600
    assert RealMedGemmaMedicalModelAdapter().timeout_s == MEDICAL_MODEL_TIMEOUT_S


def test_medgemma_timeout_remains_explicitly_configurable():
    assert RealMedGemmaMedicalModelAdapter(timeout_s=30).timeout_s == 30


def test_medgemma_review_matches_sealed_adapter_protocol(monkeypatch):
    adapter = RealMedGemmaMedicalModelAdapter(timeout_s=30)
    captured = {}

    def fake_chat(bundle, question_text=None):
        captured["bundle"] = bundle
        captured["question_text"] = question_text
        return {"review_status": "APPROVED"}

    monkeypatch.setattr(adapter, "_chat", fake_chat)

    result = adapter.review(
        {"analysis_date": "2026-08-24"},
        ["RECOVERY_STRAIN"],
        "今天还能训练吗？",
    )

    assert result["review_status"] == "APPROVED"
    assert captured["question_text"] == "今天还能训练吗？"


def test_deepseek_transport_is_flash_non_thinking_and_sanitized(monkeypatch, capsys):
    import l6_real_adapters_v0_1 as real_adapters

    captured = {}

    def fake_post_json(url, payload, api_key, timeout_s=120):
        captured.update({"url": url, "payload": payload, "api_key": api_key})
        return {
            "id": "chatcmpl-test",
            "model": "deepseek-v4-flash",
            "choices": [{"message": {"content": '{"events": []}'}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        }

    monkeypatch.setattr(real_adapters, "_post_json", fake_post_json)
    adapter = RealDeepSeekReasoningModelAdapter(api_key="secret-test-key")

    content = adapter._chat("private system prompt", "private user health data", operation="context")

    assert DEEPSEEK_MODEL_DEFAULT == "deepseek-v4-flash"
    assert content == '{"events": []}'
    assert captured["payload"]["model"] == "deepseek-v4-flash"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in captured["payload"]
    assert adapter.last_invocation == {
        "event": "deepseek_invocation",
        "operation": "context",
        "requested_model": "deepseek-v4-flash",
        "response_model": "deepseek-v4-flash",
        "thinking": "disabled",
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }
    serialized_audit = json.dumps(adapter.last_invocation)
    emitted_audit = capsys.readouterr().err
    assert "DEEPSEEK_AUDIT" in emitted_audit
    assert '"operation":"context"' in emitted_audit
    assert "secret-test-key" not in serialized_audit
    assert "private system prompt" not in serialized_audit
    assert "private user health data" not in serialized_audit
    assert "chatcmpl-test" not in serialized_audit
    assert "secret-test-key" not in emitted_audit
    assert "private system prompt" not in emitted_audit
    assert "private user health data" not in emitted_audit
    assert "chatcmpl-test" not in emitted_audit


def test_deepseek_transport_rejects_non_flash_before_network(monkeypatch):
    import l6_real_adapters_v0_1 as real_adapters

    network_called = False

    def fake_post_json(*args, **kwargs):
        nonlocal network_called
        network_called = True
        raise AssertionError("network must not be called")

    monkeypatch.setattr(real_adapters, "_post_json", fake_post_json)
    adapter = RealDeepSeekReasoningModelAdapter(
        api_key="secret-test-key",
        model="deepseek-v4-pro",
    )

    with pytest.raises(RealModelUnavailable, match="deepseek-v4-flash"):
        adapter._chat("system", "user", operation="today")

    assert network_called is False


def test_deepseek_transport_rejects_mismatched_response_model(monkeypatch):
    import l6_real_adapters_v0_1 as real_adapters

    monkeypatch.setattr(
        real_adapters,
        "_post_json",
        lambda *args, **kwargs: {
            "model": "deepseek-v4-pro",
            "choices": [{"message": {"content": "{}"}}],
            "usage": {},
        },
    )
    adapter = RealDeepSeekReasoningModelAdapter(api_key="secret-test-key")

    with pytest.raises(RealModelUnavailable, match="response model"):
        adapter._chat("system", "user", operation="qna")

    assert not hasattr(adapter, "last_invocation") or adapter.last_invocation is None


def test_deepseek_public_paths_assign_audit_operations(monkeypatch):
    adapter = RealDeepSeekReasoningModelAdapter(api_key="secret-test-key")
    operations = []

    def fake_chat(system, user, operation):
        operations.append(operation)
        if operation == "context":
            return '{"events": []}'
        return "{}"

    monkeypatch.setattr(adapter, "_chat", fake_chat)

    adapter.reason_daily({}, [])
    adapter.answer_question("今天可以训练吗？", {}, [])
    adapter.extract_context("昨晚睡得很晚", "2026-08-24")

    assert operations == ["today", "qna", "context"]


def test_deepseek_context_requires_complete_sealed_event_shape(monkeypatch):
    adapter = RealDeepSeekReasoningModelAdapter(api_key="x")
    monkeypatch.setattr(
        adapter,
        "_chat",
        lambda *args, **kwargs: '{"events": [{"context_type": "LATE_SLEEP"}]}',
    )

    with pytest.raises(Exception, match="context_date"):
        adapter.extract_context("昨晚睡得很晚", "2026-08-24")


def test_deepseek_context_accepts_headache_as_user_reported_symptom(monkeypatch):
    adapter = RealDeepSeekReasoningModelAdapter(api_key="secret-test-key")
    monkeypatch.setattr(
        adapter,
        "_chat",
        lambda *args, **kwargs: (
            '{"events": [{"context_type": "HEADACHE", '
            '"context_date": "2026-08-24", "body_part": "head"}]}'
        ),
    )

    events = adapter.extract_context("我今天有点头疼", "2026-08-24")

    assert events == [{
        "context_type": "HEADACHE",
        "context_date": "2026-08-24",
        "body_part": "head",
    }]
