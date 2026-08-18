"""Layer 4 full-rebuild acceptance: build a fresh L4 from the sealed L3 and prove
it is semantically equivalent to production, without modifying L3 or production L4.
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l3", required=True)
    parser.add_argument("--production-l4", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    scripts = root / "scripts"
    definitions = root / "definitions"
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rebuilt_l4 = output_dir / "rebuilt_l4.sqlite3"
    semantic_output = output_dir / "SEMANTIC_EQUIVALENCE.json"
    report_output = output_dir / "FULL_REBUILD_ACCEPTANCE.json"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for target in (rebuilt_l4, semantic_output, report_output):
        if target.exists():
            target.replace(target.with_name(target.name + ".previous-" + stamp))

    l3_hash_before = sha256(args.l3)
    l4_hash_before = sha256(args.production_l4)

    steps = []
    python = sys.executable

    steps.append(
        run_step(
            "migrations",
            [
                python, str(scripts / "apply_migrations_v0_1.py"),
                "--l4", str(rebuilt_l4),
                "--migrations-root", str(root / "migrations"),
            ],
            root,
        )
    )
    steps.append(
        run_step(
            "l4_full_materialization",
            [
                python, str(scripts / "l4_baseline_materializer_v0_1.py"),
                "--mode", "full",
                "--l3", str(Path(args.l3).resolve()),
                "--l4", str(rebuilt_l4),
                "--eligibility", str(definitions / "eligibility" / "l4a_baseline_eligibility_v0_1.json"),
                "--series", str(definitions / "series" / "l4b_baseline_series_v0_1.json"),
                "--windows", str(definitions / "windows" / "l4c_baseline_windows_v0_1.json"),
                "--maturity", str(definitions / "maturity" / "l4d_baseline_maturity_v0_1.json"),
            ],
            root,
        )
    )
    steps.append(
        run_step(
            "semantic_equivalence",
            [
                python, str(scripts / "l4_semantic_compare_v0_1.py"),
                "--production", str(Path(args.production_l4).resolve()),
                "--rebuilt", str(rebuilt_l4),
                "--output", str(semantic_output),
            ],
            root,
        )
    )

    semantic = json.loads(semantic_output.read_text(encoding="utf-8"))
    l3_unchanged = sha256(args.l3) == l3_hash_before
    production_l4_unchanged = sha256(args.production_l4) == l4_hash_before
    checks = [{"name": step["name"], "status": step["status"]} for step in steps]
    checks.extend(
        [
            {"name": "production_l3_unchanged", "status": "PASS" if l3_unchanged else "FAIL"},
            {"name": "production_l4_unchanged", "status": "PASS" if production_l4_unchanged else "FAIL"},
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
        "rebuilt_l4": str(rebuilt_l4),
        "semantic_report": str(semantic_output),
        "steps": steps,
        "checks": checks,
    }
    report_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "semantic_equivalence", "checks_passed", "checks_failed", "checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
