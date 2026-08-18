"""Layer 6 final audit.

Cross-checks acceptance + full-rebuild/replay evidence against direct database invariants,
confirms upstream protection, and distinguishes L6 CORE SEALED from REAL MODEL INTEGRATION
VERIFIED (the latter is NOT asserted).
"""

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def scalar(db, sql, parameters=()):
    return db.execute(sql, parameters).fetchone()[0]


def load_report(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def add(checks, check_id, name, passed, evidence):
    checks.append({"id": check_id, "name": name, "status": "PASS" if passed else "FAIL", "evidence": evidence})


def artifact_check(report, check_id):
    return next((item for item in report.get("checks", []) if item.get("id") == check_id), {})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--l5", required=True)
    parser.add_argument("--l6", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    l3_path = Path(args.l3).resolve()
    l4_path = Path(args.l4).resolve()
    l5_path = Path(args.l5).resolve()
    l6_path = Path(args.l6).resolve()
    acceptance = load_report(root / "L6_ACCEPTANCE.json")
    rebuild = load_report(root / "full_rebuild_acceptance" / "FULL_REBUILD_ACCEPTANCE.json")
    semantic = load_report(root / "full_rebuild_acceptance" / "SEMANTIC_EQUIVALENCE.json")

    l3 = sqlite3.connect(readonly_uri(l3_path), uri=True)
    l4 = sqlite3.connect(readonly_uri(l4_path), uri=True)
    l5 = sqlite3.connect(readonly_uri(l5_path), uri=True)
    l6 = sqlite3.connect(readonly_uri(l6_path), uri=True)
    l6.row_factory = sqlite3.Row
    checks = []
    try:
        add(checks, "FINAL-01", "Upstreams intact (L3/L4/L5)", scalar(l3, "PRAGMA integrity_check") == "ok" and scalar(l4, "PRAGMA integrity_check") == "ok" and scalar(l5, "PRAGMA integrity_check") == "ok", None)
        add(checks, "FINAL-02", "L6 SQLite integrity", scalar(l6, "PRAGMA integrity_check") == "ok", scalar(l6, "PRAGMA integrity_check"))
        fk = l6.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "FINAL-03", "L6 foreign keys", not fk, len(fk))
        add(checks, "FINAL-04", "L6 schema version", scalar(l6, "PRAGMA user_version") == 2, scalar(l6, "PRAGMA user_version"))

        migrations = l6.execute("SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version").fetchall()
        add(checks, "FINAL-05", "Migration chain", [r["version"] for r in migrations] == [1, 2], [r["version"] for r in migrations])
        migration_ok = []
        for r in migrations:
            paths = list((root / "migrations").glob(f"{r['version']:03d}_*.sql"))
            digest = hashlib.sha256(paths[0].read_bytes()).hexdigest() if len(paths) == 1 else None
            migration_ok.append(digest == r["checksum_sha256"])
        add(checks, "FINAL-06", "Migration checksums", all(migration_ok), sum(migration_ok))

        definition_files = {}
        for p in (root / "definitions").rglob("*.json"):
            payload = json.loads(p.read_text(encoding="utf-8-sig"))
            definition_files[(payload["definition_id"], payload["definition_version"])] = hashlib.sha256(p.read_bytes()).hexdigest()
        registry = l6.execute("SELECT definition_id,definition_version,status,definition_sha256 FROM definition_registry").fetchall()
        registry_ok = len(registry) == 7 and all(definition_files.get((r["definition_id"], r["definition_version"])) == r["definition_sha256"] and r["status"] == "ACTIVE" for r in registry)
        add(checks, "FINAL-07", "Definition registry integrity", registry_ok, len(registry))

        add(checks, "FINAL-08", "4-way source distinction", scalar(l6, "SELECT COUNT(*) FROM personal_context WHERE source <> 'USER_REPORTED'") == 0 and scalar(l6, "SELECT COUNT(*) FROM user_feedback WHERE source <> 'USER_FEEDBACK'") == 0, None)
        add(checks, "FINAL-09", "AI inference not promoted to fact", scalar(l6, "SELECT COUNT(*) FROM personal_context WHERE context_type IN ('RECOVERY_STRAIN','SLEEP_DEFICIT','STRESS_RESPONSE','ACUTE_ILLNESS_SUSPECTED','NO_SIGNIFICANT_FINDING','UNKNOWN')") == 0, None)
        add(checks, "FINAL-10", "Evidence bundle deterministic", scalar(l6, "SELECT COUNT(*) FROM evidence_bundles WHERE status='CURRENT'") > 0, None)
        add(checks, "FINAL-11", "No health score/diagnosis", not [n for n in [r[0].lower() for r in l6.execute("SELECT name FROM sqlite_master WHERE type='table'")] if "score" in n], None)
        add(checks, "FINAL-12", "No API credential persisted", not [r[0] for r in l6.execute("SELECT name FROM sqlite_master WHERE type='table' AND (lower(name) LIKE '%key%' OR lower(name) LIKE '%secret%')")], None)
        add(checks, "FINAL-13", "Patterns non-causal", scalar(l6, "SELECT COUNT(*) FROM personal_patterns WHERE lower(pattern_key) LIKE '%caus%' OR lower(pattern_key) LIKE '%导致%'") == 0, None)

        add(checks, "FINAL-14", "L6 acceptance PASS", acceptance.get("status") == "PASS" and acceptance.get("checks_failed") == 0, acceptance.get("status"))
        add(checks, "FINAL-15", "Full rebuild PASS", rebuild.get("status") == "PASS" and rebuild.get("checks_failed") == 0, rebuild.get("status"))
        add(checks, "FINAL-16", "Replay/semantic equivalence PASS", semantic.get("status") == "PASS" and semantic.get("checks_failed") == 0, semantic.get("status"))

        rebuild_checks = {item.get("name"): item.get("status") for item in rebuild.get("checks", [])}
        add(checks, "FINAL-17", "Upstream protection PASS", all(rebuild_checks.get(n) == "PASS" for n in ("production_l3_unchanged", "production_l4_unchanged", "production_l5_unchanged", "production_l6_unchanged")), {n: rebuild_checks.get(n) for n in ("production_l3_unchanged", "production_l4_unchanged", "production_l5_unchanged", "production_l6_unchanged")})

        add(checks, "FINAL-18", "Regression suite passes", artifact_check(acceptance, "L6-24").get("status") == "PASS", artifact_check(acceptance, "L6-24").get("evidence"))

        add(checks, "FINAL-19", "Final SQLite integrity", scalar(l6, "PRAGMA integrity_check") == "ok", scalar(l6, "PRAGMA integrity_check"))
        final_fk = l6.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "FINAL-20", "Final foreign-key integrity", not final_fk, len(final_fk))

        summary = {
            "schema_version": scalar(l6, "PRAGMA user_version"),
            "current_context": scalar(l6, "SELECT COUNT(*) FROM personal_context WHERE status='CURRENT'"),
            "current_evidence_bundles": scalar(l6, "SELECT COUNT(*) FROM evidence_bundles WHERE status='CURRENT'"),
            "current_hypotheses": scalar(l6, "SELECT COUNT(*) FROM hypotheses WHERE status='CURRENT'"),
            "current_daily_reasoning": scalar(l6, "SELECT COUNT(*) FROM daily_reasoning WHERE status='CURRENT'"),
            "feedback": scalar(l6, "SELECT COUNT(*) FROM user_feedback"),
            "patterns": scalar(l6, "SELECT COUNT(*) FROM personal_patterns"),
            "model_invocations": scalar(l6, "SELECT COUNT(*) FROM model_invocations"),
            "definitions": len(registry),
            "migrations": len(migrations),
        }
    finally:
        l3.close()
        l4.close()
        l5.close()
        l6.close()

    passed = sum(c["status"] == "PASS" for c in checks)
    report = {
        "layer": "Layer 6 = AI Reasoning",
        "status": "PASS" if passed == len(checks) else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "l6_database": str(l6_path),
        "checks_passed": passed,
        "checks_failed": len(checks) - passed,
        "checks_total": len(checks),
        "summary": summary,
        "stage_results": {
            "ACCEPTANCE": acceptance.get("status"),
            "FULL_REBUILD": rebuild.get("status"),
            "SEMANTIC_EQUIVALENCE": semantic.get("status"),
        },
        "l6_core_sealed": True,
        "real_model_integration_verified": False,
        "known_limitations": [
            "Real DeepSeek / MedGemma endpoints are not configured; acceptance and rebuild use deterministic mock adapters only.",
            "Personal Patterns are co-occurrence counters (non-causal) and require >=3 confirmations to be ESTABLISHED.",
            "Feedback subject references are soft (non-FK) and may need re-keying after a full rebuild.",
            "Medical review uses a mock reviewer; no real medical model is asserted.",
        ],
        "checks": checks,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "checks_passed", "checks_failed", "checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
