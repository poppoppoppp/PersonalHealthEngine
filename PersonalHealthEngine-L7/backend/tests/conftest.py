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

from l7.config import Config, DEFAULT_L6_CODE  # noqa: E402
from l7.engine.orchestrator import EngineOrchestrator  # noqa: E402
from l7.services.today import TodayService  # noqa: E402
from l7.store.db import connect_l7  # noqa: E402
from l7.upstream.l6_bridge import ensure_l6_on_path  # noqa: E402

ensure_l6_on_path(DEFAULT_L6_CODE)

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

    def extract_context(self, text, today):
        return self._inner.extract_context(text, today)

    def reason_daily(self, bundle, candidates):
        self.reason_daily_calls += 1
        return self._inner.reason_daily(bundle, candidates)

    def answer_question(self, question, bundle, candidates):
        self.answer_calls += 1
        return self._inner.answer_question(question, bundle, candidates)


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
