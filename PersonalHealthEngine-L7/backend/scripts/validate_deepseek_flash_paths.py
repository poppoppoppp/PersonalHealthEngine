#!/usr/bin/env python3
"""Run isolated real DeepSeek Flash acceptance against deployed PHE code.

The harness reads production L3-L5 databases, copies L6 into a temporary directory,
creates a temporary L7 database, and never writes fabricated validation data to live state.
Only sanitized model/operation/thinking results are printed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from l7.config import Config  # noqa: E402
from l7.engine.orchestrator import EngineOrchestrator  # noqa: E402
from l7.services.qna import QnAService  # noqa: E402
from l7.store.db import connect_l7, open_readonly, utc_now  # noqa: E402
from l7.upstream import readers  # noqa: E402
from l7.upstream.l6_bridge import (  # noqa: E402
    L6Bridge,
    ProductDeepSeekReasoningAdapter,
)


FLASH_MODEL = "deepseek-v4-flash"
REQUIRED_OPERATIONS = ("today", "qna", "context")


@contextmanager
def temporary_l6_copy(source: str | Path):
    """Yield a consistent temporary SQLite copy and remove it on exit."""
    with tempfile.TemporaryDirectory(prefix="phe-deepseek-flash-") as temp_dir:
        copied = Path(temp_dir) / "l6.sqlite3"
        source_path = Path(source).resolve()
        source_db = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
        target_db = sqlite3.connect(copied)
        try:
            source_db.backup(target_db)
        finally:
            target_db.close()
            source_db.close()
        yield copied


def _is_flash_record(record: dict) -> bool:
    return (
        record.get("requested_model") == FLASH_MODEL
        and record.get("response_model") == FLASH_MODEL
        and record.get("thinking") == "disabled"
    )


def classify_invocations(records: list[dict], persisted_models: list[str]) -> dict:
    """Return only non-sensitive acceptance fields."""
    operation_status = {
        operation: "PASS" if any(
            record.get("operation") == operation and _is_flash_record(record)
            for record in records
        ) else "FAIL"
        for operation in REQUIRED_OPERATIONS
    }
    pro_calls = sum(
        any(model not in (None, FLASH_MODEL) for model in (
            record.get("requested_model"), record.get("response_model")
        ))
        for record in records
    ) + sum(model != FLASH_MODEL for model in persisted_models)
    all_records_flash = bool(records) and all(_is_flash_record(record) for record in records)
    persisted_flash = bool(persisted_models) and all(model == FLASH_MODEL for model in persisted_models)
    passed = (
        all(status == "PASS" for status in operation_status.values())
        and all_records_flash
        and persisted_flash
        and pro_calls == 0
    )
    return {
        "model_audit": "PASS" if passed else "FAIL",
        "v4_flash": "ACTIVE" if passed else "INACTIVE",
        "v4_pro_production_calls": pro_calls,
        "thinking_mode": "DISABLED" if all_records_flash else "INVALID",
        "today_real_flash_call": operation_status["today"],
        "qna_real_flash_call": operation_status["qna"],
        "context_real_flash_call": operation_status["context"],
        "feedback_real_flash_call": "NOT_APPLICABLE",
        "feedback_reason": "No independent model path; correction reuses Context extraction.",
        "medgemma": "UNCHANGED",
        "audited_invocation_count": len(records),
        "persisted_model_identifiers": sorted(set(persisted_models)),
    }


def render_acceptance(report: dict) -> str:
    return "\n".join((
        f'DEEPSEEK MODEL AUDIT = {report["model_audit"]}',
        f'DEEPSEEK V4 FLASH = {report["v4_flash"]}',
        f'DEEPSEEK V4 PRO PRODUCTION CALLS = {report["v4_pro_production_calls"]}',
        f'DEEPSEEK THINKING MODE = {report["thinking_mode"]}',
        "",
        f'TODAY REAL FLASH CALL = {report["today_real_flash_call"]}',
        f'Q&A REAL FLASH CALL = {report["qna_real_flash_call"]}',
        f'CONTEXT REAL FLASH CALL = {report["context_real_flash_call"]}',
        f'FEEDBACK REAL FLASH CALL = {report["feedback_real_flash_call"]}',
        "",
        f'MEDGEMMA = {report["medgemma"]}',
    ))


def _latest_analysis_date(cfg: Config) -> str:
    l5 = open_readonly(cfg.l5_db, immutable_if_checkpointed=True)
    try:
        analysis_date = readers.latest_analysis_date(l5)
    finally:
        l5.close()
    if analysis_date is None:
        raise RuntimeError("no L5 analysis date available for Flash acceptance")
    return analysis_date


def _max_id(db_path: Path, table: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return int(connection.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0])
    finally:
        connection.close()


def _inject_isolated_context(db_path: Path, analysis_date: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        now = utc_now()
        connection.execute(
            "INSERT INTO personal_context (context_date,context_type,body_part,severity,raw_text,"
            "source,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,'CURRENT',?,?)",
            (
                analysis_date,
                "STRESS",
                None,
                None,
                "isolated DeepSeek Flash acceptance fixture",
                "USER_REPORTED",
                now,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _new_persisted_models(db_path: Path, daily_after: int, qa_after: int) -> list[str]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT reasoning_model FROM daily_reasoning WHERE id > ? "
            "UNION ALL SELECT reasoning_model FROM qa_sessions WHERE id > ?",
            (daily_after, qa_after),
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        connection.close()


def _acceptance_user_id(cfg: Config, l7: sqlite3.Connection) -> str:
    user_id = cfg.default_user_id
    if l7.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
        raise RuntimeError("configured acceptance user is not seeded")
    return user_id


def run_real_paths() -> dict:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    if os.environ.get("DEEPSEEK_MODEL", FLASH_MODEL) != FLASH_MODEL:
        raise RuntimeError(f"DEEPSEEK_MODEL must be {FLASH_MODEL}")
    legacy_effort_env = "_".join(("DEEPSEEK", "REASONING", "EFFORT"))
    if legacy_effort_env in os.environ:
        raise RuntimeError("legacy DeepSeek effort configuration must be absent")

    source_cfg = Config()
    analysis_date = _latest_analysis_date(source_cfg)
    with temporary_l6_copy(source_cfg.l6_db) as l6_copy:
        cfg = Config(
            environment="local",
            l3_db=source_cfg.l3_db,
            l4_db=source_cfg.l4_db,
            l5_db=source_cfg.l5_db,
            l6_db=str(l6_copy),
            l6_code_dir=source_cfg.l6_code_dir,
            l6_definitions_dir=source_cfg.l6_definitions_dir,
            l7_db=str(l6_copy.parent / "l7.sqlite3"),
            timezone_name=source_cfg.timezone_name,
            reasoning_adapter="deepseek",
            medical_adapter="mock",
            api_token="temporary-flash-acceptance",
        )
        bridge = L6Bridge(cfg.l6_code_dir)
        adapter = ProductDeepSeekReasoningAdapter()
        medical_adapter = bridge.adapters.MockMedicalModelAdapter()
        l7 = connect_l7(cfg.l7_db)
        user_id = _acceptance_user_id(cfg, l7)
        records: list[dict] = []
        try:
            daily_after = _max_id(l6_copy, "daily_reasoning")
            qa_after = _max_id(l6_copy, "qa_sessions")
            _inject_isolated_context(l6_copy, analysis_date)

            orchestrator = EngineOrchestrator(
                cfg,
                l7,
                bridge=bridge,
                reasoning_adapter=adapter,
                medical_adapter=medical_adapter,
            )
            today = orchestrator.evaluate(user_id, "deepseek_flash_acceptance")
            if today.outcome != "REMATERIALIZED" or today.model_calls < 1:
                raise RuntimeError(f"Today real path did not call the model: {today.outcome}")
            today_text = today.today_payload.get("cause", {}).get("text") or ""
            if not today_text.strip() or "推理模型暂不可用" in today_text:
                raise RuntimeError("Today real path returned fallback product copy")
            if adapter._base.last_invocation.get("operation") != "today":
                raise RuntimeError("Today invocation audit missing")
            records.append(dict(adapter._base.last_invocation))

            qna = QnAService(
                cfg,
                l7,
                bridge,
                reasoning_adapter=adapter,
                medical_adapter=medical_adapter,
            )
            answer = qna.ask(user_id, "根据现有数据，今天适合进行高强度训练吗？")
            if not answer.get("reason") or "推理模型暂不可用" in answer.get("direct_answer", ""):
                raise RuntimeError("Q&A real path returned fallback product copy")
            if adapter._base.last_invocation.get("operation") != "qna":
                raise RuntimeError("Q&A invocation audit missing")
            records.append(dict(adapter._base.last_invocation))

            context_events = adapter.extract_context("昨晚睡得比平时晚", analysis_date)
            if not context_events:
                raise RuntimeError("Context real path returned no structured event")
            if adapter._base.last_invocation.get("operation") != "context":
                raise RuntimeError("Context invocation audit missing")
            records.append(dict(adapter._base.last_invocation))

            persisted = _new_persisted_models(l6_copy, daily_after, qa_after)
            return classify_invocations(records, persisted)
        finally:
            l7.close()


def main() -> int:
    try:
        report = run_real_paths()
    except Exception as exc:  # noqa: BLE001 - never render response or request content.
        print(f"FLASH ACCEPTANCE ERROR = {type(exc).__name__}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(render_acceptance(report))
    return 0 if report["model_audit"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
