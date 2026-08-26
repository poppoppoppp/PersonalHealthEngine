"""Small SQLite-backed job repository for durable health-write processing."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone


ALLOWED_KINDS = {
    "CONTEXT_INGEST", "CONTEXT_CORRECT", "CONTEXT_DELETE", "FEEDBACK_SUBMIT",
    "QA_ASK",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


class JobRepository:
    def __init__(self, con: sqlite3.Connection, *, max_attempts: int = 5,
                 max_backoff_seconds: int = 300):
        self.con = con
        self.max_attempts = max_attempts
        self.max_backoff_seconds = max_backoff_seconds

    def enqueue(self, *, user_id: str, kind: str, input_data: dict,
                idempotency_key: str) -> dict:
        if kind not in ALLOWED_KINDS:
            raise ValueError("unsupported job kind")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("invalid idempotency key")
        input_json = json.dumps(
            input_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        input_sha = hashlib.sha256(input_json.encode("utf-8")).hexdigest()
        now = _iso(_now())
        self.con.execute("BEGIN IMMEDIATE")
        try:
            existing = self.con.execute(
                "SELECT id,status,input_sha256,created_at_utc FROM durable_jobs "
                "WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["input_sha256"] != input_sha:
                    raise ValueError("idempotency key reused with different input")
                self.con.commit()
                return {
                    "accepted": True,
                    "job_id": existing["id"],
                    "status": existing["status"],
                    "persisted_at": existing["created_at_utc"],
                    "deduplicated": True,
                }
            submission = self.con.execute(
                "INSERT INTO write_submissions "
                "(user_id,kind,input_json,input_sha256,status,created_at_utc,updated_at_utc) "
                "VALUES (?,?,?,?, 'PENDING',?,?)",
                (user_id, kind, input_json, input_sha, now, now),
            )
            job = self.con.execute(
                "INSERT INTO durable_jobs "
                "(user_id,kind,submission_id,idempotency_key,input_sha256,status,"
                "available_at_utc,created_at_utc,updated_at_utc) "
                "VALUES (?,?,?,?,?,'PENDING',?,?,?)",
                (user_id, kind, submission.lastrowid, idempotency_key, input_sha,
                 now, now, now),
            )
            self.con.commit()
            return {
                "accepted": True,
                "job_id": job.lastrowid,
                "status": "PENDING",
                "persisted_at": now,
                "deduplicated": False,
            }
        except Exception:
            self.con.rollback()
            raise

    def claim_next(self, *, worker_id: str, stale_after_seconds: int = 900) -> dict | None:
        now_dt = _now()
        now = _iso(now_dt)
        stale_before = _iso(now_dt - timedelta(seconds=stale_after_seconds))
        self.con.execute("BEGIN IMMEDIATE")
        try:
            self.con.execute(
                "UPDATE durable_jobs SET status='PENDING',worker_id=NULL,locked_at_utc=NULL,"
                "available_at_utc=?,updated_at_utc=? "
                "WHERE status='RUNNING' AND locked_at_utc<? AND attempts<?",
                (now, now, stale_before, self.max_attempts),
            )
            row = self.con.execute(
                "SELECT id FROM durable_jobs WHERE status='PENDING' "
                "AND available_at_utc<=? AND attempts<? ORDER BY id LIMIT 1",
                (now, self.max_attempts),
            ).fetchone()
            if row is None:
                self.con.commit()
                return None
            changed = self.con.execute(
                "UPDATE durable_jobs SET status='RUNNING',attempts=attempts+1,"
                "worker_id=?,locked_at_utc=?,updated_at_utc=?,"
                "queue_latency_ms=(julianday(?)-julianday(created_at_utc))*86400000.0 "
                "WHERE id=? AND status='PENDING'",
                (worker_id, now, now, now, row["id"]),
            ).rowcount
            if changed != 1:
                self.con.rollback()
                return None
            claimed = self.con.execute(
                "SELECT j.*,s.input_json FROM durable_jobs j "
                "JOIN write_submissions s ON s.id=j.submission_id WHERE j.id=?",
                (row["id"],),
            ).fetchone()
            self.con.commit()
            result = dict(claimed)
            result["input_data"] = json.loads(result.pop("input_json"))
            return result
        except Exception:
            self.con.rollback()
            raise

    def complete(self, job_id: int, *, result: dict,
                 result_version: int | None = None) -> None:
        now = _iso(_now())
        result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
        self.con.execute("BEGIN IMMEDIATE")
        try:
            row = self.con.execute(
                "SELECT submission_id FROM durable_jobs WHERE id=? AND status='RUNNING'",
                (job_id,),
            ).fetchone()
            if row is None:
                raise LookupError("running job not found")
            self.con.execute(
                "UPDATE durable_jobs SET status='SUCCEEDED',result_version=?,"
                "error_category=NULL,run_latency_ms="
                "(julianday(?)-julianday(locked_at_utc))*86400000.0,updated_at_utc=? "
                "WHERE id=?",
                (result_version, now, now, job_id),
            )
            self.con.execute(
                "UPDATE write_submissions SET status='SUCCEEDED',result_json=?,"
                "error_category=NULL,updated_at_utc=? WHERE id=?",
                (result_json, now, row["submission_id"]),
            )
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise

    def fail(self, job_id: int, *, error_category: str) -> None:
        row = self.con.execute(
            "SELECT attempts,submission_id FROM durable_jobs WHERE id=? AND status='RUNNING'",
            (job_id,),
        ).fetchone()
        if row is None:
            raise LookupError("running job not found")
        now_dt = _now()
        terminal = row["attempts"] >= self.max_attempts
        status = "FAILED" if terminal else "PENDING"
        delay = min(self.max_backoff_seconds, 2 ** row["attempts"])
        available = _iso(now_dt if terminal else now_dt + timedelta(seconds=delay))
        now = _iso(now_dt)
        self.con.execute("BEGIN IMMEDIATE")
        try:
            self.con.execute(
                "UPDATE durable_jobs SET status=?,available_at_utc=?,worker_id=NULL,"
                "run_latency_ms=(julianday(?)-julianday(locked_at_utc))*86400000.0,"
                "locked_at_utc=NULL,error_category=?,updated_at_utc=? WHERE id=?",
                (status, available, now, error_category[:80], now, job_id),
            )
            self.con.execute(
                "UPDATE write_submissions SET status=?,error_category=?,updated_at_utc=? WHERE id=?",
                (status, error_category[:80], now, row["submission_id"]),
            )
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise

    def status(self, *, user_id: str, job_id: int) -> dict:
        row = self.con.execute(
            "SELECT j.id,j.kind,j.status,j.attempts,j.result_version,j.error_category,"
            "j.created_at_utc,j.updated_at_utc,s.result_json FROM durable_jobs j "
            "JOIN write_submissions s ON s.id=j.submission_id "
            "WHERE j.id=? AND j.user_id=?",
            (job_id, user_id),
        ).fetchone()
        if row is None:
            raise LookupError("job not found")
        result = dict(row)
        result_json = result.pop("result_json")
        if result["status"] == "SUCCEEDED" and result_json is not None:
            result["result"] = json.loads(result_json)
        return result
