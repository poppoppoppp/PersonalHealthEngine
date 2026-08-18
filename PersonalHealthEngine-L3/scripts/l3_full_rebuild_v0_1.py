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


def backup_sqlite(source, destination):
    uri = Path(source).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as source_db:
        with closing(sqlite3.connect(destination)) as destination_db:
            source_db.backup(destination_db)


def run_step(name, command, root):
    result = subprocess.run(
        command, cwd=root, text=True, capture_output=True, check=False
    )
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
    parser.add_argument("--l2", required=True)
    parser.add_argument("--production-l3", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    scripts = root / "scripts"
    definitions = root / "definitions"
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_l2 = output_dir / "l2_snapshot.sqlite3"
    rebuilt_l3 = output_dir / "rebuilt_l3.sqlite3"
    semantic_output = output_dir / "SEMANTIC_EQUIVALENCE.json"
    report_output = output_dir / "FULL_REBUILD_ACCEPTANCE.json"

    for target in (snapshot_l2, rebuilt_l3, semantic_output, report_output):
        if target.exists():
            archive = target.with_name(
                target.name + ".previous-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            )
            target.replace(archive)

    l2_hash_before = sha256(args.l2)
    l3_hash_before = sha256(args.production_l3)
    backup_sqlite(args.l2, snapshot_l2)
    steps = []
    python = sys.executable

    steps.append(
        run_step(
            "migrations",
            [python, str(scripts / "apply_migrations_v0_1.py"), "--l3", str(rebuilt_l3), "--migrations-root", str(root / "migrations")],
            root,
        )
    )
    point_core = scripts / "l3_point_core_v0_1.py"
    for label, definition_name in (
        ("heart_rate", "heart_rate_v0_1.json"),
        ("spo2", "spo2_v0_1.json"),
        ("stress", "xiaomi_stress_score_v0_1.json"),
    ):
        steps.append(
            run_step(
                f"l3a_point_{label}",
                [
                    python,str(scripts / "l3_point_full_runner_v0_1.py"),
                    "--l2",str(snapshot_l2),"--l3",str(rebuilt_l3),
                    "--definition",str(definitions / "normalizers" / definition_name),
                    "--core",str(point_core),
                ],
                root,
            )
        )
        steps.append(
            run_step(
                f"l3a_point_{label}_checkpoint",
                [
                    python,str(scripts / "l3_point_checkpoint_bootstrap_v0_1.py"),
                    "--l2",str(snapshot_l2),"--l3",str(rebuilt_l3),
                    "--definition",str(definitions / "normalizers" / definition_name),
                ],
                root,
            )
        )
    steps.append(
        run_step(
            "l3a_daily_resting_heart_rate",
            [
                python,str(scripts / "l3_daily_full_runner_v0_1.py"),
                "--l2",str(snapshot_l2),"--l3",str(rebuilt_l3),
                "--definition",str(definitions / "normalizers" / "resting_heart_rate_v0_1.json"),
            ],
            root,
        )
    )
    bucket_core = scripts / "l3_bucket_incremental_runner_v0_1.py"
    for label in ("steps", "calories"):
        steps.append(
            run_step(
                f"l3a_bucket_{label}",
                [
                    python,str(scripts / "l3_bucket_full_runner_v0_1.py"),
                    "--l2",str(snapshot_l2),"--l3",str(rebuilt_l3),
                    "--definition",str(definitions / "normalizers" / f"{label}_v0_1.json"),
                    "--core",str(bucket_core),
                ],
                root,
            )
        )
        steps.append(
            run_step(
                f"l3a_bucket_{label}_checkpoint",
                [
                    python,str(scripts / "l3_bucket_checkpoint_bootstrap_v0_1.py"),
                    "--l2",str(snapshot_l2),"--l3",str(rebuilt_l3),
                    "--definition",str(definitions / "normalizers" / f"{label}_v0_1.json"),
                ],
                root,
            )
        )
    steps.append(
        run_step(
            "l3a_interval_sleep",
            [
                python,str(scripts / "l3_sleep_full_runner_v0_1.py"),
                "--l2",str(snapshot_l2),"--l3",str(rebuilt_l3),
                "--definition",str(definitions / "normalizers" / "sleep_v0_1.json"),
            ],
            root,
        )
    )
    steps.append(
        run_step(
            "l3b_full",
            [
                python,str(scripts / "l3b_materializer_v0_1.py"),"--mode","full",
                "--l2",str(snapshot_l2),"--l3",str(rebuilt_l3),
                "--quality-definition",str(definitions / "quality" / "l3b_structural_quality_v0_1.json"),
                "--resolution-definition",str(definitions / "resolution" / "l3b_source_resolution_v0_1.json"),
            ],
            root,
        )
    )
    steps.append(
        run_step(
            "l3c_full",
            [
                python,str(scripts / "l3c_materializer_v0_1.py"),"--mode","full",
                "--l2",str(snapshot_l2),"--l3",str(rebuilt_l3),
                "--definition",str(definitions / "features" / "daily_features_v0_1.json"),
            ],
            root,
        )
    )
    steps.append(
        run_step(
            "semantic_equivalence",
            [
                python,str(scripts / "l3_semantic_compare_v0_1.py"),
                "--production",str(Path(args.production_l3).resolve()),
                "--rebuilt",str(rebuilt_l3),"--output",str(semantic_output),
            ],
            root,
        )
    )

    semantic = json.loads(semantic_output.read_text(encoding="utf-8"))
    l2_unchanged = sha256(args.l2) == l2_hash_before
    production_l3_unchanged = sha256(args.production_l3) == l3_hash_before
    checks = [
        {"name": step["name"], "status": step["status"]} for step in steps
    ]
    checks.extend(
        [
            {"name": "production_l2_unchanged", "status": "PASS" if l2_unchanged else "FAIL"},
            {"name": "production_l3_unchanged", "status": "PASS" if production_l3_unchanged else "FAIL"},
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
        "l2_snapshot": str(snapshot_l2),
        "rebuilt_l3": str(rebuilt_l3),
        "semantic_report": str(semantic_output),
        "steps": steps,
        "checks": checks,
    }
    report_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in ("status","semantic_equivalence","checks_passed","checks_failed","checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
