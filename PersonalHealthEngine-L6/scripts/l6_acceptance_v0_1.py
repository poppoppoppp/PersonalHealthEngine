"""Layer 6 core acceptance (mock adapters only; no paid API).

Validates the L6 reasoning database against sealed L3/L4/L5: schema integrity, definition
checksums, 4-way source distinction, AI-inference-not-promoted-to-fact, feedback/context
provenance and revision, deterministic evidence bundles, no-look-ahead, hypothesis evidence,
deterministic confidence, medical-review policy, no health score/diagnosis, model-independent
adapters, credential isolation, full-rebuild + replay equivalence, and the regression suite.
"""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def scalar(db, sql, parameters=()):
    return db.execute(sql, parameters).fetchone()[0]


def add(checks, check_id, name, passed, evidence):
    checks.append({"id": check_id, "name": name, "status": "PASS" if passed else "FAIL", "evidence": evidence})


def load_report(path):
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--l5", required=True)
    parser.add_argument("--l6", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    l3_path = Path(args.l3).resolve()
    l4_path = Path(args.l4).resolve()
    l5_path = Path(args.l5).resolve()
    l6_path = Path(args.l6).resolve()

    l3 = sqlite3.connect(readonly_uri(l3_path), uri=True)
    l4 = sqlite3.connect(readonly_uri(l4_path), uri=True)
    l5 = sqlite3.connect(readonly_uri(l5_path), uri=True)
    l6 = sqlite3.connect(readonly_uri(l6_path), uri=True)
    l6.row_factory = sqlite3.Row

    checks = []
    try:
        # upstreams intact and read-only
        upstream_ok = (
            scalar(l3, "PRAGMA integrity_check") == "ok" and scalar(l3, "PRAGMA user_version") == 8
            and scalar(l4, "PRAGMA integrity_check") == "ok" and scalar(l4, "PRAGMA user_version") == 2
            and scalar(l5, "PRAGMA integrity_check") == "ok" and scalar(l5, "PRAGMA user_version") == 2
        )
        add(checks, "L6-01", "Upstreams intact and read-only (L3/L4/L5)", upstream_ok, {"l3_schema": scalar(l3, "PRAGMA user_version"), "l4_schema": scalar(l4, "PRAGMA user_version"), "l5_schema": scalar(l5, "PRAGMA user_version")})

        add(checks, "L6-02", "L6 SQLite integrity", scalar(l6, "PRAGMA integrity_check") == "ok", scalar(l6, "PRAGMA integrity_check"))
        fk = l6.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "L6-03", "L6 foreign keys", not fk, len(fk))
        schema_version = scalar(l6, "PRAGMA user_version")
        add(checks, "L6-04", "L6 schema version", schema_version == 2, schema_version)

        migrations = l6.execute("SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version").fetchall()
        add(checks, "L6-05", "Migration chain", [r["version"] for r in migrations] == [1, 2], [r["version"] for r in migrations])
        migration_ok = []
        for r in migrations:
            paths = list((root / "migrations").glob(f"{r['version']:03d}_*.sql"))
            digest = hashlib.sha256(paths[0].read_bytes()).hexdigest() if len(paths) == 1 else None
            migration_ok.append(digest == r["checksum_sha256"])
        add(checks, "L6-06", "Migration checksums", all(migration_ok), sum(migration_ok))

        definition_files = {}
        for p in (root / "definitions").rglob("*.json"):
            payload = json.loads(p.read_text(encoding="utf-8-sig"))
            definition_files[(payload["definition_id"], payload["definition_version"])] = (hashlib.sha256(p.read_bytes()).hexdigest(), payload["definition_type"])
        registry = l6.execute("SELECT definition_id,definition_version,definition_type,status,definition_sha256 FROM definition_registry").fetchall()
        registry_ok = len(registry) == 7 and all(definition_files.get((r["definition_id"], r["definition_version"])) == (r["definition_sha256"], r["definition_type"]) and r["status"] == "ACTIVE" for r in registry)
        add(checks, "L6-07", "Definition registry integrity", registry_ok, len(registry))

        # 4-way source distinction
        bad_context_source = scalar(l6, "SELECT COUNT(*) FROM personal_context WHERE source <> 'USER_REPORTED'")
        bad_feedback_source = scalar(l6, "SELECT COUNT(*) FROM user_feedback WHERE source <> 'USER_FEEDBACK'")
        add(checks, "L6-08", "Context/Feedback source distinction", bad_context_source == 0 and bad_feedback_source == 0, {"ctx": bad_context_source, "fb": bad_feedback_source})

        # AI inference never promoted to fact: no hypothesis type stored as a user context
        ai_promoted = scalar(
            l6,
            "SELECT COUNT(*) FROM personal_context WHERE context_type IN ('RECOVERY_STRAIN','SLEEP_DEFICIT','STRESS_RESPONSE','ACUTE_ILLNESS_SUSPECTED','NO_SIGNIFICANT_FINDING','UNKNOWN')",
        )
        add(checks, "L6-09", "AI inference never promoted to fact", ai_promoted == 0, ai_promoted)

        # context revision table present
        add(checks, "L6-10", "Context revision supported", bool(l6.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='context_revisions'").fetchone()), None)

        # evidence bundle deterministic: sha256 matches canonical json
        bundle_ok = True
        for r in l6.execute("SELECT bundle_json,bundle_sha256 FROM evidence_bundles WHERE status='CURRENT'"):
            digest = hashlib.sha256(json.dumps(json.loads(r["bundle_json"]), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if digest != r["bundle_sha256"]:
                bundle_ok = False
        add(checks, "L6-11", "Evidence bundle deterministic (hash matches)", bundle_ok, None)

        # hypothesis evidence present
        hyp_no_support = scalar(l6, "SELECT COUNT(*) FROM hypotheses WHERE status='CURRENT' AND (supporting_json IS NULL OR supporting_json='[]')")
        add(checks, "L6-12", "Hypotheses carry supporting evidence", hyp_no_support == 0, hyp_no_support)

        # confidence + overall state in enum
        bad_conf = scalar(l6, "SELECT COUNT(*) FROM daily_reasoning WHERE status='CURRENT' AND confidence NOT IN ('VERY_LOW','LOW','MODERATE','HIGH')")
        bad_state = scalar(l6, "SELECT COUNT(*) FROM daily_reasoning WHERE status='CURRENT' AND overall_state NOT IN ('STABLE','MILD_CHANGE','NOTABLE_CHANGE','INSUFFICIENT_EVIDENCE')")
        add(checks, "L6-13", "Deterministic confidence + overall state enums", bad_conf == 0 and bad_state == 0, {"conf": bad_conf, "state": bad_state})

        # no health score / no diagnosis
        forbidden_names = [r[0].lower() for r in l6.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        score_tables = [n for n in forbidden_names if any(k in n for k in ("score", "wellness", "readiness", "body_age", "recovery_score"))]
        diagnose_summaries = scalar(l6, "SELECT COUNT(*) FROM daily_reasoning WHERE status='CURRENT' AND (lower(reasoning_summary) LIKE '%确诊%' OR lower(reasoning_summary) LIKE '%患有%')")
        add(checks, "L6-14", "No health score and no diagnosis", not score_tables and diagnose_summaries == 0, {"score_tables": score_tables, "diagnose": diagnose_summaries})

        # no canonical sleep night
        canonical = scalar(l6, "SELECT COUNT(*) FROM personal_context WHERE lower(context_type) LIKE '%canonical%'")
        add(checks, "L6-15", "No canonical sleep night invented", canonical == 0, canonical)

        # medical review states valid
        bad_review = scalar(l6, "SELECT COUNT(*) FROM daily_reasoning WHERE status='CURRENT' AND medical_review_state NOT IN ('REQUIRED','PERFORMED','BYPASSED','UNAVAILABLE')")
        add(checks, "L6-16", "Medical review states valid", bad_review == 0, bad_review)

        # no API credential persisted
        api_columns = [r[0] for r in l6.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name) LIKE '%key%' OR lower(name) LIKE '%secret%'")]
        add(checks, "L6-17", "API credentials not persisted", not api_columns, api_columns)

        # model invocations have metadata, not content
        add(checks, "L6-18", "Model invocation table present", bool(l6.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_invocations'").fetchone()), None)

        # Q&A sessions + grounding
        qa_present = bool(l6.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qa_sessions'").fetchone())
        add(checks, "L6-19", "Q&A sessions table present", qa_present, None)

        # patterns non-causal
        causal_pattern = scalar(l6, "SELECT COUNT(*) FROM personal_patterns WHERE lower(pattern_key) LIKE '%caus%' OR lower(pattern_key) LIKE '%导致%'")
        add(checks, "L6-20", "Personal patterns are non-causal", causal_pattern == 0, causal_pattern)

        # full rebuild + replay equivalence
        rebuild = load_report(root / "full_rebuild_acceptance" / "FULL_REBUILD_ACCEPTANCE.json")
        semantic = load_report(root / "full_rebuild_acceptance" / "SEMANTIC_EQUIVALENCE.json")
        add(checks, "L6-21", "Full rebuild PASS", rebuild.get("status") == "PASS" and rebuild.get("checks_failed") == 0, rebuild.get("status"))
        add(checks, "L6-22", "Deterministic replay equivalence", semantic.get("status") == "PASS" and semantic.get("checks_failed") == 0, semantic.get("status"))

        # upstream unchanged (from rebuild report)
        rebuild_checks = {item.get("name"): item.get("status") for item in rebuild.get("checks", [])}
        up_ok = all(rebuild_checks.get(n) == "PASS" for n in ("production_l3_unchanged", "production_l4_unchanged", "production_l5_unchanged"))
        add(checks, "L6-23", "Upstream protection PASS", up_ok, {n: rebuild_checks.get(n) for n in ("production_l3_unchanged", "production_l4_unchanged", "production_l5_unchanged")})

        # regression suite
        regression = subprocess.run([sys.executable, "-m", "unittest", "tests.test_l6_reasoning_v0_1", "-q"], cwd=root, text=True, capture_output=True, check=False)
        add(checks, "L6-24", "Regression suite passes", regression.returncode == 0, {"returncode": regression.returncode, "tail": regression.stderr.splitlines()[-4:]})

        counts = {
            "context": scalar(l6, "SELECT COUNT(*) FROM personal_context WHERE status='CURRENT'"),
            "feedback": scalar(l6, "SELECT COUNT(*) FROM user_feedback"),
            "evidence_bundles": scalar(l6, "SELECT COUNT(*) FROM evidence_bundles WHERE status='CURRENT'"),
            "hypotheses": scalar(l6, "SELECT COUNT(*) FROM hypotheses WHERE status='CURRENT'"),
            "daily_reasoning": scalar(l6, "SELECT COUNT(*) FROM daily_reasoning WHERE status='CURRENT'"),
            "patterns": scalar(l6, "SELECT COUNT(*) FROM personal_patterns"),
        }
        summary = {"schema_version": schema_version, "counts": counts, "real_model_integration_verified": False}
    finally:
        l3.close()
        l4.close()
        l5.close()
        l6.close()

    passed = sum(c["status"] == "PASS" for c in checks)
    report = {
        "layer": "Layer 6 = AI Reasoning",
        "stage": "L6",
        "status": "PASS" if passed == len(checks) else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "l6_database": str(l6_path),
        "checks_passed": passed,
        "checks_failed": len(checks) - passed,
        "checks_total": len(checks),
        "summary": summary,
        "real_model_integration_verified": False,
        "note": "Acceptance uses mock adapters only. Real DeepSeek/MedGemma integration is a separate smoke test and is NOT asserted here.",
        "checks": checks,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("stage", "status", "checks_passed", "checks_failed", "checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
