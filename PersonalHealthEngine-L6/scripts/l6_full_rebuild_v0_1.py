"""Layer 6 full rebuild + deterministic replay acceptance.

Builds a fresh L6 from sealed L3/L4/L5 + a verbatim copy of the user-entered state
(personal_context, user_feedback), re-runs deterministic daily reasoning with the mock
adapter, and proves semantic equivalence to production without modifying any upstream or
production L6.
"""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_step(name, command, root):
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    step = {"name": name, "status": "PASS" if result.returncode == 0 else "FAIL", "returncode": result.returncode, "stdout_tail": result.stdout.splitlines()[-20:], "stderr_tail": result.stderr.splitlines()[-20:]}
    if result.returncode != 0:
        raise RuntimeError(json.dumps(step, ensure_ascii=False, indent=2))
    return step


def materializer_command(root, mode, l3, l4, l5, l6, analysis_date=None):
    definitions = root / "definitions"
    cmd = [
        sys.executable, str(root / "scripts" / "l6_reasoning_materializer_v0_1.py"),
        "--mode", mode, "--l3", str(l3), "--l4", str(l4), "--l5", str(l5), "--l6", str(l6),
        "--reasoning-adapter", "mock", "--medical-adapter", "mock",
        "--context", str(definitions / "context" / "l6_context_extraction_v0_1.json"),
        "--evidence", str(definitions / "evidence" / "l6_evidence_assembly_v0_1.json"),
        "--hypothesis", str(definitions / "hypothesis" / "l6_hypothesis_v0_1.json"),
        "--confidence", str(definitions / "confidence" / "l6_confidence_v0_1.json"),
        "--daily", str(definitions / "daily" / "l6_daily_reasoning_v0_1.json"),
        "--medical", str(definitions / "medical" / "l6_medical_review_v0_1.json"),
        "--pattern", str(definitions / "pattern" / "l6_personal_pattern_v0_1.json"),
    ]
    if analysis_date:
        cmd += ["--analysis-date", analysis_date]
    return cmd


def copy_user_state(source, destination):
    with closing(sqlite3.connect(source)) as src:
        src.row_factory = sqlite3.Row
        with closing(sqlite3.connect(destination)) as dst:
            dst.execute("PRAGMA foreign_keys = OFF")
            ctx = src.execute("SELECT * FROM personal_context").fetchall()
            for row in ctx:
                dst.execute(
                    "INSERT INTO personal_context (id,context_date,context_type,body_part,severity,raw_text,source,status,supersedes_id,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (row["id"], row["context_date"], row["context_type"], row["body_part"], row["severity"], row["raw_text"], row["source"], row["status"], row["supersedes_id"], row["created_at_utc"], row["updated_at_utc"]),
                )
            for row in src.execute("SELECT * FROM user_feedback").fetchall():
                dst.execute(
                    "INSERT INTO user_feedback (id,subject_type,subject_id,feedback_status,correction_text,source,created_at_utc) VALUES (?,?,?,?,?,?,?)",
                    (row["id"], row["subject_type"], row["subject_id"], row["feedback_status"], row["correction_text"], row["source"], row["created_at_utc"]),
                )
            # Personal Patterns are learned state accumulated from feedback; copied verbatim.
            for row in src.execute("SELECT * FROM personal_patterns").fetchall():
                dst.execute(
                    "INSERT INTO personal_patterns (id,pattern_key,trigger_context_type,outcome_signal,support_count,total_count,maturity,first_seen_date,last_seen_date,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (row["id"], row["pattern_key"], row["trigger_context_type"], row["outcome_signal"], row["support_count"], row["total_count"], row["maturity"], row["first_seen_date"], row["last_seen_date"], row["created_at_utc"], row["updated_at_utc"]),
                )
            dst.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--l5", required=True)
    parser.add_argument("--production-l6", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rebuilt_l6 = output_dir / "rebuilt_l6.sqlite3"
    semantic_output = output_dir / "SEMANTIC_EQUIVALENCE.json"
    report_output = output_dir / "FULL_REBUILD_ACCEPTANCE.json"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for target in (rebuilt_l6, semantic_output, report_output):
        if target.exists():
            target.replace(target.with_name(target.name + ".previous-" + stamp))

    hashes_before = {n: sha256(getattr(args, n)) for n in ("l3", "l4", "l5", "production_l6")}

    steps = []
    steps.append(run_step("migrations", [sys.executable, str(root / "scripts" / "apply_migrations_v0_1.py"), "--l6", str(rebuilt_l6), "--migrations-root", str(root / "migrations")], root))
    copy_user_state(args.production_l6, rebuilt_l6)
    steps.append(run_step("l6_reasoning_replay", materializer_command(root, "replay", Path(args.l3).resolve(), Path(args.l4).resolve(), Path(args.l5).resolve(), rebuilt_l6), root))
    steps.append(run_step("semantic_equivalence", [sys.executable, str(root / "scripts" / "l6_semantic_compare_v0_1.py"), "--production", str(Path(args.production_l6).resolve()), "--rebuilt", str(rebuilt_l6), "--output", str(semantic_output)], root))

    semantic = json.loads(semantic_output.read_text(encoding="utf-8"))
    unchanged = {n: sha256(getattr(args, n)) == hashes_before[n] for n in ("l3", "l4", "l5", "production_l6")}
    checks = [{"name": step["name"], "status": step["status"]} for step in steps]
    checks.extend([
        {"name": "production_l3_unchanged", "status": "PASS" if unchanged["l3"] else "FAIL"},
        {"name": "production_l4_unchanged", "status": "PASS" if unchanged["l4"] else "FAIL"},
        {"name": "production_l5_unchanged", "status": "PASS" if unchanged["l5"] else "FAIL"},
        {"name": "production_l6_unchanged", "status": "PASS" if unchanged["production_l6"] else "FAIL"},
    ])
    passed = sum(item["status"] == "PASS" for item in checks)
    report = {
        "status": "PASS" if passed == len(checks) and semantic["status"] == "PASS" else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "semantic_equivalence": semantic["status"],
        "checks_passed": passed,
        "checks_failed": len(checks) - passed,
        "checks_total": len(checks),
        "rebuilt_l6": str(rebuilt_l6),
        "semantic_report": str(semantic_output),
        "steps": steps,
        "checks": checks,
    }
    report_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "semantic_equivalence", "checks_passed", "checks_failed", "checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
