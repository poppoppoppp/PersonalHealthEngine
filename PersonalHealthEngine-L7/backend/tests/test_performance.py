import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import l7.api.app as app_module
from l7.api.app import create_app
from l7.services.qna import QnAService
from l7.performance import (
    RequestMetrics,
    current_request_metrics,
    measure_stage,
    record_model_meta,
    summarize_request_metrics,
)
from l7.store.db import SCHEMA_VERSION, connect_l7


def test_performance_schema_is_migrated(tmp_path):
    db_path = tmp_path / "l7.sqlite3"
    con = connect_l7(str(db_path))
    try:
        assert SCHEMA_VERSION >= 3
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "performance_requests" in tables
        columns = {
            row[1]
            for row in con.execute("PRAGMA table_info(performance_requests)")
        }
        assert {
            "request_id",
            "endpoint",
            "total_ms",
            "db_ms",
            "bundle_assembly_ms",
            "deepseek_semantic_ms",
            "deepseek_reasoning_ms",
            "medgemma_load_ms",
            "medgemma_prompt_eval_ms",
            "medgemma_eval_ms",
            "medgemma_total_ms",
            "finalizer_ms",
            "response_serialization_ms",
            "response_bytes",
        } <= columns
    finally:
        con.close()


def test_request_metrics_accumulate_stages_and_native_ollama_meta():
    metrics = RequestMetrics(request_id="req-1", method="POST", endpoint="/qa/ask")
    token = current_request_metrics.set(metrics)
    try:
        with measure_stage("db"):
            pass
        with measure_stage("deepseek_semantic"):
            pass
        record_model_meta(
            "medgemma",
            {
                "load_duration_ns": 2_000_000,
                "prompt_eval_duration_ns": 3_000_000,
                "eval_duration_ns": 5_000_000,
                "total_duration_ns": 11_000_000,
                "prompt_eval_count": 42,
                "eval_count": 7,
            },
        )
    finally:
        current_request_metrics.reset(token)

    assert metrics.stages_ms["db"] >= 0
    assert metrics.stages_ms["deepseek_semantic"] >= 0
    assert metrics.stages_ms["medgemma_load"] == 2
    assert metrics.stages_ms["medgemma_prompt_eval"] == 3
    assert metrics.stages_ms["medgemma_eval"] == 5
    assert metrics.stages_ms["medgemma_total"] == 11
    assert metrics.model_counts == {"prompt_eval_count": 42, "eval_count": 7}


def test_middleware_persists_sanitized_metrics_and_request_id(env):
    app = create_app(env["cfg"], env["orch"])
    with TestClient(app) as client:
        response = client.get("/health?private=do-not-record")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    row = env["l7"].execute(
        "SELECT * FROM performance_requests WHERE request_id=?",
        (response.headers["x-request-id"],),
    ).fetchone()
    assert row is not None
    assert row["endpoint"] == "/health"
    assert row["method"] == "GET"
    assert row["status_code"] == 200
    assert row["total_ms"] >= 0
    assert row["response_bytes"] > 0
    assert "private" not in " ".join(str(value) for value in row)
    column_names = {
        column[1]
        for column in env["l7"].execute("PRAGMA table_info(performance_requests)")
    }
    assert not {
        "request_body", "response_body", "prompt_text", "context_text", "secret",
    } & column_names


def test_telemetry_write_failure_never_breaks_product_request(env, monkeypatch):
    def fail_telemetry(*args, **kwargs):
        raise sqlite3.OperationalError("database is busy")

    monkeypatch.setattr(app_module, "persist_request_metrics", fail_telemetry)
    app = create_app(env["cfg"], env["orch"])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_percentile_summary_is_deterministic(tmp_path):
    con = connect_l7(str(tmp_path / "metrics.sqlite3"))
    try:
        for index, duration in enumerate((10, 20, 30, 40, 50), start=1):
            con.execute(
                "INSERT INTO performance_requests "
                "(request_id,method,endpoint,status_code,total_ms,response_bytes,created_at_utc) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"r{index}", "GET", "/today", 200, duration, 100, f"2026-08-24T00:00:0{index}Z"),
            )
        con.commit()
        summary = summarize_request_metrics(con, endpoint="/today")
    finally:
        con.close()

    assert summary == {
        "count": 5,
        "p50_ms": 30.0,
        "p95_ms": 50.0,
        "p99_ms": 50.0,
        "error_rate": 0.0,
    }


def test_slow_qna_never_blocks_health_event_loop(env, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_ask(self, user_id, question, conversation_id=None):
        started.set()
        release.wait(timeout=2)
        return {"scope": "HEALTH_DECISION", "direct_answer": "test"}

    monkeypatch.setattr(QnAService, "ask", slow_ask)
    app = create_app(env["cfg"], env["orch"])
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        inference = pool.submit(
            client.post,
            "/qa/ask",
            json={"question": "我今天适合跑步吗？"},
            headers={"Authorization": "Bearer dev-local-token"},
        )
        assert started.wait(timeout=1)
        before = time.perf_counter()
        health = client.get("/health")
        elapsed = time.perf_counter() - before
        release.set()
        assert inference.result(timeout=3).status_code == 200

    assert health.status_code == 200
    assert elapsed < 0.5
