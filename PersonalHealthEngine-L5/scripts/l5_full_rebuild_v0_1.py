"""Layer 5 full-rebuild acceptance: build a fresh L5 from sealed L3 + L4 and prove it is
semantically equivalent to production, without modifying the sealed upstreams or production L5.
"""

import argparse
import hashlib
import json
import subprocess
import sys
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
    step = {
        "name": name,
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "returncode": result.returncode,
        "stdout_tail": result.stdout.splitlines()[-20:],
        "stderr_tail": result.stderr.splitlines()[-20:],
    }
    if result.returncode != 0:
        raise RuntimeError(json.dumps(step, ensure_ascii=False, indent=2))
    return step


def materializer_command(mode, l3, l4, l5, definitions):
    return [
        sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "l5_analytics_materializer_v0_1.py"),
        "--mode", mode, "--l3", str(l3), "--l4", str(l4), "--l5", str(l5),
        "--deviation", str(definitions / "deviation" / "l5a_deviation_robust_v0_1.json"),
        "--persistence", str(definitions / "persistence" / "l5b_persistence_v0_1.json"),
        "--trend", str(definitions / "trend" / "l5b_trend_robust_v0_1.json"),
        "--change", str(definitions / "change" / "l5c_change_point_v0_1.json"),
        "--relationship", str(definitions / "relationship" / "l5d_relationship_v0_1.json"),
        "--evidence", str(definitions / "evidence" / "l5e_evidence_v0_1.json"),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--production-l5", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    scripts = root / "scripts"
    definitions = root / "definitions"
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rebuilt_l5 = output_dir / "rebuilt_l5.sqlite3"
    semantic_output = output_dir / "SEMANTIC_EQUIVALENCE.json"
    report_output = output_dir / "FULL_REBUILD_ACCEPTANCE.json"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for target in (rebuilt_l5, semantic_output, report_output):
        if target.exists():
            target.replace(target.with_name(target.name + ".previous-" + stamp))

    hashes_before = {
        "l3": sha256(args.l3),
        "l4": sha256(args.l4),
        "l5": sha256(args.production_l5),
    }

    steps = []
    steps.append(run_step(
        "migrations",
        [sys.executable, str(scripts / "apply_migrations_v0_1.py"), "--l5", str(rebuilt_l5), "--migrations-root", str(root / "migrations")],
        root,
    ))
    steps.append(run_step(
        "l5_full_materialization",
        materializer_command("full", Path(args.l3).resolve(), Path(args.l4).resolve(), rebuilt_l5, definitions),
        root,
    ))
    steps.append(run_step(
        "semantic_equivalence",
        [
            sys.executable, str(scripts / "l5_semantic_compare_v0_1.py"),
            "--production", str(Path(args.production_l5).resolve()),
            "--rebuilt", str(rebuilt_l5), "--output", str(semantic_output),
        ],
        root,
    ))

    semantic = json.loads(semantic_output.read_text(encoding="utf-8"))
    arg_map = {"l3": "l3", "l4": "l4", "l5": "production_l5"}
    unchanged = {name: sha256(getattr(args, arg_map[name])) == hashes_before[name] for name in ("l3", "l4", "l5")}
    checks = [{"name": step["name"], "status": step["status"]} for step in steps]
    checks.extend(
        [
            {"name": "production_l3_unchanged", "status": "PASS" if unchanged["l3"] else "FAIL"},
            {"name": "production_l4_unchanged", "status": "PASS" if unchanged["l4"] else "FAIL"},
            {"name": "production_l5_unchanged", "status": "PASS" if unchanged["l5"] else "FAIL"},
        ]
    )
    passed = sum(item["status"] == "PASS" for item in checks)
    report = {
        "status": "PASS" if passed == len(checks) and semantic["status"] == "PASS" else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "semantic_equivalence": semantic["status"],
        "checks_passed": passed,
        "checks_failed": len(checks) - passed,
        "checks_total": len(checks),
        "l3_source": str(Path(args.l3).resolve()),
        "l4_source": str(Path(args.l4).resolve()),
        "rebuilt_l5": str(rebuilt_l5),
        "semantic_report": str(semantic_output),
        "steps": steps,
        "checks": checks,
    }
    report_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "semantic_equivalence", "checks_passed", "checks_failed", "checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
