#!/usr/bin/env python3
"""Static audit for active PHE DeepSeek production code and configuration."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
FLASH_MODEL = "deepseek-v4-flash"
FORBIDDEN_TOKENS = (
    "deepseek-v4-pro",
    "DEEPSEEK_REASONING_EFFORT",
    "reasoning_effort",
)
ACTIVE_ROOTS = (
    "PersonalHealthEngine-L6/scripts",
    "PersonalHealthEngine-L6/.env.example",
    "PersonalHealthEngine-L7/backend/l7",
    "PersonalHealthEngine-L7/backend/scripts",
    "PersonalHealthEngine-L7/L7_TECHNICAL_ARCHITECTURE.md",
    "deployment",
)
ACTIVE_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".service", ".timer", ".example"}


def _active_paths(root: Path = REPO_ROOT) -> list[Path]:
    paths: list[Path] = []
    for relative in ACTIVE_ROOTS:
        candidate = root / relative
        if candidate.is_file():
            paths.append(candidate)
        elif candidate.is_dir():
            paths.extend(
                path for path in candidate.rglob("*")
                if path.is_file()
                and path.suffix in ACTIVE_SUFFIXES
                and path.name != Path(__file__).name
                and "__pycache__" not in path.parts
            )
    return sorted(set(paths))


def scan_paths(paths: list[Path]) -> list[dict]:
    findings: list[dict] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for token in FORBIDDEN_TOKENS:
                if token in line:
                    findings.append({
                        "path": str(path),
                        "line": line_number,
                        "token": token,
                    })
    return findings


def audit_active_paths(root: Path = REPO_ROOT) -> dict:
    findings = scan_paths(_active_paths(root))
    real_adapter = (
        root / "PersonalHealthEngine-L6/scripts/l6_real_adapters_v0_1.py"
    ).read_text(encoding="utf-8")
    required_fragments = (
        f'DEEPSEEK_MODEL_DEFAULT = "{FLASH_MODEL}"',
        'DEEPSEEK_THINKING = {"type": "disabled"}',
        '"thinking": DEEPSEEK_THINKING',
    )
    for fragment in required_fragments:
        if fragment not in real_adapter:
            findings.append({
                "path": "PersonalHealthEngine-L6/scripts/l6_real_adapters_v0_1.py",
                "line": None,
                "token": f"missing required fragment: {fragment}",
            })
    return {
        "status": "PASS" if not findings else "FAIL",
        "model": FLASH_MODEL,
        "thinking": "disabled",
        "pro_production_references": sum(
            finding["token"] == FORBIDDEN_TOKENS[0] for finding in findings
        ),
        "findings": findings,
    }


def main() -> int:
    report = audit_active_paths()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] == "PASS":
        print("DEEPSEEK MODEL AUDIT = PASS")
        print("DEEPSEEK V4 FLASH = ACTIVE")
        print("DEEPSEEK V4 PRO PRODUCTION CALLS = 0")
        print("DEEPSEEK THINKING MODE = DISABLED")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
