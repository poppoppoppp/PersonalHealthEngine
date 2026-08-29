"""EngineOrchestrator — the only component that may trigger L6 reasoning runs.

Implements the three-threshold discipline (contract §9):

1. Recompute Threshold  — upstream signature unchanged -> do nothing (no bundle assembly,
   no model call). Signature changed -> assemble the deterministic Evidence Bundle; call a
   reasoning model only when the bundle hash differs from the materialized CURRENT bundle
   (or no materialization exists yet for the analysis date).
2. UI Change Threshold  — the presented-judgment signature decides whether a new
   today_version with fresh wording is created; otherwise the previous rendered copy is
   kept verbatim (semantic stability, contract §10).
3. Notification Threshold — handled by NotificationService (Phase G); the orchestrator
   only records the state-delta input it will consume.

All L6 writes go through the sealed `reconcile_daily()` semantics (hash-idempotent,
STALE-then-insert append-only versioning). Upstream databases are opened read-only.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from l7.config import Config, DEFINITION_FILES
from l7.engine import model_cache
from l7.store.db import open_readonly, utc_now
from l7.upstream import readers
from l7.upstream.l6_bridge import L6Bridge, _is_chinese_product_text
from l7.upstream.l6_bridge import ProductDeepSeekReasoningAdapter

CONFIDENCE_ORDER = ("VERY_LOW", "LOW", "MODERATE", "HIGH")
DEGRADED_REASONING_SUMMARY = "（推理模型暂不可用，仅保留结构化证据。）"


@dataclass
class EvaluationResult:
    outcome: str                      # NO_UPSTREAM_CHANGE | BUNDLE_UNCHANGED | REMATERIALIZED | FALLBACK_NO_DATA
    model_calls: int = 0
    judgment_updated: bool = False
    today_payload: dict = field(default_factory=dict)
    eval_run_id: int | None = None
    today_version_id: int | None = None


class EngineOrchestrator:
    def __init__(
        self,
        config: Config,
        l7: sqlite3.Connection,
        bridge: L6Bridge | None = None,
        reasoning_adapter=None,
        medical_adapter=None,
    ):
        self.cfg = config
        self.l7 = l7
        self.bridge = bridge or L6Bridge(config.l6_code_dir)
        self._reasoning_adapter = reasoning_adapter
        self._medical_adapter = medical_adapter
        # Fired after evaluate() when a NEW judgment version was written (judgment_updated).
        # UI re-renderings and unchanged bundles never fire these — that is the Notification
        # Threshold separation. Listeners get (user_id, EvaluationResult).
        self.judgment_listeners: list = []

    # -- adapter resolution -------------------------------------------------
    @property
    def reasoning_adapter(self):
        if self._reasoning_adapter is None:
            if self.cfg.reasoning_adapter == "deepseek":
                self._reasoning_adapter = ProductDeepSeekReasoningAdapter()
            else:
                self._reasoning_adapter = self.bridge.adapters.MockReasoningModelAdapter()
        return self._reasoning_adapter

    @property
    def medical_adapter(self):
        if self._medical_adapter is None:
            if self.cfg.medical_adapter == "medgemma":
                self._medical_adapter = self.bridge.real_adapters.RealMedGemmaMedicalModelAdapter()
            else:
                self._medical_adapter = self.bridge.adapters.MockMedicalModelAdapter()
        return self._medical_adapter

    # -- public API ---------------------------------------------------------
    def evaluate(self, user_id: str, trigger: str) -> EvaluationResult:
        core = self.bridge.core
        started = utc_now()
        local_date = datetime.now(ZoneInfo(self.cfg.timezone_name)).date().isoformat()

        l3 = open_readonly(self.cfg.l3_db, immutable_if_checkpointed=True)
        l4 = open_readonly(self.cfg.l4_db, immutable_if_checkpointed=True)
        l5 = open_readonly(self.cfg.l5_db, immutable_if_checkpointed=True)
        l6 = open_readonly(self.cfg.l6_db)
        try:
            analysis_date = readers.latest_analysis_date(l5)
            if analysis_date is None:
                return self._record_no_data(user_id, trigger, started, local_date)

            sig = readers.upstream_signature(l3, l4, l5, l6, local_date)
            last = self.l7.execute(
                "SELECT upstream_sig_json FROM eval_runs WHERE user_id=? AND outcome != 'ERROR' "
                "ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if last is not None and json.loads(last["upstream_sig_json"]) == sig:
                payload, presentation_calls, version_id = self._render_current_today(
                    user_id, analysis_date, trigger, l6, l3, l4, l5,
                )
                run_id = self._insert_eval_run(
                    user_id, started, trigger, sig, None, "NO_UPSTREAM_CHANGE",
                    presentation_calls, version_id,
                )
                return EvaluationResult(
                    "NO_UPSTREAM_CHANGE", presentation_calls, False, payload, run_id, version_id,
                )

            # --- Recompute threshold passed: assemble deterministic bundle ---
            recent_context = readers.read_recent_context(l6, analysis_date)
            cases = readers.similar_cases(l6, recent_context, analysis_date)
            bundle, provenance = self.bridge.assemble_evidence(
                l3, l4, l5, analysis_date, recent_context, [], cases
            )
            bundle["overall_state"] = core.overall_state(bundle)
            candidates = core.generate_candidates(bundle)
            for cand in candidates:
                support = len(cand.get("supporting", []))
                cand["confidence"] = core.base_confidence(
                    support, 0, bundle["overall_state"] == "INSUFFICIENT_EVIDENCE", bool(recent_context)
                )
                cand["origin"] = "CANDIDATE"
            bhash = self.bridge.bundle_sha256(bundle)

            stored = readers.read_current_bundle(l6, analysis_date)
            need_model = stored is None or stored["bundle_sha256"] != bhash

            model_calls = 0
            if need_model:
                model_calls = self._run_reasoning_and_materialize(
                    bundle, bhash, candidates, provenance, analysis_date, sig
                )
                outcome = "REMATERIALIZED"
            else:
                outcome = "BUNDLE_UNCHANGED"

            payload, judgment_updated, version_id, presentation_calls = self._materialize_today_version(
                user_id, analysis_date, trigger, bundle, bhash, l3, l4, l5,
            )
            model_calls += presentation_calls
            run_id = self._insert_eval_run(
                user_id, started, trigger, sig, bhash, outcome, model_calls, version_id
            )
            self._log_change(user_id, run_id, outcome, bundle, analysis_date)
            result = EvaluationResult(outcome, model_calls, judgment_updated, payload, run_id, version_id)
            if judgment_updated:
                for listener in self.judgment_listeners:
                    listener(user_id, result)
            return result
        finally:
            for c in (l3, l4, l5, l6):
                c.close()

    # -- internals ------------------------------------------------------------
    def _salvage_action_list(self, actions):
        """Keep only contract-conforming Chinese actions instead of discarding the whole
        sample when one action drifts (e.g. contains a Latin token like SpO2)."""
        if not isinstance(actions, list):
            return []
        return [
            action for action in actions
            if isinstance(action, str) and _is_chinese_product_text(action)
        ][:3]

    def _attempt_reasoning_sample(self, bundle, ranked):
        """One model attempt: strict product contract first; on its (brittle) hard
        failure, salvage the raw sample field by field. Returns output or None."""
        try:
            return self.reasoning_adapter.reason_daily(bundle, ranked)
        except Exception:
            pass
        lenient = getattr(self.reasoning_adapter, "reason_daily_lenient", None)
        if lenient is None:
            return None
        try:
            output = lenient(bundle, ranked)
        except Exception:
            return None
        if isinstance(output, dict):
            output["recommended_actions"] = self._salvage_action_list(
                output.get("recommended_actions"),
            )
        return output

    def _record_no_data(self, user_id, trigger, started, local_date):
        sig = {"local_date": local_date, "note": "no upstream deviation analytics present"}
        run_id = self._insert_eval_run(user_id, started, trigger, sig, None, "FALLBACK_NO_DATA", 0, None)
        payload = {
            "schema": "l7.today/v1",
            "presentation_contract_version": 3,
            "product_state": "D",
            "product_state_label": "目前无法可靠判断",
            "headline": "目前还没有足够的健康数据可以分析。",
            "information_order": ["conclusion", "cause", "action"],
            "cause": {"hypothesis_type": "UNKNOWN", "hypothesis_label": "暂无法确定原因",
                      "text": "等待首批数据同步后开始建立你的个人基线。", "secondary": None},
            "actions": [],
            "confidence": "VERY_LOW",
            "confidence_label": "很低",
            "medical_attention": False,
            "analysis_date": None,
            "data_as_of": None,
            "updated_at_utc": started,
            "updated_at_local_hhmm": self._hhmm(started),
            "judgment_updated": False,
            "change_note": None,
            "evidence_level2": [],
            "evidence": [],
            "feedback_prompt": None,
            "version_id": None,
        }
        return EvaluationResult("FALLBACK_NO_DATA", 0, False, payload, run_id, None)

    def _run_reasoning_and_materialize(
        self, bundle, bhash, candidates, provenance, analysis_date, upstream_sig
    ):
        """Mirror the sealed materializer flow with the configured adapters. Returns #model calls."""
        core = self.bridge.core
        mat = self.bridge.materializer
        model_calls = 0

        ranked = candidates[:]
        primary = ranked[0] if ranked else {"hypothesis_type": "UNKNOWN", "confidence": "VERY_LOW"}
        secondary = ranked[1] if len(ranked) > 1 else None

        request_payload = {
            "bundle": bundle,
            "candidates": [c["hypothesis_type"] for c in ranked],
            "adapter_contract_version": getattr(
                self.reasoning_adapter, "contract_version", "l6-v0.1",
            ),
        }
        request_hash = core.sha256_text(core.canonical_json(request_payload))

        invocations = []
        model_output = model_cache.lookup(self.l7, request_hash, "REASONING")
        if model_output is None:
            for _ in range(3):
                sample = self._attempt_reasoning_sample(bundle, ranked)
                model_calls += 1
                if sample is not None:
                    model_output = sample
                    break
            if model_output is not None:
                model_cache.store(self.l7, request_hash, "REASONING", self.reasoning_adapter.model_id, model_output)
        if model_output is not None:
            ok, errors = core.validate_daily_output(
                model_output, [c["hypothesis_type"] for c in ranked] + ["UNKNOWN"], primary["confidence"]
            )
            if not ok:
                model_output = None
                invocations.append({
                    "adapter_kind": "REASONING", "model_id": self.reasoning_adapter.model_id,
                    "request_sha256": request_hash, "response_sha256": None,
                    "status": "INVALID", "error_code": "INVALID_OUTPUT",
                })
            else:
                invocations.append({
                    "adapter_kind": "REASONING", "model_id": self.reasoning_adapter.model_id,
                    "request_sha256": request_hash,
                    "response_sha256": core.sha256_text(core.canonical_json(model_output)),
                    "status": "PASS",
                })
        else:
            invocations.append({
                "adapter_kind": "REASONING", "model_id": self.reasoning_adapter.model_id,
                "request_sha256": request_hash, "response_sha256": None,
                "status": "UNAVAILABLE", "error_code": "REASONING_UNAVAILABLE",
            })

        if model_output is None:
            primary_type = primary["hypothesis_type"]
            secondary_type = secondary["hypothesis_type"] if secondary else None
            confidence = primary["confidence"]
            summary = "（推理模型暂不可用，仅保留结构化证据。）"
            actions: list[str] = []
        else:
            primary_type = model_output["primary_hypothesis_type"]
            secondary_type = model_output["secondary_hypothesis_type"]
            model_conf = model_output.get("confidence")
            if CONFIDENCE_ORDER.index(model_conf) <= CONFIDENCE_ORDER.index(primary["confidence"]):
                confidence = model_conf
            else:
                confidence = primary["confidence"]
            summary = model_output["reasoning_summary"]
            actions = model_output["recommended_actions"]

        review_state, reasons = core.medical_trigger(
            None, bundle, [primary_type, secondary_type] if secondary_type else [primary_type]
        )
        medical_model_id = None
        medical_findings = None
        if review_state == "REQUIRED":
            medical_model_id = self.medical_adapter.model_id
            med_request_hash = core.sha256_text(core.canonical_json(
                {"review_bundle": bundle, "hypothesis_types":
                 [primary_type, secondary_type] if secondary_type else [primary_type]}
            ))
            findings = model_cache.lookup(self.l7, med_request_hash, "MEDICAL")
            if findings is None:
                try:
                    findings = self.medical_adapter.review(
                        bundle, [primary_type, secondary_type] if secondary_type else [primary_type]
                    )
                    model_calls += 1
                    model_cache.store(self.l7, med_request_hash, "MEDICAL", medical_model_id, findings)
                except Exception:
                    findings = None
            if findings is not None:
                review_state = "PERFORMED"
                medical_findings = findings
                invocations.append({
                    "adapter_kind": "MEDICAL", "model_id": medical_model_id,
                    "request_sha256": med_request_hash,
                    "response_sha256": core.sha256_text(core.canonical_json(findings)),
                    "status": "PASS",
                })
            else:
                review_state = "UNAVAILABLE"
                invocations.append({
                    "adapter_kind": "MEDICAL", "model_id": medical_model_id,
                    "request_sha256": med_request_hash, "response_sha256": None,
                    "status": "UNAVAILABLE", "error_code": "MEDICAL_REVIEW_UNAVAILABLE",
                })

        daily = {
            "overall_state": bundle["overall_state"],
            "primary_hypothesis_type": primary_type,
            "secondary_hypothesis_type": secondary_type,
            "confidence": confidence,
            "recommended_actions": actions,
            "medical_review_state": review_state,
            "reasoning_model": self.reasoning_adapter.model_id,
            "medical_model": medical_model_id,
            "reasoning_summary": summary,
        }

        for cand in ranked:
            cand["counter"] = []
            cand["missing"] = list(bundle["missing_evidence"])
            cand["reasoning_summary"] = summary if cand is primary else None

        # --- materialize through the sealed write path (append-only versions) ---
        l6w = sqlite3.connect(self.cfg.l6_db)
        l6w.row_factory = sqlite3.Row
        l6w.execute("PRAGMA foreign_keys = ON")
        try:
            l6w.execute("BEGIN IMMEDIATE")
            now = utc_now()
            for key, (rel, expected_id) in DEFINITION_FILES.items():
                payload_def, checksum = self.bridge.load_definition(
                    self.cfg.definition_path(key), expected_id
                )
                mat.register_definition(
                    l6w, payload_def, checksum,
                    {"context": "CONTEXT_EXTRACTION", "evidence": "EVIDENCE_ASSEMBLY",
                     "hypothesis": "HYPOTHESIS", "confidence": "CONFIDENCE",
                     "daily": "DAILY_REASONING", "medical": "MEDICAL_REVIEW",
                     "pattern": "PERSONAL_PATTERN"}[key],
                    f"L6 {key} definition",
                )
            if review_state == "PERFORMED":
                l6w.execute(
                    "INSERT INTO medical_reviews (subject_type,subject_id,review_state,trigger_reason,"
                    "findings_json,reviewer_model,created_at_utc) VALUES ('DAILY_REASONING',?,?,?,?,?,?)",
                    (-1, review_state, core.canonical_json(reasons),
                     core.canonical_json(medical_findings), medical_model_id, now),
                )
            result = mat.reconcile_daily(
                l6w, analysis_date, bundle, bhash, ranked, daily, provenance, invocations, now
            )
            run_id = (
                "l6-incremental-"
                + datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
                + "-"
                + uuid.uuid4().hex[:8]
            )
            finished = utc_now()
            details = dict(result)
            details.update(
                {
                    "analysis_date": analysis_date,
                    "bundle_sha256": bhash,
                    "model_calls": model_calls,
                }
            )
            l6w.execute(
                "INSERT INTO pipeline_runs "
                "(run_id,mode,status,source_l3_path,source_l4_path,source_l5_path,"
                "started_at_utc,finished_at_utc,details_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    "INCREMENTAL",
                    "PASS",
                    str(Path(self.cfg.l3_db).resolve()),
                    str(Path(self.cfg.l4_db).resolve()),
                    str(Path(self.cfg.l5_db).resolve()),
                    now,
                    finished,
                    core.canonical_json(details),
                ),
            )
            l6w.execute(
                "INSERT INTO processing_checkpoints "
                "(pipeline_name,last_l5_analytic_id,last_l3_feature_id,last_l4_baseline_id,"
                "last_successful_run_id,updated_at_utc) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(pipeline_name) DO UPDATE SET "
                "last_l5_analytic_id=excluded.last_l5_analytic_id,"
                "last_l3_feature_id=excluded.last_l3_feature_id,"
                "last_l4_baseline_id=excluded.last_l4_baseline_id,"
                "last_successful_run_id=excluded.last_successful_run_id,"
                "updated_at_utc=excluded.updated_at_utc",
                (
                    mat.PIPELINE,
                    upstream_sig["l5_max_deviation_id"],
                    upstream_sig["l3_max_feature_id"],
                    upstream_sig["l4_max_baseline_id"],
                    run_id,
                    finished,
                ),
            )
            l6w.commit()
        except Exception:
            l6w.rollback()
            raise
        finally:
            l6w.close()
        self.l7.commit()  # persist model_call_cache entries
        self._last_reconcile_result = result
        return model_calls

    def _materialize_today_version(
        self, user_id, analysis_date, trigger, bundle, bhash, l3, l4, l5,
    ):
        from l7.rendering.renderer import judgment_signature, map_product_state

        l6 = open_readonly(self.cfg.l6_db)
        try:
            dr = readers.read_current_daily_reasoning(l6, analysis_date)
            if dr is None:
                raise RuntimeError("no CURRENT daily_reasoning after materialization")
            symptom_active = readers.symptom_context_active(l6, analysis_date)
            stored = readers.read_current_bundle(l6, analysis_date)
            facts = self._exact_evidence(l6, l5, l4, l3, stored, analysis_date)
        finally:
            l6.close()

        product_state = map_product_state(dr, symptom_active)
        sig_sha = judgment_signature(dr, product_state)
        latest = self.l7.execute(
            "SELECT * FROM today_versions WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
        same_judgment = (
            latest is not None
            and latest["signature_sha256"] == sig_sha
            and latest["analysis_date"] == analysis_date
        )
        if same_judgment:
            prior = json.loads(latest["rendered_json"])
            if prior.get("presentation_contract_version", 0) >= 3:
                if trigger == "manual_refresh" and self._is_degraded_projection(prior):
                    recovered_dr, recovery_calls = self._retry_degraded_reasoning(dr, bundle)
                    if recovered_dr is not None:
                        rendered, render_calls, complete = self._render_product_copy(
                            recovered_dr, bundle, product_state, analysis_date, facts, None,
                            False, "推理说明已恢复，健康判断未改变。",
                        )
                        if complete:
                            version_id = self._insert_today_version(
                                user_id, analysis_date, dr["id"], bhash, product_state,
                                sig_sha, rendered, False, trigger,
                            )
                            return (
                                self._touch_rendered(rendered, version_id), False,
                                version_id, recovery_calls + render_calls,
                            )
                    return (
                        self._touch_rendered(prior, latest["id"]), False,
                        latest["id"], recovery_calls,
                    )
                return self._touch_rendered(prior, latest["id"]), False, latest["id"], 0
            rendered, calls, complete = self._render_product_copy(
                dr, bundle, product_state, analysis_date, facts, prior, False,
                "产品展示已恢复为中文与精确证据，健康判断未改变。",
            )
            if not complete:
                return self._touch_rendered(rendered, latest["id"]), False, latest["id"], calls
            version_id = self._insert_today_version(
                user_id, analysis_date, dr["id"], bhash, product_state, sig_sha,
                rendered, False, trigger,
            )
            return self._touch_rendered(rendered, version_id), False, version_id, calls

        judgment_updated = latest is not None
        rendered, calls, complete = self._render_product_copy(
            dr, bundle, product_state, analysis_date, facts, None, judgment_updated,
            self._change_note(latest, dr) if latest is not None else "首次生成 Today 判断。",
        )
        if not complete:
            return self._touch_rendered(rendered, None), False, None, calls
        version_id = self._insert_today_version(
            user_id, analysis_date, dr["id"], bhash, product_state, sig_sha,
            rendered, judgment_updated, trigger,
        )
        return (
            self._touch_rendered(rendered, version_id, judgment_updated),
            judgment_updated,
            version_id,
            calls,
        )

    def _render_current_today(self, user_id, analysis_date, trigger, l6, l3, l4, l5):
        """Serve stable semantic copy while refreshing response-only time fields."""
        from l7.rendering.renderer import judgment_signature, map_product_state

        latest = self.l7.execute(
            "SELECT * FROM today_versions WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
        prior = None
        recover_degraded = False
        if latest is not None and latest["analysis_date"] == analysis_date:
            prior = json.loads(latest["rendered_json"])
            if prior.get("presentation_contract_version", 0) >= 3:
                recover_degraded = (
                    trigger == "manual_refresh" and self._is_degraded_projection(prior)
                )
                if not recover_degraded:
                    return self._touch_rendered(prior, latest["id"]), 0, latest["id"]

        dr = readers.read_current_daily_reasoning(l6, analysis_date)
        if dr is None:
            payload = self._record_no_data(user_id, trigger, utc_now(), analysis_date).today_payload
            return payload, 0, None
        stored = readers.read_current_bundle(l6, analysis_date)
        bundle = stored["bundle"] if stored else {}
        facts = self._exact_evidence(l6, l5, l4, l3, stored, analysis_date)
        product_state = map_product_state(
            dr, readers.symptom_context_active(l6, analysis_date),
        )
        sig_sha = judgment_signature(dr, product_state)
        if recover_degraded:
            recovered_dr, recovery_calls = self._retry_degraded_reasoning(dr, bundle)
            if recovered_dr is None:
                return self._touch_rendered(prior, latest["id"]), recovery_calls, latest["id"]
            rendered, render_calls, complete = self._render_product_copy(
                recovered_dr, bundle, product_state, analysis_date, facts, None, False,
                "推理说明已恢复，健康判断未改变。",
            )
            if not complete:
                return (
                    self._touch_rendered(prior, latest["id"]),
                    recovery_calls + render_calls,
                    latest["id"],
                )
            version_id = self._insert_today_version(
                user_id, analysis_date, dr["id"], stored["bundle_sha256"] if stored else "",
                product_state, sig_sha, rendered, False, trigger,
            )
            return (
                self._touch_rendered(rendered, version_id),
                recovery_calls + render_calls,
                version_id,
            )
        presentation_repair = latest is not None and latest["analysis_date"] == analysis_date
        source = json.loads(latest["rendered_json"]) if presentation_repair else None
        change_note = (
            "产品展示已恢复为中文与精确证据，健康判断未改变。"
            if presentation_repair else None
        )
        rendered, calls, complete = self._render_product_copy(
            dr, bundle, product_state, analysis_date, facts, source, False, change_note,
        )
        if not complete:
            prior_id = latest["id"] if presentation_repair else None
            return self._touch_rendered(rendered, prior_id), calls, prior_id
        version_id = self._insert_today_version(
            user_id, analysis_date, dr["id"], stored["bundle_sha256"] if stored else "",
            product_state, sig_sha, rendered, False, trigger,
        )
        return self._touch_rendered(rendered, version_id), calls, version_id

    @staticmethod
    def _is_degraded_projection(payload: dict) -> bool:
        return str((payload.get("cause") or {}).get("text") or "").strip() == (
            DEGRADED_REASONING_SUMMARY
        )

    def _retry_degraded_reasoning(self, dr: dict, bundle: dict) -> tuple[dict | None, int]:
        """Recover display copy only; sealed L6 evidence and judgment remain unchanged."""
        from l7.upstream.l6_bridge import _is_chinese_product_text

        core = self.bridge.core
        ranked = core.generate_candidates(bundle)
        for candidate in ranked:
            support = len(candidate.get("supporting", []))
            candidate["confidence"] = core.base_confidence(
                support,
                0,
                bundle.get("overall_state") == "INSUFFICIENT_EVIDENCE",
                bool(bundle.get("recent_context")),
            )
            candidate["origin"] = "CANDIDATE"

        request_payload = {
            "bundle": bundle,
            "candidates": [candidate["hypothesis_type"] for candidate in ranked],
            "adapter_contract_version": getattr(
                self.reasoning_adapter, "contract_version", "l6-v0.1",
            ),
        }
        request_hash = core.sha256_text(core.canonical_json(request_payload))
        allowed = [candidate["hypothesis_type"] for candidate in ranked] + ["UNKNOWN"]

        def acceptable(payload: dict | None) -> bool:
            # Display recovery adopts wording only: the recovered projection keeps the
            # sealed judgment fields untouched (recovered = dict(dr)). Requiring the
            # model's secondary hypothesis to equal the sealed one deadlocked recovery —
            # the model may legitimately add a secondary beyond the deterministic
            # candidate list, and that extra field is discarded anyway.
            ok, _ = core.validate_daily_output(payload, allowed, dr["confidence"])
            if not ok:
                return False
            if payload.get("primary_hypothesis_type") != dr["primary_hypothesis_type"]:
                return False
            summary = payload.get("reasoning_summary")
            actions = payload.get("recommended_actions")
            return (
                isinstance(summary, str)
                and _is_chinese_product_text(summary)
                and isinstance(actions, list)
                and all(
                    isinstance(action, str) and _is_chinese_product_text(action)
                    for action in actions
                )
            )

        output = model_cache.lookup(self.l7, request_hash, "REASONING")
        calls = 0
        if not acceptable(output):
            for _ in range(3):
                candidate = self._attempt_reasoning_sample(bundle, ranked)
                calls += 1
                if acceptable(candidate):
                    output = candidate
                    break
            if not acceptable(output):
                return None, calls
            model_cache.store(
                self.l7, request_hash, "REASONING", self.reasoning_adapter.model_id, output,
            )
            self.l7.commit()

        summary = output.get("reasoning_summary")
        actions = output.get("recommended_actions")
        recovered = dict(dr)
        recovered["reasoning_summary"] = summary
        recovered["recommended_actions_json"] = json.dumps(actions[:3], ensure_ascii=False)
        return recovered, calls

    def _exact_evidence(self, l6, l5, l4, l3, stored, analysis_date):
        if stored is None:
            return []
        return readers.exact_bundle_evidence(
            l6, l5, l4, l3, stored["id"], stored["bundle"], analysis_date,
            freshness_date=datetime.now(ZoneInfo(self.cfg.timezone_name)).date().isoformat(),
        )

    def _render_product_copy(
        self, dr, bundle, product_state, analysis_date, facts, source,
        judgment_updated, change_note,
    ):
        from l7.rendering.renderer import render_today_payload
        from l7.upstream.l6_bridge import _is_chinese_product_text

        display_dr = dict(dr)
        if source is None:
            source_text = (dr.get("reasoning_summary") or "").strip()
            try:
                source_actions = json.loads(dr.get("recommended_actions_json") or "[]")
            except json.JSONDecodeError:
                source_actions = []
        else:
            source_text = str((source.get("cause") or {}).get("text") or "").strip()
            source_actions = source.get("actions") or []
        if not isinstance(source_actions, list):
            source_actions = []
        product_texts = [source_text, *[a for a in source_actions if isinstance(a, str)]]
        needs_translation = any(text and not _is_chinese_product_text(text) for text in product_texts)
        calls = 0
        complete = True
        if needs_translation and hasattr(self.reasoning_adapter, "translate_product_copy"):
            calls = 1
            try:
                translated = self.reasoning_adapter.translate_product_copy(source_text, source_actions)
                source_text = translated["reasoning_summary"]
                source_actions = translated["recommended_actions"]
            except Exception:
                source_text, source_actions = "", []
                complete = False
        elif needs_translation:
            source_text, source_actions = "", []
            complete = False
        display_dr["reasoning_summary"] = source_text
        display_dr["recommended_actions_json"] = json.dumps(source_actions, ensure_ascii=False)
        return render_today_payload(
            dr=display_dr, bundle=bundle, product_state=product_state,
            analysis_date=analysis_date, generated_at_utc=utc_now(),
            timezone_name=self.cfg.timezone_name, judgment_updated=judgment_updated,
            change_note=change_note, evidence_facts=facts,
        ), calls, complete

    def _insert_today_version(
        self, user_id, analysis_date, dr_id, bhash, product_state, sig_sha,
        rendered, judgment_updated, trigger,
    ):
        cur = self.l7.execute(
            "INSERT INTO today_versions (user_id, analysis_date, l6_daily_reasoning_id,"
            " bundle_sha256, product_state, signature_sha256, rendered_json, judgment_updated,"
            " change_note, trigger, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, analysis_date, dr_id, bhash, product_state, sig_sha,
             json.dumps(rendered, ensure_ascii=False), 1 if judgment_updated else 0,
             rendered.get("change_note"), trigger, utc_now()),
        )
        self.l7.commit()
        return cur.lastrowid

    def _touch_rendered(self, rendered, version_id, judgment_updated=False):
        refreshed = dict(rendered)
        now = utc_now()
        refreshed["updated_at_utc"] = now
        refreshed["updated_at_local_hhmm"] = self._hhmm(now)
        refreshed["judgment_updated"] = judgment_updated
        refreshed["version_id"] = version_id
        return refreshed

    def _change_note(self, latest_version_row, dr) -> str:
        return "判断已更新：依据或结论发生了变化，可查看版本历史了解来源。"

    def _log_change(self, user_id, run_id, outcome, bundle, analysis_date):
        deviating = [
            {"metric": d.get("metric"), "feature_name": d.get("feature_name"),
             "deviation_class": d.get("deviation_class")}
            for d in bundle.get("deviations", [])
            if d.get("deviation_class") in ("ABOVE_TYPICAL_RANGE", "BELOW_TYPICAL_RANGE")
        ]
        self.l7.execute(
            "INSERT INTO evidence_change_log (user_id, eval_run_id, kind, detail_json, created_at_utc)"
            " VALUES (?,?,?,?,?)",
            (user_id, run_id, outcome,
             json.dumps({"analysis_date": analysis_date, "deviating": deviating}, ensure_ascii=False),
             utc_now()),
        )
        self.l7.commit()

    def _insert_eval_run(self, user_id, started, trigger, sig, bhash, outcome, model_calls, version_id) -> int:
        cur = self.l7.execute(
            "INSERT INTO eval_runs (user_id, started_at_utc, finished_at_utc, trigger,"
            " upstream_sig_json, bundle_sha256, recompute_reason, model_calls, outcome, today_version_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, started, utc_now(), trigger, json.dumps(sig, ensure_ascii=False),
             bhash, outcome, model_calls, outcome, version_id),
        )
        self.l7.commit()
        return cur.lastrowid

    def _hhmm(self, iso_utc: str) -> str:
        dt = datetime.fromisoformat(iso_utc).astimezone(ZoneInfo(self.cfg.timezone_name))
        return dt.strftime("%H:%M")
