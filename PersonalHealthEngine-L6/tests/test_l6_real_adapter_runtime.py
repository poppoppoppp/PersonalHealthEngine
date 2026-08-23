from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from l6_real_adapters_v0_1 import (  # noqa: E402
    MEDICAL_MODEL_TIMEOUT_S,
    RealMedGemmaMedicalModelAdapter,
)


def test_medgemma_default_timeout_supports_cpu_vps_inference():
    assert MEDICAL_MODEL_TIMEOUT_S >= 600
    assert RealMedGemmaMedicalModelAdapter().timeout_s == MEDICAL_MODEL_TIMEOUT_S


def test_medgemma_timeout_remains_explicitly_configurable():
    assert RealMedGemmaMedicalModelAdapter(timeout_s=30).timeout_s == 30
