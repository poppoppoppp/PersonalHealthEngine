"""Durability and concurrency contracts for asynchronous health writes."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from l7.api.app import create_app
from l7.jobs import JobRepository
from l7.store.db import connect_l7
from l7.worker import JobWorker


AUTH = {"Authorization": "Bearer dev-local-token"}


def test_context_is_durable_before_202_without_model_or_evaluation(env, monkeypatch):
    app = create_app(env["cfg"], env["orch"])

    def forbidden(*args, **kwargs):
        raise AssertionError("request handler must not run model or evaluation")

    monkeypatch.setattr(env["adapter"], "extract_context", forbidden)
    monkeypatch.setattr(env["orch"], "evaluate", forbidden)
    with TestClient(app) as client:
        response = client.post(
            "/context",
            headers={**AUTH, "Idempotency-Key": "context-20260824-1"},
            json={"text": "今天有点头疼", "date": "2026-08-24"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True and body["status"] == "PENDING"
    row = env["l7"].execute(
        "SELECT j.id,j.status,s.input_json FROM durable_jobs j "
        "JOIN write_submissions s ON s.id=j.submission_id WHERE j.id=?",
        (body["job_id"],),
    ).fetchone()
    assert row is not None and row["status"] == "PENDING"
    assert "今天有点头疼" in row["input_json"]


def test_feedback_returns_202_and_retry_deduplicates(env, monkeypatch):
    app = create_app(env["cfg"], env["orch"])

    def forbidden(*args, **kwargs):
        raise AssertionError("request handler must not evaluate")

    monkeypatch.setattr(env["orch"], "evaluate", forbidden)
    headers = {**AUTH, "Idempotency-Key": "feedback-20260824-1"}
    payload = {"verdict": "准确"}
    with TestClient(app) as client:
        first = client.post("/feedback", headers=headers, json=payload)
        second = client.post("/feedback", headers=headers, json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert env["l7"].execute(
        "SELECT COUNT(*) FROM durable_jobs WHERE idempotency_key=?",
        ("feedback-20260824-1",),
    ).fetchone()[0] == 1


def test_claim_is_atomic_across_connections(env):
    repo = JobRepository(env["l7"])
    queued = repo.enqueue(
        user_id="owner", kind="CONTEXT_INGEST", input_data={"text": "x"},
        idempotency_key="atomic-1",
    )
    second_con = connect_l7(env["cfg"].l7_db)
    try:
        other = JobRepository(second_con)
        claimed = repo.claim_next(worker_id="worker-a")
        assert claimed["id"] == queued["job_id"]
        assert other.claim_next(worker_id="worker-b") is None
    finally:
        second_con.close()


def test_simultaneous_duplicate_enqueues_coalesce(env):
    def submit():
        con = connect_l7(env["cfg"].l7_db)
        try:
            return JobRepository(con).enqueue(
                user_id="owner", kind="CONTEXT_INGEST", input_data={"text": "same"},
                idempotency_key="simultaneous-1",
            )["job_id"]
        finally:
            con.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        job_ids = list(pool.map(lambda _: submit(), range(2)))
    assert job_ids[0] == job_ids[1]


def test_stale_claim_recovery_and_bounded_backoff(env):
    repo = JobRepository(env["l7"], max_attempts=3, max_backoff_seconds=8)
    queued = repo.enqueue(
        user_id="owner", kind="CONTEXT_INGEST", input_data={"text": "x"},
        idempotency_key="recovery-1",
    )
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    env["l7"].execute(
        "UPDATE durable_jobs SET status='RUNNING',locked_at_utc=?,worker_id='dead' WHERE id=?",
        (old, queued["job_id"]),
    )
    env["l7"].commit()
    claimed = repo.claim_next(worker_id="worker-new", stale_after_seconds=60)
    assert claimed["id"] == queued["job_id"]
    repo.fail(claimed["id"], error_category="TimeoutError")
    row = env["l7"].execute(
        "SELECT status,attempts,available_at_utc,error_category FROM durable_jobs WHERE id=?",
        (claimed["id"],),
    ).fetchone()
    assert row["status"] == "PENDING" and row["attempts"] == 1
    delay = datetime.fromisoformat(row["available_at_utc"]) - datetime.now(timezone.utc)
    assert timedelta(0) <= delay <= timedelta(seconds=8)


def test_job_status_never_returns_submission_input(env):
    app = create_app(env["cfg"], env["orch"])
    with TestClient(app) as client:
        accepted = client.post(
            "/context",
            headers={**AUTH, "Idempotency-Key": "status-privacy-1"},
            json={"text": "private health statement"},
        ).json()
        status = client.get(f"/jobs/{accepted['job_id']}", headers=AUTH)
    assert status.status_code == 200
    assert "private health statement" not in status.text


def test_worker_processes_context_after_ack_and_records_result_version(env):
    repo = JobRepository(env["l7"])
    queued = repo.enqueue(
        user_id="owner", kind="CONTEXT_INGEST",
        input_data={"text": "今天有点头疼", "date": "2026-08-24"},
        idempotency_key="worker-context-1",
    )
    worker = JobWorker(env["cfg"], orchestrator=env["orch"], worker_id="test-worker")
    try:
        assert worker.run_once() is True
    finally:
        worker.l7.close()
    row = env["l7"].execute(
        "SELECT status,result_version,error_category,queue_latency_ms,run_latency_ms "
        "FROM durable_jobs WHERE id=?",
        (queued["job_id"],),
    ).fetchone()
    assert row["status"] == "SUCCEEDED", row["error_category"]
    assert row["result_version"] is not None
    assert row["queue_latency_ms"] is not None and row["queue_latency_ms"] >= 0
    assert row["run_latency_ms"] is not None and row["run_latency_ms"] >= 0
