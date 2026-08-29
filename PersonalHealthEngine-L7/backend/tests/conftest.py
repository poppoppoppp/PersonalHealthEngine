"""Phase C test fixtures.

Strategy: L3/L4/L5 production databases are opened strictly read-only (mode=ro) and shared
— L7 never writes them. The L6 database is copied per-test (sqlite backup API) because the
sealed write path (`reconcile_daily`) appends versions into it. Real model adapters are
never used in tests; a counting mock adapter asserts model-call discipline.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from l7.config import Config  # noqa: E402
from l7.engine.orchestrator import EngineOrchestrator  # noqa: E402
from l7.services.today import TodayService  # noqa: E402
from l7.store.db import connect_l7  # noqa: E402
from l7.upstream.l6_bridge import ensure_l6_on_path  # noqa: E402

REPO_L6_CODE = str(BACKEND.parents[1] / "PersonalHealthEngine-L6" / "scripts")
ensure_l6_on_path(REPO_L6_CODE)

PROD_L3 = r"D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3"
PROD_L4 = r"D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3"
PROD_L5 = r"D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3"
PROD_L6 = r"D:\PersonalHealthEngine-L6\db\personal_health_reasoning.sqlite3"


class CountingMockReasoningAdapter:
    """Deterministic mock (sealed MockReasoningModelAdapter behavior) + a call counter."""

    model_id = "mock-reasoning-v0.1"

    def __init__(self):
        from l6_adapters_v0_1 import MockReasoningModelAdapter

        self._inner = MockReasoningModelAdapter()
        self.reason_daily_calls = 0
        self.answer_calls = 0
        self.semantic_calls = 0
        self.last_answer_bundle = None

    def classify_question(self, question, conversation_semantics):
        self.semantic_calls += 1
        q = question.lower()
        prior = " ".join(turn.get("text", "") for turn in conversation_semantics)
        if question in {"你是谁？", "你能做什么？", "你根据什么回答我？"}:
            scope = "PRODUCT_META"
            intent = "PRODUCT_IDENTITY"
            decision_type = None
            domains = []
            metrics = []
            evidence = False
            consequence = "NONE"
        elif any(text in q for text in (
            "python", "法国首都", "股票", "写一首歌", "商业计划书", "推荐一部电影",
        )):
            scope = "OUT_OF_SCOPE"
            intent = "NON_HEALTH_REQUEST"
            decision_type = None
            domains = []
            metrics = []
            evidence = False
            consequence = "NONE"
        elif any(text in question for text in ("昨晚其实两点才睡", "今天有点头疼", "昨天跑了十公里")):
            scope = "HEALTH_CONTEXT"
            intent = "CONTEXT_REPORT"
            decision_type = None
            domains = ["RECOVERY"]
            metrics = []
            evidence = False
            consequence = "LOW"
        elif "最近7天平均睡眠" in question:
            scope = "HEALTH_DATA"
            intent = "METRIC_LOOKUP"
            decision_type = None
            domains = ["SLEEP"]
            metrics = ["SLEEP_DURATION"]
            evidence = True
            consequence = "NONE"
        elif "最近7天平均步数" in question:
            scope = "HEALTH_DATA"
            intent = "METRIC_LOOKUP"
            decision_type = None
            domains = ["ACTIVITY"]
            metrics = ["STEPS"]
            evidence = True
            consequence = "NONE"
        elif "静息心率" in question and "趋势" in question:
            scope = "HEALTH_DATA"
            intent = "METRIC_TREND"
            decision_type = None
            domains = ["CARDIOVASCULAR"]
            metrics = ["RESTING_HEART_RATE"]
            evidence = True
            consequence = "NONE"
        elif "昨晚睡" in question or "睡了多久" in question:
            scope = "HEALTH_DATA"
            intent = "METRIC_LOOKUP"
            decision_type = None
            domains = ["SLEEP"]
            metrics = ["SLEEP_DURATION"]
            evidence = True
            consequence = "NONE"
        elif any(text in question for text in ("散步", "转几圈", "少动点", "出去走走", "躺着")):
            scope = "HEALTH_DECISION"
            intent = "ACTIVITY_RECOMMENDATION"
            decision_type = "PHYSICAL_ACTIVITY"
            domains = ["ACTIVITY", "RECOVERY"]
            metrics = ["STEPS", "RESTING_HEART_RATE", "SLEEP_DURATION"]
            evidence = True
            consequence = "MODERATE"
        elif "半小时" in question and ("跑步" in prior or "训练" in prior):
            scope = "HEALTH_DECISION"
            intent = "ACTIVITY_RECOMMENDATION"
            decision_type = "PHYSICAL_ACTIVITY"
            domains = ["ACTIVITY", "RECOVERY"]
            metrics = ["RESTING_HEART_RATE", "SLEEP_DURATION"]
            evidence = True
            consequence = "MODERATE"
        else:
            scope = "HEALTH_DECISION"
            intent = "ACTIVITY_RECOMMENDATION"
            decision_type = "PHYSICAL_ACTIVITY"
            domains = ["ACTIVITY", "RECOVERY"]
            metrics = ["RESTING_HEART_RATE", "SLEEP_DURATION"]
            evidence = True
            consequence = "MODERATE" if any(k in question for k in ("跑步", "训练", "练")) else "LOW"
        return {
            "scope": scope,
            "intent": intent,
            "decision_type": decision_type,
            "relevant_domains": domains,
            "relevant_metrics": metrics,
            "requires_personal_evidence": evidence,
            "time_range": (
                "LAST_7_DAYS" if "最近7天" in question else
                "LAST_30_DAYS" if "趋势" in question else
                "LAST_NIGHT" if scope == "HEALTH_DATA" else "CURRENT"
            ),
            "aggregation": (
                "AVERAGE" if "平均" in question else
                "TREND" if "趋势" in question else
                "LATEST" if scope == "HEALTH_DATA" else None
            ),
            "medical_consequence": consequence,
            "needs_medical_review": consequence == "HIGH",
            "potential_context": scope == "HEALTH_CONTEXT",
            "context_write": "AUTO_SAVE" if scope == "HEALTH_CONTEXT" else "NONE",
            "reason": "test_fixture_classification",
        }

    def extract_context(self, text, today):
        return self._inner.extract_context(text, today)

    def reason_daily(self, bundle, candidates):
        self.reason_daily_calls += 1
        return self._inner.reason_daily(bundle, candidates)

    def answer_question(self, question, bundle, candidates):
        self.answer_calls += 1
        self.last_answer_bundle = bundle
        return self._inner.answer_question(question, bundle, candidates)

    def answer_question_candidate(self, question, bundle, candidates):
        legacy = self.answer_question(question, bundle, candidates)
        direct = legacy.get("answer_text") or legacy.get("reasoning_summary")
        return {
            "direct_answer": direct,
            "reason": legacy.get("reasoning_summary") or direct,
            "recommended_actions": legacy.get("recommended_actions", [])[:3],
            "confidence": "LOW",
            "evidence_refs": [next(iter(bundle["evidence_catalog"]))],
            "medical_claims": [],
            "uncertainties": [],
        }


def _backup(src: str, dst: Path) -> None:
    source = sqlite3.connect(Path(src).resolve().as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(str(dst))
    source.backup(target)
    target.close()
    source.close()


import os
import shutil

TEST_TMP_ROOT = Path(r"D:\PersonalHealthEngine-L7\backend\.tmp\tests")


@pytest.fixture()
def tmp_path(request):
    """Workspace-local replacement for pytest's tmp_path (sandbox-friendly)."""
    d = TEST_TMP_ROOT / request.node.name
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def env(tmp_path):
    l6_copy = tmp_path / "l6_copy.sqlite3"
    _backup(PROD_L6, l6_copy)
    cfg = Config(
        environment="local",
        l3_db=PROD_L3,
        l4_db=PROD_L4,
        l5_db=PROD_L5,
        l6_db=str(l6_copy),
        l7_db=str(tmp_path / "l7_product.sqlite3"),
        l6_code_dir=REPO_L6_CODE,
        reasoning_adapter="mock",
        medical_adapter="mock",
    )
    l7 = connect_l7(cfg.l7_db)
    adapter = CountingMockReasoningAdapter()
    orch = EngineOrchestrator(cfg, l7, reasoning_adapter=adapter)
    service = TodayService(cfg, l7, orch)
    yield {"cfg": cfg, "l7": l7, "orch": orch, "adapter": adapter, "service": service,
           "l6_copy": str(l6_copy), "tmp_path": tmp_path}
    l7.close()


@pytest.fixture()
def l6_write(env):
    """Writable handle to the per-test L6 copy (for injecting test contexts)."""
    con = sqlite3.connect(env["l6_copy"])
    con.row_factory = sqlite3.Row
    yield con
    con.close()
