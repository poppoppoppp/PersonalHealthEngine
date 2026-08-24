"""CONTROLLED real-model gate (NOT a repeatable pytest test — burns exactly one DeepSeek call).

Validates the L7 EngineOrchestrator real-adapter path end-to-end against a throwaway copy of
the sealed L6 production db. This exercises the exact integration that was previously missing
(Discovery item #1): real DeepSeek reasoning materialized through the sealed `reconcile_daily`
write path. The production L6 db is NOT modified.

Run manually:
    python scripts/validate_real_reasoning_gate.py
Requires DEEPSEEK_API_KEY in the environment.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from l7.config import Config, DEFAULT_L6_CODE  # noqa: E402
from l7.engine.orchestrator import EngineOrchestrator  # noqa: E402
from l7.store.db import connect_l7  # noqa: E402
from l7.upstream.l6_bridge import L6Bridge, ensure_l6_on_path  # noqa: E402

ensure_l6_on_path(DEFAULT_L6_CODE)

PROD_L3 = r"D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3"
PROD_L4 = r"D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3"
PROD_L5 = r"D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3"
PROD_L6 = r"D:\PersonalHealthEngine-L6\db\personal_health_reasoning.sqlite3"


def main() -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("SKIP: DEEPSEEK_API_KEY not set")
        return 2

    tmp = BACKEND / ".tmp" / "realgate"
    if tmp.exists():
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    l6_copy = tmp / "l6_copy.sqlite3"
    src = sqlite3.connect(Path(PROD_L6).resolve().as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(str(l6_copy))
    src.backup(dst); dst.close(); src.close()

    # Force a bundle change so the orchestrator must reason (need_model=True).
    w = sqlite3.connect(str(l6_copy)); w.row_factory = sqlite3.Row
    w.execute(
        "INSERT INTO personal_context (context_date,context_type,body_part,severity,raw_text,"
        "source,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,'CURRENT',?,?)",
        ("2026-08-16", "STRESS", None, None, "最近工作压力比较大", "USER_REPORTED",
         "2026-08-17T14:00:00+00:00", "2026-08-17T14:00:00+00:00"),
    )
    w.commit(); w.close()

    cfg = Config(
        environment="local", l3_db=PROD_L3, l4_db=PROD_L4, l5_db=PROD_L5,
        l6_db=str(l6_copy), l7_db=str(tmp / "l7.sqlite3"),
        reasoning_adapter="deepseek", medical_adapter="mock",
    )
    l7 = connect_l7(cfg.l7_db)
    bridge = L6Bridge(cfg.l6_code_dir)
    real_adapter = bridge.real_adapters.RealDeepSeekReasoningModelAdapter()
    orch = EngineOrchestrator(cfg, l7, bridge=bridge, reasoning_adapter=real_adapter)

    result = orch.evaluate("owner", "real_gate")
    p = result.today_payload

    # Inspect what got materialized into the throwaway L6 copy.
    r = sqlite3.connect(str(l6_copy)); r.row_factory = sqlite3.Row
    cur = [dict(x) for x in r.execute(
        "SELECT id, reasoning_model, overall_state, primary_hypothesis_type, confidence,"
        " reasoning_summary, status FROM daily_reasoning ORDER BY id"
    )]
    r.close()

    report = {
        "outcome": result.outcome,
        "model_calls": result.model_calls,
        "adapter_reason_daily_calls": "real-deepseek",
        "product_state": p.get("product_state"),
        "product_state_label": p.get("product_state_label"),
        "primary_hypothesis": p.get("cause", {}).get("hypothesis_type"),
        "actions": p.get("actions"),
        "judgment_updated": p.get("judgment_updated"),
        "reasoning_model_rows_in_copy": cur,
        "summary_preview": (p.get("cause", {}).get("text") or "")[:200],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = (
        result.outcome == "REMATERIALIZED"
        and result.model_calls == 1
        and any(row["reasoning_model"] == "deepseek-v4-flash" and row["status"] == "CURRENT" for row in cur)
        and (p.get("cause", {}).get("text") or "").strip() != ""
    )
    print("GATE:", "PASS" if ok else "FAIL")
    l7.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
