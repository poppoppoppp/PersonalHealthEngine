"""Feedback Service (§30–§32).

Feedback at important nodes -> AI-structured classification -> sealed L6 feedback write
path (pattern learning included) -> fact-base re-evaluation. A correction that changes the
facts triggers the full chain: update facts -> rebuild evidence -> re-run L6 (and medical
review if triggered) -> update Today. "Thanks for your feedback" without effect is not an
acceptable outcome.
"""

from __future__ import annotations

import json
import sqlite3

from l7.config import Config
from l7.engine.orchestrator import EngineOrchestrator
from l7.jobs import JobRepository
from l7.store.db import open_readonly, utc_now
from l7.upstream.l6_bridge import L6Bridge

PATTERN_ESTABLISHED_MIN_SUPPORT = 3  # sealed L6 rule

VERDICT_MAP = {
    "准确": "judgment_confirmed",
    "不太准确": "judgment_rejected",
    "补充情况": "context_added",
}


def outcome_signals(bundle: dict) -> set[str]:
    signals: set[str] = set()
    for d in bundle.get("deviations", []):
        if d.get("deviation_class") == "ABOVE_TYPICAL_RANGE":
            signals.add(f"{d.get('metric')}_UP")
        elif d.get("deviation_class") == "BELOW_TYPICAL_RANGE":
            signals.add(f"{d.get('metric')}_DOWN")
    return signals


class FeedbackService:
    def __init__(self, config: Config, l7: sqlite3.Connection, bridge: L6Bridge,
                 orchestrator: EngineOrchestrator, reasoning_adapter=None):
        self.cfg = config
        self.l7 = l7
        self.bridge = bridge
        self.orch = orchestrator
        self._adapter = reasoning_adapter

    @property
    def adapter(self):
        if self._adapter is None:
            self._adapter = self.orch.reasoning_adapter
        return self._adapter

    def enqueue_submit(self, user_id: str, *, verdict: str, text: str | None,
                       subject_type: str, subject_id: int | None,
                       analysis_date: str | None, idempotency_key: str,
                       jobs: JobRepository) -> dict:
        if verdict not in VERDICT_MAP:
            raise ValueError(f"unknown verdict {verdict!r}")
        queued = jobs.enqueue(
            user_id=user_id,
            kind="FEEDBACK_SUBMIT",
            input_data={
                "verdict": verdict,
                "text": text,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "analysis_date": analysis_date,
            },
            idempotency_key=idempotency_key,
        )
        category = VERDICT_MAP[verdict]
        feedback_status = {
            "judgment_confirmed": "CONFIRMED",
            "judgment_rejected": "REJECTED",
            "context_added": "CORRECTED",
        }[category]
        return {
            **queued,
            "feedback_id": None,
            "category": category,
            "feedback_status": feedback_status,
            "corrected_context_ids": [],
            "re_evaluation": None,
        }

    # ------------------------------------------------------------------
    def submit(self, user_id: str, verdict: str, text: str | None = None,
               subject_type: str = "DAILY_REASONING", subject_id: int | None = None,
               analysis_date: str | None = None) -> dict:
        if verdict not in VERDICT_MAP:
            raise ValueError(f"unknown verdict {verdict!r}")
        category = VERDICT_MAP[verdict]
        feedback_status = {
            "judgment_confirmed": "CONFIRMED",
            "judgment_rejected": "REJECTED",
            "context_added": "CORRECTED",
        }[category]

        if subject_id is None or analysis_date is None:
            l6 = open_readonly(self.cfg.l6_db)
            try:
                row = l6.execute(
                    "SELECT id, analysis_date FROM daily_reasoning WHERE status='CURRENT'"
                    + (" AND analysis_date=?" if analysis_date else "")
                    + " ORDER BY id DESC LIMIT 1",
                    ((analysis_date,) if analysis_date else ()),
                ).fetchone()
            finally:
                l6.close()
            if row is None:
                raise LookupError("no CURRENT daily reasoning to give feedback on")
            if subject_id is None:
                subject_id = row["id"]
            if analysis_date is None:
                analysis_date = row["analysis_date"]

        # Refine 补充情况: structured personal fact -> context_added, otherwise reason_corrected.
        if verdict == "补充情况" and text:
            events = self.adapter.extract_context(text, analysis_date) or []
            category = "context_added" if events else "reason_corrected"

        l6w = sqlite3.connect(self.cfg.l6_db)
        l6w.row_factory = sqlite3.Row
        try:
            l6w.execute("BEGIN IMMEDIATE")
            now = utc_now()
            cur = l6w.execute(
                "INSERT INTO user_feedback (subject_type,subject_id,feedback_status,"
                "correction_text,source,created_at_utc) VALUES (?,?,?,?,?,?)",
                (subject_type, subject_id, feedback_status, text, "USER_FEEDBACK", now),
            )
            feedback_id = cur.lastrowid

            bundle = None
            if subject_type == "DAILY_REASONING":
                row = l6w.execute(
                    "SELECT b.bundle_json FROM evidence_bundles b"
                    " JOIN daily_reasoning d ON d.evidence_bundle_id=b.id WHERE d.id=?",
                    (subject_id,),
                ).fetchone()
                if row:
                    bundle = json.loads(row["bundle_json"])
            if bundle is not None:
                self._update_patterns(l6w, bundle, feedback_status, now)

            corrected_ids: list[int] = []
            if feedback_status == "CORRECTED" and text:
                events = self.adapter.extract_context(text, bundle.get("analysis_date") if bundle else None) or []
                for event in events:
                    c = l6w.execute(
                        "INSERT INTO personal_context (context_date,context_type,body_part,"
                        "severity,raw_text,source,status,created_at_utc,updated_at_utc)"
                        " VALUES (?,?,?,?,?,?,'CURRENT',?,?)",
                        (event["context_date"], event["context_type"], event.get("body_part"),
                         event.get("severity"), text, "USER_REPORTED", now, now),
                    )
                    corrected_ids.append(c.lastrowid)
            l6w.commit()
        except Exception:
            l6w.rollback()
            raise
        finally:
            l6w.close()

        # Fact base touched -> re-evaluate (change detection keeps the cost honest).
        re_eval = self.orch.evaluate(user_id, trigger=f"feedback:{category}")
        return {
            "status": "RECORDED",
            "feedback_id": feedback_id,
            "category": category,
            "feedback_status": feedback_status,
            "corrected_context_ids": corrected_ids,
            "re_evaluation": {
                "outcome": re_eval.outcome,
                "model_calls": re_eval.model_calls,
                "judgment_updated": re_eval.judgment_updated,
            },
        }

    # ------------------------------------------------------------------
    def _update_patterns(self, db: sqlite3.Connection, bundle: dict,
                         feedback_status: str, now: str) -> None:
        triggers = [c.get("context_type") for c in bundle.get("recent_context", [])]
        signals = sorted(outcome_signals(bundle))
        if not triggers or not signals:
            return
        for trigger in triggers:
            for signal in signals:
                key = f"{trigger}::{signal}"
                row = db.execute(
                    "SELECT id,support_count,total_count FROM personal_patterns WHERE pattern_key=?",
                    (key,),
                ).fetchone()
                confirmed = 1 if feedback_status == "CONFIRMED" else 0
                if row:
                    support = row["support_count"] + confirmed
                    total = row["total_count"] + 1
                    maturity = "ESTABLISHED" if support >= PATTERN_ESTABLISHED_MIN_SUPPORT else "OBSERVING"
                    db.execute(
                        "UPDATE personal_patterns SET support_count=?,total_count=?,maturity=?,"
                        "last_seen_date=?,updated_at_utc=? WHERE id=?",
                        (support, total, maturity, bundle.get("data_date"), now, row["id"]),
                    )
                else:
                    db.execute(
                        "INSERT INTO personal_patterns (pattern_key,trigger_context_type,"
                        "outcome_signal,support_count,total_count,maturity,first_seen_date,"
                        "last_seen_date,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (key, trigger, signal, confirmed, 1, "OBSERVING",
                         bundle.get("data_date"), bundle.get("data_date"), now, now),
                    )
