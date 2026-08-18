"""Import seam for the SEALED Layer 6 code.

L7 reuses L6 deterministic logic strictly by importing the sealed modules — never by
copying, editing, or re-implementing them. The L6 scripts directory is placed on
`sys.path` once; all imports are plain module imports.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def ensure_l6_on_path(l6_code_dir: str) -> None:
    p = str(Path(l6_code_dir).resolve())
    if p not in sys.path:
        sys.path.insert(0, p)


class L6Bridge:
    """Lazily-imported handles to the sealed L6 modules."""

    def __init__(self, l6_code_dir: str):
        ensure_l6_on_path(l6_code_dir)
        self.core = importlib.import_module("l6_core_v0_1")
        self.evidence = importlib.import_module("l6_evidence_v0_1")
        self.adapters = importlib.import_module("l6_adapters_v0_1")
        self.materializer = importlib.import_module("l6_reasoning_materializer_v0_1")
        # Real adapters are imported lazily so an absent optional dependency or missing
        # credential never breaks the deterministic default path.
        self._real = None

    @property
    def real_adapters(self):
        if self._real is None:
            self._real = importlib.import_module("l6_real_adapters_v0_1")
        return self._real

    # Convenience proxies -------------------------------------------------
    def load_definition(self, path, expected_id):
        return self.core.load_definition(Path(path), expected_id)

    def assemble_evidence(self, l3, l4, l5, analysis_date, recent_context, recent_feedback, similar_cases):
        return self.evidence.assemble_evidence(
            l3, l4, l5, analysis_date, recent_context, recent_feedback, similar_cases
        )

    def bundle_sha256(self, bundle):
        return self.evidence.bundle_sha256(bundle)
