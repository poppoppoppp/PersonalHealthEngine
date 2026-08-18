"""Phase I upstream integrity check: the SEALED layers must be untouched by L7 work.

Baseline = the state documented in L7_ENVIRONMENT_DISCOVERY.md (Phase A), which already
included the sealed L6 self-validation rows written by the sealed L6 CLIs before L7 began.
Read-only; prints a PASS/FAIL verdict per check."""
import sqlite3
import sys
from pathlib import Path

CHECKS = []

def check(name, ok, detail=""):
    CHECKS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

def ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)

# ---- L6: sealed reasoning db must still hold exactly the Phase-A-documented state. ----
con = ro(r"D:\PersonalHealthEngine-L6\db\personal_health_reasoning.sqlite3")
con.row_factory = sqlite3.Row
dr = [dict(r) for r in con.execute("SELECT * FROM daily_reasoning")]
check("L6 daily_reasoning rows == 1", len(dr) == 1, f"rows={len(dr)}")
check("L6 CURRENT judgment still mock-reasoning-v0.1",
      dr and dr[0]["reasoning_model"] == "mock-reasoning-v0.1" and dr[0]["status"] == "CURRENT",
      dr[0]["reasoning_model"] if dr else "-")

expected = {
    "personal_context": 2,          # sealed seed contexts
    "evidence_bundles": 1,
    "hypotheses": 1,
    "qa_sessions": 1,               # sealed L6 CLI self-validation (Phase A baseline)
    "medical_reviews": 1,           # sealed L6 CLI self-validation (Phase A baseline)
    "user_feedback": 1,             # sealed L6 CLI self-validation (Phase A baseline)
    "model_invocations": 1,
    "personal_patterns": 12,         # sealed pattern run, all OBSERVING
    "context_revisions": 0,         # L7 corrections would appear here
}
for table, want in expected.items():
    n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    check(f"L6 {table} == {want} (Phase A baseline)", n == want, f"rows={n}")
# L7-era writes would show these: none may exist.
n = con.execute("SELECT COUNT(*) FROM personal_context WHERE source='USER_REPORTED' AND created_at_utc > '2026-08-17T15:29'").fetchone()[0]
check("no L7-era context writes to production L6", n == 0, f"rows={n}")
con.close()

# ---- L5/L4/L3: readable read-only, non-empty. ----
for name, path, table in [
    ("L5 analytics", r"D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3", "deviation_analytics"),
    ("L4 baselines", r"D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3", "rolling_baselines"),
    ("L3 features", r"D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3", "derived_features"),
]:
    try:
        c = ro(path)
        n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        check(f"{name} readable ({table})", n > 0, f"rows={n}")
        c.close()
    except Exception as e:
        check(f"{name} readable", False, str(e))

failed = [c for c in CHECKS if not c[1]]
print("INTEGRITY:", "PASS" if not failed else f"FAIL ({len(failed)})")
sys.exit(0 if not failed else 1)
