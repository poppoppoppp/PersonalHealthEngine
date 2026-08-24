"""Today service: the product-facing side of Current Today State."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from l7.config import Config
from l7.engine.orchestrator import EngineOrchestrator, EvaluationResult
from l7.store.db import open_readonly
from l7.upstream import readers
from l7.rendering.labels import (
    context_label,
    outcome_label,
    pattern_status_label,
    STATE_LABELS,
    trigger_label,
)


class TodayService:
    def __init__(self, config: Config, l7: sqlite3.Connection, orchestrator: EngineOrchestrator):
        self.cfg = config
        self.l7 = l7
        self.orch = orchestrator

    def get_today(self, user_id: str, trigger: str = "app_open") -> dict:
        result: EvaluationResult = self.orch.evaluate(user_id, trigger)
        return result.today_payload

    def list_versions(self, user_id: str, limit: int = 30) -> list[dict]:
        rows = self.l7.execute(
            "SELECT id, analysis_date, product_state, judgment_updated, change_note, trigger,"
            " created_at_utc, l6_daily_reasoning_id, bundle_sha256"
            " FROM today_versions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        versions = []
        for row in rows:
            item = dict(row)
            item["product_state_label"] = STATE_LABELS.get(
                item["product_state"], "状态未知",
            )
            item["trigger_label"] = trigger_label(item["trigger"])
            item["created_at_local"] = datetime.fromisoformat(
                item["created_at_utc"]
            ).astimezone(ZoneInfo(self.cfg.timezone_name)).strftime("%m-%d %H:%M")
            versions.append(item)
        return versions

    def list_eval_runs(self, user_id: str, limit: int = 30) -> list[dict]:
        rows = self.l7.execute(
            "SELECT id, trigger, outcome, model_calls, bundle_sha256, started_at_utc,"
            " finished_at_utc FROM eval_runs WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def evidence_detail(self, user_id: str) -> dict:
        """Evidence Level 3: raw values, baseline details and quality info for the metrics
        that actually deviate in the current bundle — nothing else (§47 dashboard boundary)."""
        l6 = open_readonly(self.cfg.l6_db)
        l5 = open_readonly(self.cfg.l5_db, immutable_if_checkpointed=True)
        l4 = open_readonly(self.cfg.l4_db, immutable_if_checkpointed=True)
        l3 = open_readonly(self.cfg.l3_db, immutable_if_checkpointed=True)
        try:
            analysis_date = readers.latest_analysis_date(l5)
            if analysis_date is None:
                return {"analysis_date": None, "metrics": []}
            stored = readers.read_current_bundle(l6, analysis_date)
            if stored is None:
                return {"analysis_date": analysis_date, "metrics": []}
            bundle = stored["bundle"]
            metrics: list[dict] = []
            facts = readers.exact_bundle_evidence(
                l6, l5, l4, l3, stored["id"], bundle, analysis_date,
            )
            for fact in facts:
                deviation = dict(fact["deviation"])
                baseline = dict(fact["baseline"])
                metrics.append({
                    **{k: fact[k] for k in (
                        "metric", "feature_name", "feature_label", "feature_date", "freshness_days",
                        "freshness_label", "deviation_class", "deviation_label",
                        "baseline_maturity", "baseline_maturity_label", "evidence_status",
                        "evidence_status_label", "current_value", "baseline_median", "unit",
                        "current_value_display", "baseline_value_display", "text",
                        "l5_deviation_id", "l3_feature_id", "l4_baseline_id",
                    )},
                    "metric_label": fact["feature_label"],
                    "series": readers.exact_feature_series(
                        l3, fact["feature_name"], fact["source_sid"],
                    ),
                    "deviations": [deviation],
                    "baselines": [baseline],
                })
            return {
                "analysis_date": analysis_date,
                "bundle_sha256": stored["bundle_sha256"],
                "provenance_note": "数值来自 L3 特征 / L4 个人基线 / L5 分析（只读）。",
                "metrics": metrics,
            }
        finally:
            for c in (l6, l5, l4, l3):
                c.close()

    def patterns(self, user_id: str) -> dict:
        """我的规律 projection: only patterns with real action value are surfaced (§36);
        single events never become patterns (§33); counterevidence is always shown (§34).
        display_status exposes upgrade/downgrade/invalidation semantics (§42) without ever
        deleting the underlying counters."""
        l6 = open_readonly(self.cfg.l6_db)
        try:
            rows = readers.read_patterns(l6)
        finally:
            l6.close()
        shown = []
        observing = 0
        for p in rows:
            support, total = p["support_count"], p["total_count"]
            weakened_or_invalidated = total >= 4 and support * 2 < total
            actionable = (p["maturity"] == "ESTABLISHED" or support >= 2
                          or weakened_or_invalidated)
            if not actionable:
                observing += 1
                continue
            if p["maturity"] == "ESTABLISHED":
                display = "ESTABLISHED"
            elif total >= 4 and support == 0:
                display = "INVALIDATED"
            elif weakened_or_invalidated:
                display = "WEAKENED"
            else:
                display = "OBSERVING"
            shown.append({
                "pattern_key": p["pattern_key"],
                "trigger": p["trigger_context_type"],
                "trigger_label": context_label(p["trigger_context_type"]),
                "outcome": p["outcome_signal"],
                "outcome_label": outcome_label(p["outcome_signal"]),
                "support_count": support,
                "total_count": total,
                "counter_examples": max(total - support, 0),
                "first_seen_date": p["first_seen_date"],
                "last_seen_date": p["last_seen_date"],
                "maturity": p["maturity"],
                "display_status": display,
                "display_status_label": pattern_status_label(display),
                "description": (
                    f"过去 {total} 次「{context_label(p['trigger_context_type'])}」之后，"
                    f"{support} 次出现「{outcome_label(p['outcome_signal'])}」。"
                ),
            })
        return {
            "patterns": shown,
            "observing_count": observing,
            "accumulation_note": (
                "规律需要多次独立证据支持，正在积累中。" if observing else None
            ),
        }

    def model_usage(self, user_id: str) -> dict:
        rows = self.l7.execute(
            "SELECT COALESCE(SUM(model_calls),0) AS calls, COUNT(*) AS runs FROM eval_runs WHERE user_id=?",
            (user_id,),
        ).fetchone()
        cached = self.l7.execute("SELECT COUNT(*) AS n FROM model_call_cache").fetchone()
        return {"eval_runs": rows["runs"], "total_model_calls": rows["calls"], "cached_entries": cached["n"]}
