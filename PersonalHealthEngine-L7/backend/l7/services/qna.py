"""Q&A Service — Personal Health Decision Assistant (§18–§22).

Health facts always come from the engine (Personal Evidence Bundle); the conversation
store only carries dialogue semantics. The medical-review policy is the sealed L6 policy;
MedGemma is only invoked on trigger, never per request.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from l7.config import Config
from l7.store.db import open_readonly, utc_now
from l7.upstream import readers
from l7.upstream.l6_bridge import L6Bridge

HEALTH_SCOPE_MARKERS = (
    "练", "训练", "跑步", "跑", "健身", "运动", "睡", "睡眠", "心率", "心跳", "咖啡", "咖啡因",
    "身体", "恢复", "疲劳", "累", "压力", "酒", "病", "疼", "痛", "不舒服", "状态", "学习",
    "工作", "熬夜", "休息", "吃饭", "饮食", "血氧", "体检", "症状", "发烧", "感冒",
    "train", "workout", "run", "sleep", "heart rate", "coffee", "recovery", "stress",
    "alcohol", "sick", "pain", "tired", "energy",
)

REFUSAL_TEXT = (
    "这个问题超出了我的范围。我只回答与你身体状态和健康决策相关的问题，"
    "比如今天能不能训练、睡眠不足怎么安排、指标变化意味着什么。"
)


def in_health_scope(question: str) -> bool:
    q = question.lower()
    return any(m in q for m in HEALTH_SCOPE_MARKERS)


class QnAService:
    def __init__(self, config: Config, l7: sqlite3.Connection, bridge: L6Bridge,
                 reasoning_adapter=None, medical_adapter=None):
        self.cfg = config
        self.l7 = l7
        self.bridge = bridge
        self._reasoning = reasoning_adapter
        self._medical = medical_adapter

    @property
    def reasoning_adapter(self):
        if self._reasoning is None:
            from l7.engine.orchestrator import EngineOrchestrator
            tmp = EngineOrchestrator(self.cfg, self.l7, bridge=self.bridge)
            self._reasoning = tmp.reasoning_adapter
        return self._reasoning

    @property
    def medical_adapter(self):
        if self._medical is None:
            from l7.engine.orchestrator import EngineOrchestrator
            tmp = EngineOrchestrator(self.cfg, self.l7, bridge=self.bridge)
            self._medical = tmp.medical_adapter
        return self._medical

    # -- conversation lifecycle --------------------------------------------
    def _sleep_exists_for(self, local_date: str) -> bool:
        l3 = open_readonly(self.cfg.l3_db, immutable_if_checkpointed=True)
        try:
            row = l3.execute(
                "SELECT COUNT(*) FROM derived_features WHERE local_date=? "
                "AND feature_name LIKE 'sleep_source_episode.%' AND status='CURRENT'",
                (local_date,),
            ).fetchone()
            return row[0] > 0
        finally:
            l3.close()

    def open_or_roll_conversation(self, user_id: str, now_local: datetime | None = None) -> dict:
        """Strong boundary (§22): after the date advances and the long sleep ended, start a
        fresh short-term conversation. Durable facts stay retrievable regardless."""
        now_local = now_local or datetime.now(ZoneInfo(self.cfg.timezone_name))
        today = now_local.date().isoformat()

        row = self.l7.execute(
            "SELECT c.* FROM conversations c WHERE c.user_id=? AND c.status='OPEN'"
            " ORDER BY c.id DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        if row is not None:
            opened_local = datetime.fromisoformat(row["opened_at_utc"]).astimezone(
                ZoneInfo(self.cfg.timezone_name)
            ).date()
            last = self.l7.execute(
                "SELECT MAX(created_at_utc) FROM qa_turns WHERE conversation_id=?", (row["id"],)
            ).fetchone()[0]
            last_local_date = (
                datetime.fromisoformat(last).astimezone(ZoneInfo(self.cfg.timezone_name)).date()
                if last else opened_local
            )
            date_advanced = today > last_local_date.isoformat()
            long_sleep_ended = self._sleep_exists_for(today) or now_local.hour >= 12
            if date_advanced and long_sleep_ended:
                self.l7.execute(
                    "UPDATE conversations SET status='CLOSED', closed_at_utc=?, boundary_reason=?"
                    " WHERE id=?",
                    (utc_now(), "date_advanced_and_long_sleep_ended", row["id"]),
                )
                self.l7.commit()
                row = None

        if row is None:
            cur = self.l7.execute(
                "INSERT INTO conversations (user_id, opened_at_utc, status) VALUES (?,?,'OPEN')",
                (user_id, utc_now()),
            )
            self.l7.commit()
            return {"conversation_id": cur.lastrowid, "rolled_over": True}
        return {"conversation_id": row["id"], "rolled_over": False}

    def conversation_state(self, user_id: str, conversation_id: int) -> dict:
        turns = self.l7.execute(
            "SELECT role, text, created_at_utc FROM qa_turns WHERE conversation_id=?"
            " ORDER BY id",
            (conversation_id,),
        ).fetchall()
        return {"conversation_id": conversation_id,
                "turns": [dict(t) for t in turns]}

    # -- answering ----------------------------------------------------------
    def ask(self, user_id: str, question: str, conversation_id: int | None = None,
            today_override: str | None = None) -> dict:
        question = (question or "").strip()
        if not question:
            raise ValueError("empty question")

        if conversation_id is None:
            conversation_id = self.open_or_roll_conversation(user_id)["conversation_id"]

        self.l7.execute(
            "INSERT INTO qa_turns (conversation_id,user_id,role,text,created_at_utc)"
            " VALUES (?,?,?,?,?)",
            (conversation_id, user_id, "USER", question, utc_now()),
        )
        self.l7.commit()

        answer = self._answer(user_id, question)

        self.l7.execute(
            "INSERT INTO qa_turns (conversation_id,user_id,role,text,l6_qa_session_id,"
            "evidence_ref_json,created_at_utc) VALUES (?,?,?,?,?,?,?)",
            (conversation_id, user_id, "ASSISTANT", answer["direct_answer"],
             answer.get("l6_qa_session_id"),
             json.dumps(answer["evidence_ref"], ensure_ascii=False), utc_now()),
        )
        self.l7.commit()
        answer["conversation_id"] = conversation_id
        return answer

    def _answer(self, user_id: str, question: str) -> dict:
        if not in_health_scope(question):
            return {
                "answer_first": True,
                "direct_answer": REFUSAL_TEXT,
                "reason": None,
                "actions": [],
                "evidence_ref": {"grounded": False},
                "scope": "OUT_OF_SCOPE",
                "medical_review_state": "BYPASSED",
                "l6_qa_session_id": None,
            }

        core = self.bridge.core
        l3 = open_readonly(self.cfg.l3_db, immutable_if_checkpointed=True)
        l4 = open_readonly(self.cfg.l4_db, immutable_if_checkpointed=True)
        l5 = open_readonly(self.cfg.l5_db, immutable_if_checkpointed=True)
        l6 = open_readonly(self.cfg.l6_db)
        try:
            analysis_date = readers.latest_analysis_date(l5)
            if analysis_date is None:
                return self._insufficient_answer(None, "尚无任何健康数据。")
            recent_context = readers.read_recent_context(l6, analysis_date)
            recent_feedback = [dict(r) for r in l6.execute(
                "SELECT subject_type,subject_id,feedback_status FROM user_feedback"
                " ORDER BY id DESC LIMIT 20")]
            bundle, _prov = self.bridge.assemble_evidence(
                l3, l4, l5, analysis_date, recent_context, recent_feedback, [])
            bundle["overall_state"] = core.overall_state(bundle)
            candidates = core.generate_candidates(bundle)

            if bundle["overall_state"] == "INSUFFICIENT_EVIDENCE" or not candidates:
                missing = "；".join(bundle.get("missing_evidence", [])[:3]) or "个人基线数据不足"
                return self._insufficient_answer(analysis_date, missing)

            hypothesis_types = [c["hypothesis_type"] for c in candidates]
            review_state, reasons = core.medical_trigger(question, bundle, hypothesis_types)
        finally:
            for c in (l3, l4, l5, l6):
                c.close()

        model_calls = 0
        medical_model = None
        if review_state == "REQUIRED":
            medical_model = self.medical_adapter.model_id
            try:
                self.medical_adapter.review(bundle, hypothesis_types, question)
                review_state = "PERFORMED"
                model_calls += 1
            except Exception:
                review_state = "UNAVAILABLE"
        else:
            review_state = "BYPASSED"

        try:
            result = self.reasoning_adapter.answer_question(question, bundle, candidates)
            model_calls += 1
        except Exception:
            result = None

        if result is None:
            direct = "目前不能可靠判断：推理模型暂不可用，请稍后再试。"
            reason = None
            actions: list[str] = []
        else:
            direct = result.get("answer_text") or result.get("reasoning_summary")
            if not isinstance(direct, str) or not direct.strip():
                direct = "目前不能可靠判断。"
            else:
                direct = direct.strip()
            reason = None
            actions = [a for a in (result.get("recommended_actions") or []) if isinstance(a, str)][:3]

        # Persist through the sealed qa_sessions / medical_reviews semantics.
        question_bundle = {
            "analysis_date": analysis_date,
            "overall_state": bundle["overall_state"],
            "candidates": hypothesis_types,
        }
        qhash = core.sha256_text(core.canonical_json(question_bundle))
        now = utc_now()
        l6w = sqlite3.connect(self.cfg.l6_db)
        l6w.row_factory = sqlite3.Row
        try:
            l6w.execute("BEGIN IMMEDIATE")
            cur = l6w.execute(
                "INSERT INTO qa_sessions (question_text,asked_at_utc,evidence_bundle_id,"
                "question_bundle_sha256,answer_json,answer_text,medical_review_state,"
                "reasoning_model,status,created_at_utc,updated_at_utc)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (question, now, None, qhash,
                 core.canonical_json(result) if result else None,
                 direct, review_state, self.reasoning_adapter.model_id, "CURRENT", now, now),
            )
            qa_id = cur.lastrowid
            l6w.execute(
                "INSERT INTO medical_reviews (subject_type,subject_id,review_state,trigger_reason,"
                "findings_json,reviewer_model,created_at_utc) VALUES ('QA',?,?,?,?,?,?)",
                (qa_id, review_state, core.canonical_json(reasons),
                 core.canonical_json({"no_diagnosis": True}), medical_model, now),
            )
            l6w.commit()
        except Exception:
            l6w.rollback()
            raise
        finally:
            l6w.close()

        return {
            "answer_first": True,
            "direct_answer": direct,
            "reason": reason,
            "actions": actions,
            "evidence_ref": {
                "grounded": True,
                "analysis_date": analysis_date,
                "overall_state": bundle["overall_state"],
                "question_bundle_sha256": qhash,
                "bundle_sha256": self.bridge.bundle_sha256(bundle),
            },
            "scope": "HEALTH",
            "medical_review_state": review_state,
            "l6_qa_session_id": qa_id,
        }

    def _insufficient_answer(self, analysis_date: str | None, missing: str) -> dict:
        return {
            "answer_first": True,
            "direct_answer": "目前不能可靠判断。",
            "reason": f"缺少的信息：{missing}",
            "actions": [],
            "evidence_ref": {"grounded": True, "analysis_date": analysis_date,
                             "insufficient": True},
            "scope": "HEALTH",
            "medical_review_state": "BYPASSED",
            "l6_qa_session_id": None,
        }
