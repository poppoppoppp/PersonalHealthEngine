"""L7 configuration.

All paths and credentials come from environment variables with sensible local-dev
defaults that point at the real sealed layer layout on this machine. Nothing here
persists secrets; `api_token` defaults to a dev value only when environment == "local".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_L3 = r"D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3"
DEFAULT_L4 = r"D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3"
DEFAULT_L5 = r"D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3"
DEFAULT_L6 = r"D:\PersonalHealthEngine-L6\db\personal_health_reasoning.sqlite3"
DEFAULT_L6_CODE = r"D:\PersonalHealthEngine-L6\scripts"
DEFAULT_L6_DEFINITIONS = r"D:\PersonalHealthEngine-L6\definitions"
DEFAULT_L7_DB = r"D:\PersonalHealthEngine-L7\backend\data\l7_product.sqlite3"

# The owner's device timezone (Xiaomi Band 9 / Mi Fitness China).
DEFAULT_TIMEZONE = "Asia/Shanghai"

DEFINITION_FILES = {
    "context": ("context/l6_context_extraction_v0_1.json", "l6.context.extraction"),
    "evidence": ("evidence/l6_evidence_assembly_v0_1.json", "l6.evidence.assembly"),
    "hypothesis": ("hypothesis/l6_hypothesis_v0_1.json", "l6.hypothesis"),
    "confidence": ("confidence/l6_confidence_v0_1.json", "l6.confidence"),
    "daily": ("daily/l6_daily_reasoning_v0_1.json", "l6.daily.reasoning"),
    "medical": ("medical/l6_medical_review_v0_1.json", "l6.medical.review"),
    "pattern": ("pattern/l6_personal_pattern_v0_1.json", "l6.personal.pattern"),
}


@dataclass
class Config:
    environment: str = field(default_factory=lambda: os.environ.get("L7_ENV", "local"))
    l3_db: str = field(default_factory=lambda: os.environ.get("L7_L3_DB", DEFAULT_L3))
    l4_db: str = field(default_factory=lambda: os.environ.get("L7_L4_DB", DEFAULT_L4))
    l5_db: str = field(default_factory=lambda: os.environ.get("L7_L5_DB", DEFAULT_L5))
    l6_db: str = field(default_factory=lambda: os.environ.get("L7_L6_DB", DEFAULT_L6))
    l6_code_dir: str = field(default_factory=lambda: os.environ.get("L7_L6_CODE", DEFAULT_L6_CODE))
    l6_definitions_dir: str = field(
        default_factory=lambda: os.environ.get("L7_L6_DEFINITIONS", DEFAULT_L6_DEFINITIONS)
    )
    l7_db: str = field(default_factory=lambda: os.environ.get("L7_DB", DEFAULT_L7_DB))
    timezone_name: str = field(default_factory=lambda: os.environ.get("L7_TIMEZONE", DEFAULT_TIMEZONE))
    # Reasoning adapter: "mock" (deterministic default) or "deepseek" (real, gated).
    reasoning_adapter: str = field(
        default_factory=lambda: os.environ.get("L7_REASONING_ADAPTER", "mock")
    )
    medical_adapter: str = field(
        default_factory=lambda: os.environ.get("L7_MEDICAL_ADAPTER", "mock")
    )
    api_token: str | None = field(default_factory=lambda: os.environ.get("L7_API_TOKEN"))
    # 机主画像：用于把科学参考区间细化到年龄/性别档。仅本机使用，不出服务器。
    owner_age: int | None = field(
        default_factory=lambda: int(os.environ["L7_OWNER_AGE"])
        if os.environ.get("L7_OWNER_AGE", "").isdigit()
        else None
    )
    owner_sex: str | None = field(
        default_factory=lambda: os.environ.get("L7_OWNER_SEX") or None
    )
    default_user_id: str = "owner"

    def resolve_api_token(self) -> str:
        if self.api_token:
            return self.api_token
        if self.environment == "local":
            return "dev-local-token"
        raise RuntimeError("L7_API_TOKEN must be set outside the local environment")

    def definition_path(self, key: str) -> Path:
        rel, _ = DEFINITION_FILES[key]
        return Path(self.l6_definitions_dir) / rel


LOCAL_DEFAULTS = Config()
