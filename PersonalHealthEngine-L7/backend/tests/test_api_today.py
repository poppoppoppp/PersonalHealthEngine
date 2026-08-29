"""Integration tests: Product API contract over the real engine state."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from l7.api.app import create_app

TOKEN = "dev-local-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client(env):
    app = create_app(env["cfg"], env["orch"])
    with TestClient(app) as c:
        yield c


def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_today_requires_auth(client):
    assert client.get("/today").status_code == 401
    assert client.get("/today", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_today_contract_fields(client, env):
    r = client.get("/today", headers=AUTH)
    assert r.status_code == 200
    p = r.json()
    for key in ("schema", "product_state", "product_state_label", "headline",
                "information_order", "cause", "actions", "confidence",
                "medical_attention", "analysis_date", "data_as_of",
                "updated_at_utc", "updated_at_local_hhmm", "judgment_updated",
                "evidence_level2", "version_id"):
        assert key in p, f"missing {key}"
    assert p["schema"] == "l7.today/v1"
    assert p["product_state"] in ("A", "B", "C", "D", "E")
    assert isinstance(p["actions"], list) and len(p["actions"]) <= 3
    assert env["adapter"].reason_daily_calls == 0, "serving Today must not call a model here"


def test_repeated_today_opens_cost_nothing(client, env):
    client.get("/today", headers=AUTH)
    client.get("/today", headers=AUTH)
    client.get("/today", headers=AUTH)
    assert env["adapter"].reason_daily_calls == 0
    runs = client.get("/today/eval-runs", headers=AUTH).json()["runs"]
    assert all(run["model_calls"] == 0 for run in runs)


def test_today_versions_append_only(client):
    client.get("/today", headers=AUTH)
    v1 = client.get("/today/versions", headers=AUTH).json()["versions"]
    assert len(v1) == 1
    assert v1[0]["product_state"] == "C"
    assert v1[0]["l6_daily_reasoning_id"] is not None


def test_evidence_detail_exposes_only_deviating_metrics(client):
    r = client.get("/evidence/today", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["analysis_date"] == "2026-08-16"
    assert isinstance(body["metrics"], list) and body["metrics"]
    m = body["metrics"][0]
    for key in ("feature_name", "metric_label", "deviation_class", "baseline_maturity",
                "series", "deviations", "baselines"):
        assert key in m, f"missing {key}"
    assert m["deviation_class"] in ("ABOVE_TYPICAL_RANGE", "BELOW_TYPICAL_RANGE")


def test_today_evidence_contains_only_primary_features(client):
    """Regression 2026-08-29: sleep sub-features (segment count, stage duplicates)
    surfaced on the home screen as separate '睡眠' entries with meaningless values.
    User-facing evidence facts must be canonical per-metric features, plus the one
    meaningful secondary (awake duration), with primaries leading."""
    from l7.upstream.readers import PRIMARY_FEATURE_NAMES, SECONDARY_FEATURE_NAMES

    today = client.get("/today", headers=AUTH).json()
    assert today["evidence"], "fixture bundle must carry at least one primary deviation"
    for fact in today["evidence"]:
        assert fact["feature_name"] in PRIMARY_FEATURE_NAMES | SECONDARY_FEATURE_NAMES, (
            f"non-user-facing feature surfaced as evidence: {fact['feature_name']}"
        )
        assert fact["display_label"], "every fact needs a display label"
    names = [fact["feature_name"] for fact in today["evidence"]]
    assert len(names) == len(set(names)), "no duplicate values for the same metric"
    primary_positions = [
        i for i, fact in enumerate(today["evidence"])
        if fact["feature_name"] in PRIMARY_FEATURE_NAMES
    ]
    secondary_positions = [
        i for i, fact in enumerate(today["evidence"])
        if fact["feature_name"] in SECONDARY_FEATURE_NAMES
    ]
    assert not primary_positions or not secondary_positions or (
        max(primary_positions) < min(secondary_positions)
    ), "primaries must lead, secondaries follow"

    detail = client.get("/evidence/today", headers=AUTH).json()
    used = [m for m in detail["all_metrics"] if m["used_in_judgment"]]
    primary_facts = [
        fact for fact in today["evidence"]
        if fact["feature_name"] in PRIMARY_FEATURE_NAMES
    ]
    assert len(primary_facts) == len(used), (
        "primary evidence count must match the evidence page's 本次使用 count"
    )


def test_refresh_returns_current_copy_while_a_job_is_in_flight(client):
    """Pull-to-refresh must not spin for minutes behind the worker's re-evaluation
    after a context correction: with a job queued, refresh returns the current copy
    immediately instead of starting a duplicate model evaluation."""
    r = client.post(
        "/context",
        json={"text": "今晚早点睡"},
        headers={**AUTH, "Idempotency-Key": "refresh-fast-path-test"},
    )
    assert r.status_code == 202
    import time

    started = time.monotonic()
    refresh = client.post("/today/refresh", headers=AUTH)
    elapsed = time.monotonic() - started
    assert refresh.status_code == 200
    body = refresh.json()
    assert body["outcome"] == "JOB_IN_PROGRESS"
    assert body["model_calls"] == 0
    assert body["today"]["schema"] == "l7.today/v1"
    assert elapsed < 10, "fast path must not wait on the worker's evaluation"


def test_sleep_structure_endpoint(client):
    r = client.get("/history/sleep-structure?days=14", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["note"]
    nights = body["nights"]
    assert nights, "production L3 copy carries real sleep episodes"
    assert len(nights) <= 14
    newest = nights[0]
    assert newest["local_date"] >= nights[-1]["local_date"], "nights sorted newest first"
    for night in nights:
        assert night["sleep_minutes"] > 0
        assert night["total_minutes"] >= night["sleep_minutes"]
        ratio = night["awake_ratio"]
        assert ratio is None or 0 <= ratio < 1
    assert "深睡" in body["note"], "must be honest about the two-stage source structure"


def test_evidence_detail_includes_eight_readable_metric_overviews(client):
    r = client.get("/evidence/today", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    metrics = body["all_metrics"]
    assert [item["key"] for item in metrics] == [
        "steps",
        "active_calories",
        "sleep",
        "heart_rate",
        "resting_heart_rate",
        "spo2",
        "stress",
        "workouts",
    ]
    for item in metrics:
        for key in (
            "label",
            "value_display",
            "data_date",
            "freshness_status",
            "freshness_label",
            "used_in_judgment",
            "series",
        ):
            assert key in item, f"{item['key']} missing {key}"
        user_text = " ".join(
            str(item.get(key) or "")
            for key in ("label", "value_display", "freshness_label", "availability_note")
        )
        assert "bucket_count" not in user_text
        assert all(layer not in user_text for layer in ("L3", "L4", "L5"))

    unavailable = metrics[-1]
    assert unavailable["key"] == "workouts"
    assert unavailable["freshness_status"] == "UNAVAILABLE"
    assert unavailable["availability_note"]
    by_key = {item["key"]: item for item in metrics}
    assert by_key["active_calories"]["value_display"].endswith("（设备记录）")
    assert by_key["stress"]["value_display"].endswith("（设备原始指标）")


def test_patterns_projection_shape(client):
    r = client.get("/patterns", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["patterns"] == []          # all 12 sealed patterns are single-observation OBSERVING
    assert body["observing_count"] == 12
    assert body["accumulation_note"], "must show accumulation state when nothing qualifies (§46)"


def test_settings_roundtrip(client):
    s = client.get("/settings", headers=AUTH).json()["settings"]
    assert s["notification_mode"] == "SMART"
    r = client.put("/settings", headers=AUTH, json={"notification_mode": "QUIET"})
    assert r.status_code == 200
    assert client.get("/settings", headers=AUTH).json()["settings"]["notification_mode"] == "QUIET"
    bad = client.put("/settings", headers=AUTH, json={"notification_mode": "SOMETIMES"})
    assert bad.status_code == 400
    unknown = client.put("/settings", headers=AUTH, json={"favorite_color": "blue"})
    assert unknown.status_code == 400


def test_usage_reports_zero_paid_calls(client):
    client.get("/today", headers=AUTH)
    u = client.get("/usage", headers=AUTH).json()
    assert u["total_model_calls"] == 0
