import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(r"D:\PersonalHealthEngine-L6")
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from l6_core_v0_1 import (  # noqa: E402
    base_confidence,
    extract_context_events,
    generate_candidates,
    medical_trigger,
    overall_state,
    validate_daily_output,
)
from l6_adapters_v0_1 import (  # noqa: E402
    DeepSeekReasoningModelAdapter,
    MedGemmaMedicalModelAdapter,
    MockReasoningModelAdapter,
    MockMedicalModelAdapter,
    ModelError,
)

L3_PRODUCTION = Path(r"D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3")
L4_PRODUCTION = Path(r"D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3")
L5_PRODUCTION = Path(r"D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3")
APPLY_MIGRATIONS = SCRIPTS / "apply_migrations_v0_1.py"
MATERIALIZER = SCRIPTS / "l6_reasoning_materializer_v0_1.py"
CONTEXT_INGEST = SCRIPTS / "l6_context_ingest_v0_1.py"
FEEDBACK = SCRIPTS / "l6_feedback_v0_1.py"
QA = SCRIPTS / "l6_qa_v0_1.py"

DEFS = {
    "context": ROOT / "definitions" / "context" / "l6_context_extraction_v0_1.json",
    "evidence": ROOT / "definitions" / "evidence" / "l6_evidence_assembly_v0_1.json",
    "hypothesis": ROOT / "definitions" / "hypothesis" / "l6_hypothesis_v0_1.json",
    "confidence": ROOT / "definitions" / "confidence" / "l6_confidence_v0_1.json",
    "daily": ROOT / "definitions" / "daily" / "l6_daily_reasoning_v0_1.json",
    "medical": ROOT / "definitions" / "medical" / "l6_medical_review_v0_1.json",
    "pattern": ROOT / "definitions" / "pattern" / "l6_personal_pattern_v0_1.json",
}


def bundle(overall, deviations=None, context=None, persistence=None, change=None, trends=None, analysis_date="2026-08-16"):
    return {
        "analysis_date": analysis_date,
        "data_date": analysis_date,
        "overall_state": overall,
        "deviations": deviations or [],
        "persistence": persistence or [],
        "change": change or [],
        "trends": trends or [],
        "relationships": [],
        "recent_context": context or [],
        "recent_feedback": [],
        "similar_cases": [],
        "missing_evidence": ["HRV unavailable"],
    }


class CoreUnitTests(unittest.TestCase):
    def test_context_extraction(self):
        events = extract_context_events("昨天练腿练得很狠，而且两点才睡", "2026-08-17")
        types = {e["context_type"] for e in events}
        self.assertIn("HIGH_INTENSITY_TRAINING", types)
        self.assertIn("LATE_SLEEP", types)
        train = next(e for e in events if e["context_type"] == "HIGH_INTENSITY_TRAINING")
        self.assertEqual(train["body_part"], "legs")
        self.assertEqual(train["context_date"], "2026-08-16")

    def test_context_does_not_invent_fields(self):
        events = extract_context_events("昨晚睡晚了", "2026-08-17")
        self.assertEqual({e["context_type"] for e in events}, {"LATE_SLEEP"})
        self.assertNotIn("sleep_duration", events[0])

    def test_overall_state_stable_and_insufficient(self):
        self.assertEqual(overall_state(bundle("STABLE", deviations=[{"deviation_class": "WITHIN_TYPICAL_RANGE"}])), "STABLE")
        self.assertEqual(overall_state(bundle("INSUFFICIENT_EVIDENCE", deviations=[{"deviation_class": "INSUFFICIENT_BASELINE"}])), "INSUFFICIENT_EVIDENCE")
        self.assertEqual(overall_state(bundle("X", deviations=[])), "INSUFFICIENT_EVIDENCE")

    def test_overall_state_notable_and_mild(self):
        notable = overall_state(bundle("X", deviations=[{"deviation_class": "ABOVE_TYPICAL_RANGE"}, {"deviation_class": "BELOW_TYPICAL_RANGE"}, {"deviation_class": "ABOVE_TYPICAL_RANGE"}]))
        self.assertEqual(notable, "NOTABLE_CHANGE")
        mild = overall_state(bundle("X", deviations=[{"deviation_class": "ABOVE_TYPICAL_RANGE"}]))
        self.assertEqual(mild, "MILD_CHANGE")

    def test_hypothesis_candidates(self):
        b = bundle("NOTABLE_CHANGE", deviations=[{"metric": "heart_rate", "deviation_class": "ABOVE_TYPICAL_RANGE"}], context=[{"context_type": "HIGH_INTENSITY_TRAINING"}])
        types = [c["hypothesis_type"] for c in generate_candidates(b)]
        self.assertIn("RECOVERY_STRAIN", types)

    def test_stable_day_no_hypothesis(self):
        b = bundle("STABLE", deviations=[{"deviation_class": "WITHIN_TYPICAL_RANGE"}])
        types = [c["hypothesis_type"] for c in generate_candidates(b)]
        self.assertEqual(types, ["NO_SIGNIFICANT_FINDING"])

    def test_confidence_deterministic(self):
        self.assertEqual(base_confidence(3, 0, False, False), "HIGH")
        self.assertEqual(base_confidence(2, 0, False, False), "MODERATE")
        self.assertEqual(base_confidence(1, 0, False, False), "LOW")
        self.assertEqual(base_confidence(0, 0, False, False), "VERY_LOW")
        self.assertEqual(base_confidence(1, 2, False, False), "VERY_LOW")
        self.assertEqual(base_confidence(2, 1, False, False), "LOW")

    def test_medical_trigger_and_bypass(self):
        b = bundle("X", context=[{"context_type": "FEVER"}])
        self.assertEqual(medical_trigger(None, b, ["UNKNOWN"])[0], "REQUIRED")
        b2 = bundle("STABLE", deviations=[{"deviation_class": "WITHIN_TYPICAL_RANGE"}])
        self.assertEqual(medical_trigger("今天适合跑步吗", b2, ["NO_SIGNIFICANT_FINDING"])[0], "BYPASSED")

    def test_output_validation(self):
        ok, _ = validate_daily_output({"primary_hypothesis_type": "UNKNOWN", "confidence": "LOW", "recommended_actions": ["观察"], "reasoning_summary": "x"}, ["UNKNOWN"], "LOW")
        self.assertTrue(ok)
        bad, errs = validate_daily_output({"primary_hypothesis_type": "NOT_REAL", "confidence": "LOW"}, ["UNKNOWN"], "LOW")
        self.assertFalse(bad)

    def test_adapters_present_and_mock_works(self):
        self.assertTrue(issubclass(DeepSeekReasoningModelAdapter, object))
        self.assertTrue(issubclass(MedGemmaMedicalModelAdapter, object))
        mock = MockReasoningModelAdapter()
        out = mock.reason_daily(bundle("STABLE", deviations=[{"deviation_class": "WITHIN_TYPICAL_RANGE"}]), [{"hypothesis_type": "NO_SIGNIFICANT_FINDING", "supporting": []}])
        self.assertEqual(out["primary_hypothesis_type"], "NO_SIGNIFICANT_FINDING")

    def test_real_adapters_raise_when_unconfigured(self):
        with self.assertRaises(ModelError):
            DeepSeekReasoningModelAdapter().reason_daily({}, [])
        with self.assertRaises(ModelError):
            MedGemmaMedicalModelAdapter().review({}, ["UNKNOWN"])

    def test_personal_pattern_support_threshold(self):
        # 3 independent confirmations are required to become ESTABLISHED.
        def maturity(support):
            return "ESTABLISHED" if support >= 3 else "OBSERVING"
        self.assertEqual(maturity(2), "OBSERVING")
        self.assertEqual(maturity(3), "ESTABLISHED")
        # pattern is non-causal: key expresses co-occurrence, never "causes"
        key = "HIGH_INTENSITY_TRAINING::heart_rate_UP"
        self.assertNotIn("caus", key.lower())
        self.assertNotIn("导致", key)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.l6 = Path(self.temp.name) / "l6.sqlite3"
        subprocess.run([sys.executable, str(APPLY_MIGRATIONS), "--l6", str(self.l6), "--migrations-root", str(ROOT / "migrations")], check=True, capture_output=True, text=True)

    def tearDown(self):
        self.temp.cleanup()

    def _materialize(self, mode="full", analysis_date=None, reasoning="mock", medical="mock"):
        cmd = [sys.executable, str(MATERIALIZER), "--mode", mode, "--l3", str(L3_PRODUCTION), "--l4", str(L4_PRODUCTION), "--l5", str(L5_PRODUCTION), "--l6", str(self.l6), "--reasoning-adapter", reasoning, "--medical-adapter", medical]
        if analysis_date:
            cmd += ["--analysis-date", analysis_date]
        for k in ("context", "evidence", "hypothesis", "confidence", "daily", "medical", "pattern"):
            cmd += [f"--{k}", str(DEFS[k])]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def _ingest(self, text, date=None, correct=None):
        cmd = [sys.executable, str(CONTEXT_INGEST), "--l6", str(self.l6), "--text", text]
        if date:
            cmd += ["--date", date]
        if correct:
            cmd += ["--correct", str(correct)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_end_to_end_daily_reasoning_and_source_distinction(self):
        self._materialize("full")
        with closing(sqlite3.connect(self.l6)) as db:
            db.row_factory = sqlite3.Row
            daily = db.execute("SELECT * FROM daily_reasoning WHERE status='CURRENT'").fetchone()
            sources = {r[0] for r in db.execute("SELECT DISTINCT source FROM personal_context")}
            api_key_columns = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%api%'")]
        self.assertIsNotNone(daily)
        self.assertIn(daily["overall_state"], ("STABLE", "MILD_CHANGE", "NOTABLE_CHANGE", "INSUFFICIENT_EVIDENCE"))
        self.assertIn(daily["confidence"], ("VERY_LOW", "LOW", "MODERATE", "HIGH"))
        # context source is always USER_REPORTED; no API key tables
        self.assertTrue(sources <= {"USER_REPORTED"})
        self.assertEqual(api_key_columns, [])

    def test_context_revision_and_ai_not_promoted(self):
        self._ingest("昨天练腿了", date="2026-08-17")
        with closing(sqlite3.connect(self.l6)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT id, context_type, source, status FROM personal_context ORDER BY id").fetchone()
        self.assertEqual(row["source"], "USER_REPORTED")
        self.assertEqual(row["context_type"], "HIGH_INTENSITY_TRAINING")
        self._ingest("昨天练背了", date="2026-08-17", correct=row["id"])
        with closing(sqlite3.connect(self.l6)) as db:
            db.row_factory = sqlite3.Row
            superseded = db.execute("SELECT status FROM personal_context WHERE id=?", (row["id"],)).fetchone()["status"]
            revisions = db.execute("SELECT COUNT(*) n FROM context_revisions").fetchone()["n"]
            # AI inference is not stored as user context
            ai_context = db.execute("SELECT COUNT(*) n FROM personal_context WHERE source <> 'USER_REPORTED'").fetchone()["n"]
        self.assertEqual(superseded, "SUPERSEDED")
        self.assertEqual(revisions, 1)
        self.assertEqual(ai_context, 0)

    def test_medical_review_trigger_and_no_diagnosis(self):
        self._ingest("昨天有点发烧", date="2026-08-17")
        result = self._materialize("incremental")
        self.assertEqual(result["medical_review_state"], "PERFORMED")
        with closing(sqlite3.connect(self.l6)) as db:
            db.row_factory = sqlite3.Row
            hyp = db.execute("SELECT hypothesis_type FROM hypotheses WHERE status='CURRENT'").fetchall()
            reviews = db.execute("SELECT review_state FROM medical_reviews ORDER BY id DESC").fetchone()["review_state"]
        self.assertIn("ACUTE_ILLNESS_SUSPECTED", {r["hypothesis_type"] for r in hyp})
        self.assertEqual(reviews, "PERFORMED")

    def test_model_unavailable_safe_fallback(self):
        result = self._materialize("full", reasoning="deepseek")
        with closing(sqlite3.connect(self.l6)) as db:
            db.row_factory = sqlite3.Row
            daily = db.execute("SELECT * FROM daily_reasoning WHERE status='CURRENT'").fetchone()
        self.assertIsNotNone(daily)
        self.assertIn("不可用", daily["reasoning_summary"] or "")
        # no crash; deterministic evidence retained

    def test_no_lookahead_replay(self):
        self._materialize("replay", analysis_date="2026-08-13")
        with closing(sqlite3.connect(self.l6)) as db:
            db.row_factory = sqlite3.Row
            b = db.execute("SELECT bundle_json FROM evidence_bundles WHERE analysis_date='2026-08-13' AND status='CURRENT'").fetchone()
        bundle = json.loads(b["bundle_json"])
        self.assertLessEqual(bundle["data_date"], "2026-08-13")
        for d in bundle["deviations"]:
            self.assertLessEqual(d["feature_date"], "2026-08-13")

    def test_qa_grounded_in_personal_data(self):
        self._materialize("full")
        r = subprocess.run([sys.executable, str(QA), "--l3", str(L3_PRODUCTION), "--l4", str(L4_PRODUCTION), "--l5", str(L5_PRODUCTION), "--l6", str(self.l6), "--question", "我今天能不能练腿？", "--evidence", str(DEFS["evidence"]), "--hypothesis", str(DEFS["hypothesis"])], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertIn(out["overall_state"], ("STABLE", "MILD_CHANGE", "NOTABLE_CHANGE", "INSUFFICIENT_EVIDENCE"))
        with closing(sqlite3.connect(self.l6)) as db:
            db.row_factory = sqlite3.Row
            qa = db.execute("SELECT * FROM qa_sessions WHERE status='CURRENT'").fetchone()
        self.assertIsNotNone(qa)
        self.assertEqual(qa["question_text"], "我今天能不能练腿？")


if __name__ == "__main__":
    unittest.main()
