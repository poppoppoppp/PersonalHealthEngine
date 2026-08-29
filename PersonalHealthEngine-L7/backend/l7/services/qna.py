"""Q&A Service — Personal Health Decision Assistant (§18–§22).

Health facts always come from the engine (Personal Evidence Bundle); the conversation
store only carries dialogue semantics. The medical-review policy is the sealed L6 policy;
MedGemma is only invoked on trigger, never per request.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from l7.config import Config
from l7.jobs import JobRepository
from l7.medical_cache import MedicalReviewCache
from l7.performance import measure_stage, record_cache_result, record_model_meta
from l7.engine.qna_orchestration import (
    MEDICAL_CRITIC_PROMPT_VERSION,
    MEDICAL_REVIEW_SCHEMA_VERSION,
    MEDICAL_SAFETY_CONTEXT_TYPES,
    PRODUCT_META_TEXT,
    REFUSAL_TEXT,
    SEMANTIC_UNAVAILABLE_TEXT,
    build_medical_review_bundle,
    deterministic_fast_classification,
    medical_consequence_gate,
    medical_review_cache_key,
    select_question_evidence,
    validate_candidate,
    validate_medical_review,
    validate_semantic_classification,
)
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

def in_health_scope(question: str) -> bool:
    """Legacy deterministic hint retained for tests; never owns production scope routing."""
    q = question.lower()
    return any(m in q for m in HEALTH_SCOPE_MARKERS)


class QnAService:
    def __init__(self, config: Config, l7: sqlite3.Connection, bridge: L6Bridge,
                 reasoning_adapter=None, medical_adapter=None, context_writer=None,
                 job_repository=None):
        self.cfg = config
        self.l7 = l7
        self.bridge = bridge
        self._reasoning = reasoning_adapter
        self._medical = medical_adapter
        self._context_writer = context_writer
        self._jobs = job_repository or JobRepository(l7)
        self._medical_cache = MedicalReviewCache(l7)

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

    def conversation_state(self, user_id: str, conversation_id: int, *, limit: int = 30,
                           cursor: int | None = None) -> dict:
        owner = self.l7.execute(
            "SELECT 1 FROM conversations WHERE id=? AND user_id=?",
            (conversation_id, user_id),
        ).fetchone()
        if owner is None:
            raise LookupError("conversation not found")
        params: list = [conversation_id]
        cursor_sql = ""
        if cursor is not None:
            cursor_sql = " AND id<?"
            params.append(cursor)
        params.append(limit + 1)
        turns = self.l7.execute(
            "SELECT id,role,text,created_at_utc FROM qa_turns WHERE conversation_id=?"
            + cursor_sql + " ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        has_more = len(turns) > limit
        page = turns[:limit]
        next_cursor = page[-1]["id"] if has_more else None
        page = list(reversed(page))
        return {
            "conversation_id": conversation_id,
            "turns": [dict(t) for t in page],
            "next_cursor": next_cursor,
        }

    # -- answering ----------------------------------------------------------
    def ask(self, user_id: str, question: str, conversation_id: int | None = None,
            today_override: str | None = None) -> dict:
        question = (question or "").strip()
        if not question:
            raise ValueError("empty question")

        if conversation_id is None:
            conversation_id = self.open_or_roll_conversation(user_id)["conversation_id"]
        else:
            conversation = self.l7.execute(
                "SELECT status FROM conversations WHERE id=? AND user_id=?",
                (conversation_id, user_id),
            ).fetchone()
            if conversation is None or conversation["status"] != "OPEN":
                raise ValueError("conversation is not open for this user")

        user_turn = self.l7.execute(
            "INSERT INTO qa_turns (conversation_id,user_id,role,text,created_at_utc)"
            " VALUES (?,?,?,?,?)",
            (conversation_id, user_id, "USER", question, utc_now()),
        )
        self.l7.commit()

        answer = self._answer(user_id, question, conversation_id)
        audit = answer.pop("_audit")

        assistant_turn = self.l7.execute(
            "INSERT INTO qa_turns (conversation_id,user_id,role,text,l6_qa_session_id,"
            "evidence_ref_json,created_at_utc) VALUES (?,?,?,?,?,?,?)",
            (conversation_id, user_id, "ASSISTANT", answer["direct_answer"],
             answer.get("l6_qa_session_id"),
             json.dumps(answer["evidence_ref"], ensure_ascii=False), utc_now()),
        )
        self.l7.execute(
            "INSERT INTO qna_audits (user_id,conversation_id,user_turn_id,assistant_turn_id,"
            "semantic_classifier_model,semantic_classification_json,reasoning_model,"
            "reasoning_called,medical_review_required,medical_model,medical_review_state,"
            "finalization_path,stage_events_json,context_write_state,created_at_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id, conversation_id, user_turn.lastrowid, assistant_turn.lastrowid,
                audit["semantic_classifier_model"],
                json.dumps(audit.get("semantic_classification"), ensure_ascii=False, sort_keys=True)
                if audit.get("semantic_classification") is not None else None,
                audit.get("reasoning_model"), 1 if audit.get("reasoning_called") else 0,
                1 if audit.get("medical_review_required") else 0,
                audit.get("medical_model"), audit["medical_review_state"],
                audit["finalization_path"],
                json.dumps(audit["stage_events"], ensure_ascii=False),
                audit.get("context_write_state"), utc_now(),
            ),
        )
        self.l7.commit()
        answer["conversation_id"] = conversation_id
        return answer

    def _conversation_semantics(self, conversation_id: int, limit: int = 6) -> list[dict]:
        rows = self.l7.execute(
            "SELECT role, text FROM qa_turns WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit + 1),
        ).fetchall()
        # The current question was stored immediately before classification. It is supplied
        # separately, so exclude that newest row and restore chronological order.
        return [dict(row) for row in reversed(rows[1:])]

    @staticmethod
    def _audited(answer: dict, audit: dict, finalization_path: str) -> dict:
        audit["finalization_path"] = finalization_path
        audit["medical_review_state"] = answer["medical_review_state"]
        if not audit["stage_events"] or audit["stage_events"][-1] != "FINALIZER":
            audit["stage_events"].append("FINALIZER")
        answer["_audit"] = audit
        return answer

    def _answer(self, user_id: str, question: str, conversation_id: int) -> dict:
        audit = {
            "semantic_classifier_model": "deterministic-fast-v1",
            "semantic_classification": None,
            "reasoning_model": None,
            "reasoning_called": False,
            "medical_review_required": False,
            "medical_model": None,
            "medical_review_state": "BYPASSED",
            "stage_events": ["SEMANTIC"],
            "context_write_state": None,
        }
        classification = deterministic_fast_classification(question)
        if classification is None:
            audit["semantic_classifier_model"] = self.reasoning_adapter.model_id
            try:
                with measure_stage("deepseek_semantic"):
                    classification = validate_semantic_classification(
                        self.reasoning_adapter.classify_question(
                            question, self._conversation_semantics(conversation_id),
                        )
                    )
            except Exception:
                return self._audited({
                    "answer_first": True,
                    "direct_answer": SEMANTIC_UNAVAILABLE_TEXT,
                    "reason": None,
                    "actions": [],
                    "evidence_ref": {"grounded": False},
                    "scope": "UNAVAILABLE",
                    "medical_review_state": "BYPASSED",
                    "l6_qa_session_id": None,
                }, audit, "SEMANTIC_UNAVAILABLE")
        else:
            classification = validate_semantic_classification(classification)

        audit["semantic_classification"] = classification

        if classification["potential_context"]:
            audit["stage_events"].append("CONTEXT_WRITE")
            if classification["context_write"] == "AUTO_SAVE" and self._context_writer is not None:
                try:
                    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:20]
                    context_capture = self._context_writer.enqueue_ingest(
                        user_id,
                        question,
                        today=datetime.now(ZoneInfo(self.cfg.timezone_name)).date().isoformat(),
                        idempotency_key=f"qna-context:{conversation_id}:{digest}",
                        jobs=self._jobs,
                    )
                    audit["context_write_state"] = context_capture.get("status", "UNKNOWN")
                except Exception:
                    audit["context_write_state"] = "FAILED"
            elif classification["context_write"] == "CONFIRM":
                audit["context_write_state"] = "CONFIRM_REQUIRED"
            else:
                audit["context_write_state"] = "NOT_WRITTEN"

        if classification["scope"] == "HEALTH_CONTEXT":
            saved = audit["context_write_state"] in {"PENDING", "RUNNING", "COMPLETE"}
            direct = (
                "这条个人情况已保存，正在后台更新；完成后 PHE 会按正式 Context 规则使用它。"
                if saved else
                "我识别到你在补充个人情况，但目前没有自动写入；你可以在“补充情况”中确认。"
            )
            return self._audited({
                "answer_first": True,
                "direct_answer": direct,
                "reason": None,
                "actions": [],
                "evidence_ref": {"grounded": False, "context_capture": bool(saved)},
                "scope": "HEALTH_CONTEXT",
                "medical_review_state": "BYPASSED",
                "l6_qa_session_id": None,
            }, audit, "CONTEXT_QUEUED" if saved else "CONTEXT_NOT_WRITTEN")

        if classification["scope"] == "PRODUCT_META":
            return self._audited({
                "answer_first": True,
                "direct_answer": PRODUCT_META_TEXT,
                "reason": None,
                "actions": [],
                "evidence_ref": {"grounded": False},
                "scope": "PRODUCT_META",
                "medical_review_state": "BYPASSED",
                "l6_qa_session_id": None,
            }, audit, "PRODUCT_META_FIXED")

        if classification["scope"] == "OUT_OF_SCOPE":
            return self._audited({
                "answer_first": True,
                "direct_answer": REFUSAL_TEXT,
                "reason": None,
                "actions": [],
                "evidence_ref": {"grounded": False},
                "scope": "OUT_OF_SCOPE",
                "medical_review_state": "BYPASSED",
                "l6_qa_session_id": None,
            }, audit, "OUT_OF_SCOPE_FIXED")

        if classification["scope"] == "HEALTH_DATA":
            audit["stage_events"].append("ENGINE_DATA")
            return self._audited(
                self._health_data_answer(classification), audit, "HEALTH_DATA_ENGINE",
            )

        audit["stage_events"].append("EVIDENCE")
        core = self.bridge.core
        l3 = open_readonly(self.cfg.l3_db, immutable_if_checkpointed=True)
        l4 = open_readonly(self.cfg.l4_db, immutable_if_checkpointed=True)
        l5 = open_readonly(self.cfg.l5_db, immutable_if_checkpointed=True)
        l6 = open_readonly(self.cfg.l6_db)
        try:
            analysis_date = readers.latest_analysis_date(l5)
            if analysis_date is None:
                return self._audited(
                    self._insufficient_answer(None, "尚无任何健康数据。"),
                    audit,
                    "INSUFFICIENT_EVIDENCE",
                )
            recent_context = readers.read_recent_context(l6, analysis_date)
            recent_feedback = [dict(r) for r in l6.execute(
                "SELECT subject_type,subject_id,feedback_status FROM user_feedback"
                " ORDER BY id DESC LIMIT 20")]
            with measure_stage("bundle_assembly"):
                full_bundle, _prov = self.bridge.assemble_evidence(
                    l3, l4, l5, analysis_date, recent_context, recent_feedback, [])
            full_bundle["overall_state"] = core.overall_state(full_bundle)
            patterns = readers.read_patterns(l6)
            today_row = self.l7.execute(
                "SELECT rendered_json FROM today_versions WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            today_snapshot = json.loads(today_row[0]) if today_row else None
            bundle = select_question_evidence(
                full_bundle, classification, patterns, today_snapshot,
            )
            candidates = core.generate_candidates(bundle)

            if bundle["overall_state"] == "INSUFFICIENT_EVIDENCE" or not candidates:
                missing = "；".join(bundle.get("missing_evidence", [])[:3]) or "个人基线数据不足"
                return self._audited(
                    self._insufficient_answer(analysis_date, missing),
                    audit,
                    "INSUFFICIENT_EVIDENCE",
                )

            hypothesis_types = [c["hypothesis_type"] for c in candidates]
            sealed_review_state, sealed_reasons = core.medical_trigger(
                question, bundle, hypothesis_types,
            )
            daily = readers.read_current_daily_reasoning(l6, analysis_date)
            today_medical_state = daily.get("medical_review_state") if daily else None
        finally:
            for c in (l3, l4, l5, l6):
                c.close()

        medical_model = None
        medical_result = None
        finalization_path = "REASONING_UNAVAILABLE"
        audit["stage_events"].append("REASONING")
        audit["reasoning_model"] = self.reasoning_adapter.model_id
        audit["reasoning_called"] = True
        try:
            candidate_method = getattr(self.reasoning_adapter, "answer_question_candidate", None)
            if candidate_method is None:
                candidate_method = self.reasoning_adapter.answer_question
            with measure_stage("deepseek_reasoning"):
                result = candidate_method(question, bundle, candidates)
        except Exception:
            result = None

        if result is None:
            direct = "目前不能可靠判断：推理模型暂不可用，请稍后再试。"
            reason = None
            actions: list[str] = []
            candidate = None
            candidate_issues = ["reasoning_unavailable"]
            review_state = (
                "UNAVAILABLE"
                if classification["medical_consequence"] in {"MODERATE", "HIGH"}
                else "BYPASSED"
            )
            audit["medical_review_required"] = review_state == "UNAVAILABLE"
        else:
            try:
                candidate, candidate_issues = validate_candidate(result, bundle)
            except Exception:
                candidate = None
                candidate_issues = ["invalid_candidate_schema"]

            if candidate is None:
                direct = "当前不能基于现有证据可靠给出这个结论。"
                reason = "候选回答未通过结构校验。"
                actions = []
                review_state = "UNAVAILABLE"
                finalization_path = "INVALID_CANDIDATE_SCHEMA"
            else:
                review_required, reasons = medical_consequence_gate(
                    classification,
                    sealed_review_state,
                    sealed_reasons,
                    today_medical_state,
                    candidate,
                    candidate_issues,
                    has_medical_safety_context=any(
                        item.get("context_type") in MEDICAL_SAFETY_CONTEXT_TYPES
                        for item in bundle.get("recent_context", [])
                    ),
                )
                if review_required:
                    medical_model = self.medical_adapter.model_id
                    audit["medical_review_required"] = True
                    audit["medical_model"] = medical_model
                    audit["stage_events"].append("MEDICAL")
                    review_bundle = build_medical_review_bundle(
                        bundle, candidate, candidate_issues,
                        today_medical_state=today_medical_state,
                    )
                    try:
                        adapter = self.medical_adapter
                        verify_identity = getattr(adapter, "verify_model_identity", None)
                        if callable(verify_identity) and getattr(adapter, "_identity", None) is None:
                            verify_identity()
                        identity = getattr(adapter, "_identity", None) or {}
                        model_artifact_hash = core.sha256_text(core.canonical_json({
                            "model_id": adapter.model_id,
                            "model": getattr(adapter, "model", None),
                            "digest": identity.get("digest"),
                            "adapter": f"{type(adapter).__module__}.{type(adapter).__qualname__}",
                        }))
                        evidence_hash = core.sha256_text(core.canonical_json({
                            "resolved_evidence": review_bundle["resolved_evidence"],
                            "safety_context": review_bundle["safety_context"],
                            "missing_evidence": review_bundle["missing_evidence"],
                        }))
                        cache_key = medical_review_cache_key(
                            question_representation=question.strip(),
                            classification=classification,
                            candidate=candidate,
                            resolved_evidence_hash=evidence_hash,
                            medical_state={
                                "today": today_medical_state,
                                "sealed_review_state": sealed_review_state,
                                "sealed_reasons": sealed_reasons,
                            },
                            model_artifact_hash=model_artifact_hash,
                            critic_prompt_version=MEDICAL_CRITIC_PROMPT_VERSION,
                            schema_version=MEDICAL_REVIEW_SCHEMA_VERSION,
                        )

                        def perform_review():
                            with measure_stage("medgemma_total"):
                                raw = adapter.review(
                                    review_bundle, hypothesis_types, question,
                                )
                            record_model_meta(
                                "medgemma", getattr(adapter, "last_meta", None),
                            )
                            if "review_status" not in raw and "findings" in raw:
                                raw = {
                                "review_status": "ESCALATE" if raw.get("escalation") else "APPROVED",
                                "medical_concerns": list(raw.get("findings", [])),
                                "causality_concerns": [],
                                "missing_safety_considerations": [],
                                "unsafe_actions": [],
                                "required_changes": [],
                                "escalation_reason": (
                                    "mock reviewer escalation" if raw.get("escalation") else None
                                ),
                                "review_summary": "mock medical review",
                                }
                            return raw

                        medical_result, cache_outcome = self._medical_cache.get_or_review(
                            cache_key, perform_review,
                            model_artifact_hash=model_artifact_hash,
                        )
                        record_cache_result(hit=cache_outcome in {"HIT", "COALESCED"})
                    except Exception:
                        medical_result = validate_medical_review({
                            "review_status": "UNAVAILABLE",
                            "medical_concerns": [],
                            "causality_concerns": [],
                            "missing_safety_considerations": [],
                            "unsafe_actions": [],
                            "required_changes": [],
                            "escalation_reason": None,
                            "review_summary": "medical review unavailable",
                        })

                    status = medical_result["review_status"]
                    review_state = "UNAVAILABLE" if status == "UNAVAILABLE" else "PERFORMED"
                    if status == "APPROVED" and not candidate_issues:
                        final_candidate = candidate
                        finalization_path = "APPROVED"
                    elif status == "APPROVED_WITH_CHANGES" and not candidate_issues:
                        # The revision is a fresh model sample per attempt; one imperfect
                        # sample must not fail the whole answer closed (same reasoning as
                        # the degraded-Today recovery retries).
                        audit["stage_events"].append("REVISION")
                        final_candidate = None
                        for _ in range(3):
                            try:
                                revised = self.reasoning_adapter.revise_question_candidate(
                                    question, bundle, candidate,
                                    medical_result["required_changes"],
                                )
                                final_candidate, revision_issues = validate_candidate(
                                    revised, bundle,
                                )
                                if not revision_issues:
                                    break
                                final_candidate = None
                            except Exception:
                                final_candidate = None
                        if final_candidate is not None:
                            finalization_path = "APPROVED_WITH_CHANGES"
                        else:
                            finalization_path = "APPROVED_WITH_CHANGES_FAILED_CLOSED"
                    else:
                        final_candidate = None
                        finalization_path = status if not candidate_issues else "DETERMINISTIC_REJECTED"

                    if final_candidate is not None:
                        direct = final_candidate["direct_answer"]
                        reason = final_candidate["reason"] if final_candidate["reason"] != direct else None
                        actions = final_candidate["recommended_actions"]
                    elif status == "ESCALATE" and not candidate_issues:
                        direct = "现有信息涉及需要及时处理的健康安全风险，请停止相关活动并尽快寻求专业医疗帮助。"
                        reason = None
                        actions = []
                    elif status == "UNAVAILABLE":
                        direct = "这个问题需要安全审查，但当前无法完成，因此我不能把未经审查的建议发给你。请稍后再试。"
                        reason = None
                        actions = []
                    else:
                        direct = "当前不能基于现有证据可靠给出这个结论。"
                        reason = "候选回答未通过医学安全或证据校验。"
                        actions = []
                else:
                    review_state = "BYPASSED"
                    reasons = []
                    direct = candidate["direct_answer"]
                    reason = candidate["reason"] if candidate["reason"] != direct else None
                    actions = candidate["recommended_actions"]
                    finalization_path = "MEDICAL_BYPASSED"

        if candidate is None:
            reasons = sorted(set(sealed_reasons + candidate_issues))

        # Persist through the sealed qa_sessions / medical_reviews semantics.
        question_bundle = {
            "question": question,
            "analysis_date": analysis_date,
            "overall_state": bundle["overall_state"],
            "personal_evidence_bundle": bundle,
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
                 core.canonical_json(medical_result or {"no_diagnosis": True}), medical_model, now),
            )
            l6w.commit()
        except Exception:
            l6w.rollback()
            raise
        finally:
            l6w.close()

        return self._audited({
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
            "scope": classification["scope"],
            "medical_review_state": review_state,
            "l6_qa_session_id": qa_id,
        }, audit, finalization_path)

    def _health_data_answer(self, classification: dict) -> dict:
        l3 = open_readonly(self.cfg.l3_db, immutable_if_checkpointed=True)
        l5 = open_readonly(self.cfg.l5_db, immutable_if_checkpointed=True)
        try:
            analysis_date = readers.latest_analysis_date(l5)
            metric = next(iter(classification["relevant_metrics"]), None)
            result = (
                readers.deterministic_health_data_query(
                    l3,
                    metric,
                    classification.get("time_range"),
                    classification.get("aggregation"),
                    analysis_date,
                )
                if analysis_date and metric else None
            )
        finally:
            l3.close()
            l5.close()

        if result is None:
            return {
                "answer_first": True,
                "direct_answer": "目前没有找到能可靠回答这个问题的个人数据。",
                "reason": "PHE 引擎中缺少对应的确定性指标。",
                "actions": [],
                "evidence_ref": {"grounded": True, "data_authority": "L3", "insufficient": True},
                "scope": "HEALTH_DATA",
                "medical_review_state": "BYPASSED",
                "l6_qa_session_id": None,
            }

        return {
            "answer_first": True,
            "direct_answer": result["direct_answer"],
            "reason": None,
            "actions": [],
            "evidence_ref": {
                "grounded": True,
                "data_authority": "L3",
                "analysis_date": analysis_date,
                "data_date": result["data_date"],
                "feature_name": result["feature_name"],
                "aggregation": result["aggregation"],
                "source_count": result["source_count"],
                "values": result["values"],
            },
            "scope": "HEALTH_DATA",
            "medical_review_state": "BYPASSED",
            "l6_qa_session_id": None,
        }

    def _insufficient_answer(self, analysis_date: str | None, missing: str) -> dict:
        return {
            "answer_first": True,
            "direct_answer": "目前不能可靠判断。",
            "reason": f"缺少的信息：{missing}",
            "actions": [],
            "evidence_ref": {"grounded": True, "analysis_date": analysis_date,
                             "insufficient": True},
            "scope": "HEALTH_DECISION",
            "medical_review_state": "BYPASSED",
            "l6_qa_session_id": None,
        }
