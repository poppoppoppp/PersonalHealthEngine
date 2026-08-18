"""Today service: the product-facing side of Current Today State."""

from __future__ import annotations

import json
import sqlite3

from l7.config import Config
from l7.engine.orchestrator import EngineOrchestrator, EvaluationResult
from l7.store.db import open_readonly
from l7.upstream import readers
from l7.rendering.renderer import METRIC_LABELS, metric_label


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
        return [dict(r) for r in rows]

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
        l5 = open_readonly(self.cfg.l5_db)
        l4 = open_readonly(self.cfg.l4_db)
        l3 = open_readonly(self.cfg.l3_db)
        try:
            analysis_date = readers.latest_analysis_date(l5)
            if analysis_date is None:
                return {"analysis_date": None, "metrics": []}
            stored = readers.read_current_bundle(l6, analysis_date)
            if stored is None:
                return {"analysis_date": analysis_date, "metrics": []}
            bundle = stored["bundle"]
            metrics: list[dict] = []
            seen_features: set[str] = set()
            for d in bundle.get("deviations", []):
                feature_name = d.get("feature_name")
                if not feature_name or feature_name in seen_features:
                    continue
                if d.get("deviation_class") not in (
                    "ABOVE_TYPICAL_RANGE", "BELOW_TYPICAL_RANGE"
                ):
                    continue
                seen_features.add(feature_name)
                metrics.append({
                    "feature_name": feature_name,
                    "metric": d.get("metric"),
                    "metric_label": metric_label(d.get("metric")),
                    "deviation_class": d.get("deviation_class"),
                    "baseline_maturity": d.get("baseline_maturity"),
                    "evidence_status": d.get("evidence_status"),
                    "series": readers.feature_series(l3, feature_name),
                    "deviations": readers.deviation_detail(l5, feature_name),
                    "baselines": readers.baseline_detail(l4, feature_name, analysis_date),
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
                "outcome": p["outcome_signal"],
                "support_count": support,
                "total_count": total,
                "counter_examples": max(total - support, 0),
                "first_seen_date": p["first_seen_date"],
                "last_seen_date": p["last_seen_date"],
                "maturity": p["maturity"],
                "display_status": display,
                "description": (
                    f"过去 {total} 次「{p['trigger_context_type']}」之后，"
                    f"{support} 次出现「{p['outcome_signal']}」。"
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
