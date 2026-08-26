"""Single-consumer worker for durable L7 write jobs."""

from __future__ import annotations

import argparse
import os
import socket
import time

from l7.config import Config
from l7.engine.orchestrator import EngineOrchestrator
from l7.jobs import JobRepository
from l7.services.context import ContextService
from l7.services.feedback import FeedbackService
from l7.services.history import HistoryService
from l7.services.qna import QnAService
from l7.store.db import connect_l7


class JobWorker:
    def __init__(self, config: Config | None = None, *, orchestrator=None,
                 worker_id: str | None = None):
        self.cfg = config or Config()
        self.l7 = connect_l7(self.cfg.l7_db)
        self.orch = orchestrator or EngineOrchestrator(self.cfg, self.l7)
        self.jobs = JobRepository(self.l7)
        bridge = self.orch.bridge
        self.context = ContextService(
            self.cfg, self.l7, bridge, self.orch,
            reasoning_adapter=self.orch._reasoning_adapter,
        )
        self.feedback = FeedbackService(
            self.cfg, self.l7, bridge, self.orch,
            reasoning_adapter=self.orch._reasoning_adapter,
        )
        self.history = HistoryService(self.cfg, self.l7)
        self.qna = QnAService(
            self.cfg, self.l7, bridge,
            reasoning_adapter=self.orch._reasoning_adapter,
            medical_adapter=self.orch._medical_adapter,
            context_writer=self.context,
        )
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"

    def run_once(self) -> bool:
        job = self.jobs.claim_next(worker_id=self.worker_id)
        if job is None:
            return False
        try:
            data = job["input_data"]
            if job["kind"] == "CONTEXT_INGEST":
                result = self.context.ingest(
                    job["user_id"], data["text"], data.get("date"),
                )
            elif job["kind"] == "CONTEXT_CORRECT":
                result = self.context.correct(
                    job["user_id"], data["context_id"], data["text"], data.get("date"),
                )
            elif job["kind"] == "CONTEXT_DELETE":
                result = self.context.delete(job["user_id"], data["context_id"])
            elif job["kind"] == "FEEDBACK_SUBMIT":
                result = self.feedback.submit(
                    job["user_id"], data["verdict"], data.get("text"),
                    subject_type=data.get("subject_type", "DAILY_REASONING"),
                    subject_id=data.get("subject_id"),
                    analysis_date=data.get("analysis_date"),
                )
            elif job["kind"] == "QA_ASK":
                result = self.qna.ask(
                    job["user_id"], data["question"], data.get("conversation_id"),
                )
            else:
                raise ValueError("unsupported job kind")
            version = None
            if job["kind"] != "QA_ASK":
                self.history.rebuild(job["user_id"])
                version = self.l7.execute(
                    "SELECT id FROM today_versions WHERE user_id=? ORDER BY id DESC LIMIT 1",
                    (job["user_id"],),
                ).fetchone()
            self.jobs.complete(
                job["id"], result=result,
                result_version=version["id"] if version else None,
            )
        except Exception as exc:
            self.jobs.fail(job["id"], error_category=type(exc).__name__)
        return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    worker = JobWorker()
    if args.once:
        worker.run_once()
        return
    while True:
        if not worker.run_once():
            time.sleep(max(args.poll_seconds, 0.1))


if __name__ == "__main__":
    main()
