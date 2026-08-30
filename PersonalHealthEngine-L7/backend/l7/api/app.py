"""L7 Product API (FastAPI).

Endpoints are the contract between the engine and any client (Flutter app today, future
web/CLI). No secrets are ever exposed; model credentials never reach this layer.
"""

from __future__ import annotations

import json
import hashlib
import re
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from l7 import __version__
from l7.config import Config
from l7.engine.orchestrator import EngineOrchestrator
from l7.engine.qna_orchestration import deterministic_fast_classification
from l7.jobs import JobRepository
from l7.performance import RequestMetrics, current_request_metrics, persist_request_metrics
from l7.services.context import ContextService
from l7.services.feedback import FeedbackService
from l7.services.history import HistoryService
from l7.services.notify import NotificationService, parse_quiet_hours
from l7.services.qna import QnAService
from l7.services.today import TodayService
from l7.store.db import connect_l7, utc_now
from l7.upstream.l6_bridge import L6Bridge


def create_app(config: Config | None = None, orchestrator: EngineOrchestrator | None = None) -> FastAPI:
    cfg = config or Config()
    l7 = connect_l7(cfg.l7_db)
    orch = orchestrator or EngineOrchestrator(cfg, l7)
    today_service = TodayService(cfg, l7, orch)
    history_service = HistoryService(cfg, l7)
    notify_service = NotificationService(cfg, l7)

    # Notification Threshold hook: fires only when a new judgment version exists.
    if notify_service not in getattr(orch, "_l7_notify_registered", []):
        def _on_judgment(user_id, result):
            payload = result.today_payload or {}
            notify_service.consider(
                user_id,
                payload.get("product_state", "D"),
                result.judgment_updated,
                payload.get("change_note"),
                result.today_version_id,
            )
        orch.judgment_listeners.append(_on_judgment)
        orch._l7_notify_registered = [notify_service]

    bridge = orch.bridge if orchestrator is not None else L6Bridge(cfg.l6_code_dir)
    context_service = ContextService(cfg, l7, bridge, orch,
                                     reasoning_adapter=orch._reasoning_adapter)
    feedback_service = FeedbackService(cfg, l7, bridge, orch,
                                       reasoning_adapter=orch._reasoning_adapter)
    qna_service = QnAService(cfg, l7, bridge,
                             reasoning_adapter=orch._reasoning_adapter,
                             medical_adapter=orch._medical_adapter,
                             context_writer=context_service)
    jobs = JobRepository(l7)

    # Build projections at process startup, never on latency-sensitive GET paths.
    if l7.execute(
        "SELECT 1 FROM today_versions WHERE user_id=? LIMIT 1", (cfg.default_user_id,),
    ).fetchone() is None:
        orch.evaluate(cfg.default_user_id, trigger="startup_bootstrap")
    history_service.rebuild(cfg.default_user_id)

    app = FastAPI(title="Personal Health Engine — L7 Product API", version=__version__)
    if cfg.environment == "local":
        # Dev convenience only: the Flutter web/desktop client runs from another origin.
        # Never enabled outside the local environment.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.state.config = cfg
    app.state.l7 = l7
    app.state.orchestrator = orch
    app.state.today_service = today_service

    token = cfg.resolve_api_token()

    def conditional_json(request: Request, payload: dict) -> Response:
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        etag = f'"{hashlib.sha256(canonical).hexdigest()}"'
        headers = {
            "ETag": etag,
            "Cache-Control": "private, max-age=0, must-revalidate",
        }
        if request.headers.get("If-None-Match") == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(payload, headers=headers)

    @app.middleware("http")
    async def performance_telemetry(request: Request, call_next):
        supplied_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_id
            if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied_id)
            else uuid.uuid4().hex
        )
        metrics = RequestMetrics(
            request_id=request_id,
            method=request.method,
            endpoint=request.url.path,
        )
        context_token = current_request_metrics.set(metrics)
        response = None
        status_code = 500
        error_category = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            route = request.scope.get("route")
            metrics.endpoint = getattr(route, "path", request.url.path)
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            error_category = type(exc).__name__
            raise
        finally:
            current_request_metrics.reset(context_token)
            response_bytes = 0
            if response is not None:
                try:
                    response_bytes = int(response.headers.get("content-length", 0))
                except (TypeError, ValueError):
                    response_bytes = 0
            try:
                persist_request_metrics(
                    l7,
                    metrics,
                    status_code=status_code,
                    response_bytes=response_bytes,
                    error_category=error_category,
                )
            except Exception:
                # Observability is best-effort and must never take the product API down.
                try:
                    l7.rollback()
                except Exception:
                    pass

    def require_auth(request: Request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or header[len("Bearer "):] != token:
            raise HTTPException(status_code=401, detail="unauthorized")
        return cfg.default_user_id

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "environment": cfg.environment}

    @app.get("/today")
    def get_today(request: Request, user_id: str = Depends(require_auth)):
        return conditional_json(
            request, today_service.get_today(user_id, trigger="app_open"),
        )

    async def _request_collection_cycle(timeout_seconds: float = 300.0) -> None:
        """手动刷新 = 采集 + 分析一条龙。

        L1-L5 管线跑在宿主机 systemd 上，容器通过共享的 /runtime 卷发信号：
        写入 collect.request，宿主机的 path 单元启动管线，完成后写 collect.finished
        时间戳。本地开发环境没有 /runtime，直接跳过采集只做分析。"""
        import asyncio
        import os
        from pathlib import Path

        runtime = Path(os.environ.get("PHE_RUNTIME", "/runtime"))
        request_path = runtime / "collect.request"
        finished_path = runtime / "collect.finished"
        try:
            prev = finished_path.stat().st_mtime if finished_path.exists() else 0.0
            request_path.write_text("now")
        except OSError:
            return  # 无共享卷（本地开发）：跳过采集，仅分析
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            await asyncio.sleep(2)
            try:
                if finished_path.stat().st_mtime > prev:
                    return
            except OSError:
                continue

    @app.post("/today/refresh")
    async def refresh_today(request: Request, user_id: str = Depends(require_auth)):
        # A queued health-write job (context correction, feedback) is already re-running
        # the engine in the worker. Blocking this request on a second concurrent
        # evaluation only produces a long spinner and a duplicate model call — return the
        # current copy at once; the job's own evaluation updates Today when it lands.
        try:
            body = await request.json()
        except Exception:
            body = {}
        # Only the pipeline hook runs unattended (gated by the scheduled model gate);
        # every other path is user-initiated and always allowed to reason.
        trigger = (
            "scheduled" if isinstance(body, dict) and body.get("trigger") == "scheduled"
            else "manual_refresh"
        )
        if trigger == "manual_refresh":
            # 手动刷新 = 采集 + 分析一条龙（采集失败/无共享卷时退化为仅分析）。
            await _request_collection_cycle()
        pending = l7.execute(
            "SELECT 1 FROM durable_jobs WHERE user_id=? AND status IN ('PENDING','RUNNING')"
            " LIMIT 1",
            (user_id,),
        ).fetchone()
        if pending is not None:
            try:
                current = today_service.get_today(user_id, trigger="app_open")
            except LookupError:
                result = orch.evaluate(user_id, trigger=trigger)
                return {
                    "outcome": result.outcome,
                    "model_calls": result.model_calls,
                    "judgment_updated": result.judgment_updated,
                    "today": result.today_payload,
                }
            return {
                "outcome": "JOB_IN_PROGRESS",
                "model_calls": 0,
                "judgment_updated": False,
                "today": current,
            }
        result = orch.evaluate(user_id, trigger=trigger)
        return {
            "outcome": result.outcome,
            "model_calls": result.model_calls,
            "judgment_updated": result.judgment_updated,
            "today": result.today_payload,
        }

    @app.get("/today/versions")
    def today_versions(user_id: str = Depends(require_auth)):
        return {"versions": today_service.list_versions(user_id)}

    @app.get("/today/eval-runs")
    def eval_runs(user_id: str = Depends(require_auth)):
        return {"runs": today_service.list_eval_runs(user_id)}

    @app.get("/evidence/today")
    def evidence_today(user_id: str = Depends(require_auth)):
        return today_service.evidence_detail(user_id)

    @app.get("/patterns")
    def patterns(request: Request, user_id: str = Depends(require_auth)):
        return conditional_json(request, today_service.patterns(user_id))

    @app.get("/usage")
    def usage(user_id: str = Depends(require_auth)):
        return today_service.model_usage(user_id)

    @app.get("/settings")
    def get_settings(user_id: str = Depends(require_auth)):
        rows = l7.execute("SELECT key, value_json FROM settings WHERE user_id=?", (user_id,)).fetchall()
        defaults = {"notification_mode": "SMART", "quiet_hours": None}
        for r in rows:
            defaults[r["key"]] = json.loads(r["value_json"])
        return {"settings": defaults}

    @app.put("/settings")
    async def put_settings(request: Request, user_id: str = Depends(require_auth)):
        body = await request.json()
        allowed = {"notification_mode": ("QUIET", "SMART", "DAILY"), "quiet_hours": None}
        for key, value in body.items():
            if key not in allowed:
                raise HTTPException(status_code=400, detail=f"unknown setting {key}")
            if key == "notification_mode" and value not in allowed[key]:
                raise HTTPException(status_code=400, detail="invalid notification_mode")
            if key == "quiet_hours" and value is not None and parse_quiet_hours(value) is None:
                raise HTTPException(status_code=400, detail="quiet_hours must be 'HH:MM-HH:MM'")
            l7.execute(
                "INSERT INTO settings (user_id,key,value_json,updated_at_utc) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id,key) DO UPDATE SET value_json=excluded.value_json,"
                "updated_at_utc=excluded.updated_at_utc",
                (user_id, key, json.dumps(value, ensure_ascii=False), utc_now()),
            )
        l7.commit()
        return get_settings(user_id)

    # ---------------- Q&A (§18–§22) ----------------
    @app.post("/qa/conversations")
    def qa_open_conversation(user_id: str = Depends(require_auth)):
        return qna_service.open_or_roll_conversation(user_id)

    @app.get("/qa/conversations/{conversation_id}")
    def qa_conversation(conversation_id: int,
                        limit: int = Query(30, ge=1, le=50),
                        cursor: int | None = Query(None, ge=1),
                        user_id: str = Depends(require_auth)):
        try:
            return qna_service.conversation_state(
                user_id, conversation_id, limit=limit, cursor=cursor,
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/qa/ask")
    async def qa_ask(request: Request, user_id: str = Depends(require_auth)):
        body = await request.json()
        question = (body or {}).get("question")
        if not question:
            raise HTTPException(status_code=400, detail="question required")
        try:
            if deterministic_fast_classification(question) is not None:
                return await run_in_threadpool(
                    qna_service.ask,
                    user_id,
                    question,
                    body.get("conversation_id"),
                )
            result = jobs.enqueue(
                user_id=user_id,
                kind="QA_ASK",
                input_data={
                    "question": question,
                    "conversation_id": body.get("conversation_id"),
                },
                idempotency_key=request.headers.get("Idempotency-Key") or uuid.uuid4().hex,
            )
            return JSONResponse(result, status_code=202)
        except ValueError as e:
            status = 409 if "idempotency key reused" in str(e) else 400
            raise HTTPException(status_code=status, detail=str(e))

    # ---------------- Context (§23–§29) ----------------
    @app.get("/context")
    def context_list(limit: int = Query(30, ge=1, le=50),
                     cursor: int | None = Query(None, ge=1),
                     user_id: str = Depends(require_auth)):
        return context_service.list_current(user_id, limit=limit, cursor=cursor)

    @app.post("/context")
    async def context_add(request: Request, user_id: str = Depends(require_auth)):
        body = await request.json()
        text = (body or {}).get("text")
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        try:
            result = context_service.enqueue_ingest(
                user_id, text, today=body.get("date"),
                idempotency_key=request.headers.get("Idempotency-Key") or uuid.uuid4().hex,
                jobs=jobs,
            )
            return JSONResponse(result, status_code=202)
        except ValueError as e:
            status = 409 if "idempotency key reused" in str(e) else 400
            raise HTTPException(status_code=status, detail=str(e))

    @app.put("/context/{context_id}")
    async def context_correct(context_id: int, request: Request,
                              user_id: str = Depends(require_auth)):
        body = await request.json()
        text = (body or {}).get("text")
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        try:
            result = context_service.enqueue_correct(
                user_id, context_id, text, today=body.get("date"),
                idempotency_key=request.headers.get("Idempotency-Key") or uuid.uuid4().hex,
                jobs=jobs,
            )
            return JSONResponse(result, status_code=202)
        except ValueError as e:
            status = 409 if "idempotency key reused" in str(e) else 400
            raise HTTPException(status_code=status, detail=str(e))

    @app.delete("/context/{context_id}")
    def context_delete(context_id: int, request: Request,
                       user_id: str = Depends(require_auth)):
        try:
            result = context_service.enqueue_delete(
                user_id, context_id,
                idempotency_key=request.headers.get("Idempotency-Key") or uuid.uuid4().hex,
                jobs=jobs,
            )
            return JSONResponse(result, status_code=202)
        except ValueError as e:
            status = 409 if "idempotency key reused" in str(e) else 400
            raise HTTPException(status_code=status, detail=str(e))

    @app.get("/context/pending-question")
    def context_pending_question(user_id: str = Depends(require_auth)):
        return context_service.pending_question(user_id)

    # ---------------- Feedback (§30–§32) ----------------
    @app.post("/feedback")
    async def feedback_submit(request: Request, user_id: str = Depends(require_auth)):
        body = await request.json() or {}
        verdict = body.get("verdict")
        if not verdict:
            raise HTTPException(status_code=400, detail="verdict required")
        try:
            result = feedback_service.enqueue_submit(
                user_id,
                verdict=verdict,
                text=body.get("text"),
                subject_type=body.get("subject_type", "DAILY_REASONING"),
                subject_id=body.get("subject_id"),
                analysis_date=body.get("analysis_date"),
                idempotency_key=request.headers.get("Idempotency-Key") or uuid.uuid4().hex,
                jobs=jobs,
            )
            return JSONResponse(result, status_code=202)
        except ValueError as e:
            status = 409 if "idempotency key reused" in str(e) else 400
            raise HTTPException(status_code=status, detail=str(e))
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/jobs/{job_id}")
    def job_status(job_id: int, user_id: str = Depends(require_auth)):
        try:
            return jobs.status(user_id=user_id, job_id=job_id)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ---------------- History / Episodes (§37–§40) ----------------
    @app.get("/history/episodes")
    def history_episodes(request: Request,
                         limit: int = Query(30, ge=1, le=50),
                         cursor: int | None = Query(None, ge=1),
                         user_id: str = Depends(require_auth)):
        return conditional_json(
            request,
            history_service.list_episodes(user_id, limit=limit, cursor=cursor),
        )

    @app.get("/history/episodes/{episode_id}")
    def history_episode(episode_id: int,
                        limit: int = Query(30, ge=1, le=50),
                        cursor: int | None = Query(None, ge=1),
                        user_id: str = Depends(require_auth)):
        try:
            return history_service.episode_detail(
                user_id, episode_id, limit=limit, cursor=cursor,
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/history/search")
    def history_search(q: str = "", user_id: str = Depends(require_auth)):
        return history_service.search(user_id, q)

    @app.get("/history/sleep-structure")
    def history_sleep_structure(
        days: int = Query(14, ge=1, le=60),
        user_id: str = Depends(require_auth),
    ):
        return history_service.sleep_structure(user_id, days=days)

    # ---------------- Notifications (§48–§52) ----------------
    @app.get("/notifications")
    def notifications_feed(user_id: str = Depends(require_auth)):
        return notify_service.feed(user_id)

    @app.get("/notifications/decisions")
    def notifications_decisions(user_id: str = Depends(require_auth)):
        return notify_service.decisions(user_id)

    return app


app = None  # lazily created by the entrypoint to avoid import-time side effects


def main() -> None:
    import uvicorn

    application = create_app()
    uvicorn.run(application, host="127.0.0.1", port=8707)


if __name__ == "__main__":
    main()
