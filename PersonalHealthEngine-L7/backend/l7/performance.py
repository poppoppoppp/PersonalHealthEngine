"""Privacy-safe latency telemetry for the private L7 product API."""

from __future__ import annotations

import math
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from l7.store.db import utc_now


STAGE_COLUMNS = {
    "db": "db_ms",
    "bundle_assembly": "bundle_assembly_ms",
    "deepseek_semantic": "deepseek_semantic_ms",
    "deepseek_reasoning": "deepseek_reasoning_ms",
    "medgemma_load": "medgemma_load_ms",
    "medgemma_prompt_eval": "medgemma_prompt_eval_ms",
    "medgemma_eval": "medgemma_eval_ms",
    "medgemma_total": "medgemma_total_ms",
    "finalizer": "finalizer_ms",
    "response_serialization": "response_serialization_ms",
}


@dataclass
class RequestMetrics:
    request_id: str
    method: str
    endpoint: str
    started: float = field(default_factory=time.perf_counter)
    stages_ms: dict[str, float] = field(default_factory=dict)
    model_counts: dict[str, int] = field(default_factory=dict)


current_request_metrics: ContextVar[RequestMetrics | None] = ContextVar(
    "l7_current_request_metrics", default=None,
)


@contextmanager
def measure_stage(stage: str):
    """Accumulate a named stage on the active request, if one exists."""
    if stage not in STAGE_COLUMNS:
        raise ValueError(f"unknown performance stage {stage!r}")
    metrics = current_request_metrics.get()
    started = time.perf_counter()
    try:
        yield
    finally:
        if metrics is not None:
            elapsed = (time.perf_counter() - started) * 1000
            metrics.stages_ms[stage] = metrics.stages_ms.get(stage, 0.0) + elapsed


def _ns_to_ms(value) -> float:
    return round(float(value or 0) / 1_000_000, 3)


def record_model_meta(kind: str, meta: dict | None) -> None:
    """Attach native provider timings without prompts, output, or credentials."""
    metrics = current_request_metrics.get()
    if metrics is None or not meta:
        return
    if kind != "medgemma":
        return
    metrics.stages_ms.update({
        "medgemma_load": _ns_to_ms(meta.get("load_duration_ns")),
        "medgemma_prompt_eval": _ns_to_ms(meta.get("prompt_eval_duration_ns")),
        "medgemma_eval": _ns_to_ms(meta.get("eval_duration_ns")),
        "medgemma_total": _ns_to_ms(meta.get("total_duration_ns")),
    })
    for key in ("prompt_eval_count", "eval_count"):
        value = meta.get(key)
        if value is not None:
            metrics.model_counts[key] = int(value)


def persist_request_metrics(
    con: sqlite3.Connection,
    metrics: RequestMetrics,
    *,
    status_code: int,
    response_bytes: int,
    error_category: str | None = None,
) -> None:
    total_ms = round((time.perf_counter() - metrics.started) * 1000, 3)
    values = [
        metrics.request_id,
        metrics.method,
        metrics.endpoint,
        status_code,
        total_ms,
        *(round(metrics.stages_ms.get(stage, 0.0), 3) for stage in STAGE_COLUMNS),
        metrics.model_counts.get("prompt_eval_count"),
        metrics.model_counts.get("eval_count"),
        max(int(response_bytes), 0),
        error_category,
        utc_now(),
    ]
    con.execute(
        "INSERT INTO performance_requests ("
        "request_id,method,endpoint,status_code,total_ms,"
        + ",".join(STAGE_COLUMNS.values())
        + ",prompt_eval_count,eval_count,response_bytes,error_category,created_at_utc) "
        + "VALUES (" + ",".join("?" for _ in values) + ")",
        values,
    )
    con.commit()


def _nearest_rank(values: list[float], percentile: float) -> float:
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return round(values[index], 3)


def summarize_request_metrics(
    con: sqlite3.Connection, *, endpoint: str | None = None,
) -> dict:
    sql = "SELECT status_code,total_ms FROM performance_requests"
    params: tuple = ()
    if endpoint is not None:
        sql += " WHERE endpoint=?"
        params = (endpoint,)
    rows = con.execute(sql, params).fetchall()
    if not rows:
        return {"count": 0, "p50_ms": None, "p95_ms": None,
                "p99_ms": None, "error_rate": 0.0}
    values = sorted(float(row["total_ms"]) for row in rows)
    errors = sum(1 for row in rows if int(row["status_code"]) >= 400)
    return {
        "count": len(rows),
        "p50_ms": _nearest_rank(values, 0.50),
        "p95_ms": _nearest_rank(values, 0.95),
        "p99_ms": _nearest_rank(values, 0.99),
        "error_rate": round(errors / len(rows), 6),
    }
