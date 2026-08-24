"""Layer 6 REAL model adapters (integration layer, additive to the sealed core).

These are the real DeepSeek V4 Flash and MedGemma 1.5 4B adapters. They are separate from the
sealed `l6_adapters_v0_1.py` (mock adapters) and are only exercised by the standalone
integration runner `l6_real_model_integration_v0_1.py` — never by core acceptance/rebuild.

Credentials come ONLY from environment variables. API keys are never written to files,
databases, logs, or the report.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

from l6_core_v0_1 import CONFIDENCE_LEVELS, HYPOTHESIS_TYPES, canonical_json

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL_DEFAULT = "deepseek-v4-flash"
DEEPSEEK_THINKING = {"type": "disabled"}
DEEPSEEK_AUDIT_LOGGER = logging.getLogger("phe.deepseek.audit")
# Ollama model tag (resolves to medgemma1.5:latest). This is the development-time real MedGemma
# runtime; production can point MEDICAL_MODEL_ENDPOINT at a remote/cloud Ollama-compatible host.
MEDGEMMA_MODEL_DEFAULT = "medgemma1.5"
MEDGEMMA_OLLAMA_ENDPOINT_DEFAULT = "http://localhost:11434"
MEDICAL_MODEL_TIMEOUT_S = 600

DAILY_OUTPUT_SCHEMA_HINT = {
    "primary_hypothesis_type": "string (one of the allowed hypothesis types)",
    "secondary_hypothesis_type": "string or null (optional, only when genuinely close)",
    "confidence": "string (VERY_LOW | LOW | MODERATE | HIGH; must be <= deterministic ceiling)",
    "recommended_actions": ["array of actionable strings"],
    "reasoning_summary": "string (plain language, no statistical jargon)",
}

MEDICAL_OUTPUT_SCHEMA_HINT = {
    "review_status": "APPROVED | APPROVED_WITH_CHANGES | REJECTED | ESCALATE | UNAVAILABLE",
    "medical_concerns": ["array of strings"],
    "causality_concerns": ["array of strings"],
    "missing_safety_considerations": ["array of strings"],
    "unsafe_actions": ["array of strings"],
    "required_changes": ["array of strings"],
    "escalation_reason": "string or null",
    "review_summary": "string",
}

# Native structured-output schema passed to Ollama's `format` parameter (JSON-schema grammar).
# This forces the Medical Critic to emit the L6 medical-review contract shape (enum + arrays),
# rather than relying on fragile string parsing of a free-form response.
MEDICAL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "review_status", "medical_concerns", "causality_concerns",
        "missing_safety_considerations", "unsafe_actions",
        "required_changes", "escalation_reason", "review_summary",
    ],
    "properties": {
        "review_status": {"type": "string", "enum": ["APPROVED", "APPROVED_WITH_CHANGES", "REJECTED", "ESCALATE", "UNAVAILABLE"]},
        "medical_concerns": {"type": "array", "items": {"type": "string"}},
        "causality_concerns": {"type": "array", "items": {"type": "string"}},
        "missing_safety_considerations": {"type": "array", "items": {"type": "string"}},
        "unsafe_actions": {"type": "array", "items": {"type": "string"}},
        "required_changes": {"type": "array", "items": {"type": "string"}},
        "escalation_reason": {"type": ["string", "null"]},
        "review_summary": {"type": "string"},
    },
}

MEDICAL_REVIEW_STATUSES = ("APPROVED", "APPROVED_WITH_CHANGES", "REJECTED", "ESCALATE", "UNAVAILABLE")
MEDICAL_ARRAY_FIELDS = (
    "medical_concerns", "causality_concerns",
    "missing_safety_considerations", "unsafe_actions", "required_changes",
)

# Strict critic instructions. The 4B model rubber-stamps without an explicit rule set; this
# prompt encodes the L6 medical-safety contract (no diagnosis, no unsupported causation, no
# invented facts, low-risk scenarios must not be escalated, thin evidence must not be amplified).
MEDICAL_CRITIC_SYSTEM_PROMPT = (
    "You are a strict Medical Critic / safety reviewer for a personal-health reasoning app. "
    "The app has wearable sensor data (heart rate, sleep, stress) and user-reported context. "
    "It is NOT a diagnostic system: it cannot directly observe heart problems, influenza, COVID, "
    "or any disease, and it must never state a diagnosis. You review medical-safety only; you are "
    "not the main reasoning model and must not re-derive baselines or analytics.\n\n"
    "Review the Medical Review Bundle below and decide whether the proposed AI interpretation "
    "(deepseek_interpretation) and the proposed actions are medically safe and honest.\n\n"
    "Hard rules you MUST apply:\n"
    "1. CAUSALITY: wearable/sleep/stress data only support associations, never causation of a "
    "disease or organ condition. If deepseek_interpretation states that one thing 'caused/导致了' "
    "a disease or organ problem (e.g. '心脏问题', '疾病', 'caused your heart'), flag it in "
    "causality_concerns and do NOT APPROVE - return APPROVED_WITH_CHANGES or REJECTED and put the "
    "required rephrasing in required_changes.\n"
    "2. DIAGNOSIS: If deepseek_interpretation states a definite disease diagnosis (e.g. "
    "'你得了流感/COVID/心脏病/发炎') without clinical evidence, flag it in medical_concerns and "
    "do NOT APPROVE.\n"
    "3. NO INVENTED FACTS: never assert alcohol, medication, symptoms, a specific measured body "
    "temperature, HRV values, training, or disease history that are absent from the bundle. "
    "Mention absent facts only conditionally (e.g. 'if there is a fever, seek evaluation').\n"
    "4. INSUFFICIENT EVIDENCE: if evidence is thin, do not raise certainty and do not invent a "
    "conclusion; state that more information is needed.\n"
    "5. ESCALATION: use ESCALATE only for genuinely urgent red-flag safety (e.g. high fever with "
    "severe symptoms, chest pain, difficulty breathing); otherwise prefer APPROVED or "
    "APPROVED_WITH_CHANGES and list the required rephrasing in required_changes.\n"
    "6. LOW-RISK SCENARIOS: a mild recovery/sleep deficit with no severe symptoms is not an "
    "emergency; do not upgrade it to a severe medical risk and do not ESCALATE it without an "
    "urgent, bundle-supported reason.\n\n"
    "Write your findings in clear English; when you critique a phrase, quote the exact phrase from "
    "the bundle. Fill every field; use empty arrays [] when there is nothing to report and use "
    "null for escalation_reason when you do not escalate."
)

# MedGemma (Gemma-3 lineage) can surface an internal reasoning span delimited by these special
# tokens. The production contract forbids saving or surfacing it, so it is stripped as a
# versioned, tested adapter normalization step (in addition to sending think=false).
THINKING_TRACE_RE = re.compile(r"<unused94>.*?<unused95>", re.DOTALL)


def strip_thinking_trace(text):
    """Remove MedGemma thinking spans and stray special delimiters. Returns clean text.

    Defensive versioned normalization: the adapter requests think=false and structured output,
    which should never emit a thinking span; if one leaks through, it is removed here rather
    than ever reaching health output, provenance, or the integration report.
    """
    if text is None:
        return ""
    cleaned = THINKING_TRACE_RE.sub(" ", text)
    cleaned = cleaned.replace("<unused94>", "").replace("<unused95>", "")
    return cleaned


def normalize_medical_output(payload):
    """Versioned, testable post-schema normalization of a Medical Review object.

    Only performs cosmetic normalization (trim whitespace; empty-string escalation_reason ->
    null; ensure array fields stay lists). It does NOT repair a schema violation into a hidden
    PASS - the caller still runs strict schema validation after this step.
    """
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    for key in MEDICAL_ARRAY_FIELDS:
        if isinstance(out.get(key), list):
            out[key] = [s.strip() if isinstance(s, str) else s for s in out[key]]
    if out.get("review_summary") is not None and isinstance(out["review_summary"], str):
        out["review_summary"] = out["review_summary"].strip()
    if out.get("escalation_reason") == "":
        out["escalation_reason"] = None
    if out.get("review_status") == "":
        out["review_status"] = None
    return out


class RealModelError(Exception):
    pass


class RealModelUnavailable(RealModelError):
    pass


def _extract_json(text):
    """Robustly extract the first JSON object from a model response (handles fences)."""
    if not text:
        raise RealModelError("empty model response")
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # drop the opening fence line (``` or ```json)
        lines = lines[1:] if lines and lines[0].startswith("```") else lines
        # drop trailing closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RealModelError("no JSON object found in model response")
    return json.loads(stripped[start : end + 1])


def _post_json(url, payload, api_key, timeout_s=120):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise RealModelError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RealModelError(f"network error: {exc.reason}") from exc


class RealDeepSeekReasoningModelAdapter:
    model_id = DEEPSEEK_MODEL_DEFAULT

    def __init__(self, api_key=None, model=None, base_url=None):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", DEEPSEEK_MODEL_DEFAULT)
        self.model_id = self.model
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL)).rstrip("/")
        self.last_usage = None
        self.last_invocation = None

    def _require_key(self):
        if not self.api_key:
            raise RealModelUnavailable("DEEPSEEK_API_KEY is not set")

    def _require_flash_model(self):
        if self.model != DEEPSEEK_MODEL_DEFAULT:
            raise RealModelUnavailable(
                f"DeepSeek production model must be {DEEPSEEK_MODEL_DEFAULT!r}; got {self.model!r}"
            )

    def _chat(self, system, user, operation):
        self._require_flash_model()
        self._require_key()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "thinking": DEEPSEEK_THINKING,
            "response_format": {"type": "json_object"},
        }
        endpoint = f"{self.base_url}/chat/completions"
        response = _post_json(endpoint, payload, self.api_key)
        response_model = response.get("model")
        if response_model != DEEPSEEK_MODEL_DEFAULT:
            raise RealModelUnavailable(
                f"DeepSeek response model must be {DEEPSEEK_MODEL_DEFAULT!r}; got {response_model!r}"
            )
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RealModelError(f"unexpected DeepSeek response shape: {response}") from exc
        self.last_usage = response.get("usage") or {}
        self.last_invocation = {
            "event": "deepseek_invocation",
            "operation": operation,
            "requested_model": self.model,
            "response_model": response_model,
            "thinking": "disabled",
            "usage": self.last_usage,
        }
        DEEPSEEK_AUDIT_LOGGER.info(
            "deepseek_invocation %s",
            json.dumps(self.last_invocation, sort_keys=True, separators=(",", ":")),
        )
        return content

    def _daily_system(self, candidates):
        types = sorted({c.get("hypothesis_type") for c in candidates} | set(HYPOTHESIS_TYPES))
        return (
            "You are the reasoning layer of a personal health intelligence system. "
            "You reason ONLY from the provided Evidence Bundle; never invent upstream evidence, "
            "never invent user context the user did not report, and never write inference as user fact. "
            "Output STRICT JSON matching this schema: "
            + json.dumps(DAILY_OUTPUT_SCHEMA_HINT, ensure_ascii=False)
            + ". Allowed hypothesis types: "
            + json.dumps(types, ensure_ascii=False)
            + ". Allowed confidence levels: "
            + json.dumps(list(CONFIDENCE_LEVELS), ensure_ascii=False)
            + ". Do not output a health/readiness/wellness score, do not diagnose a disease, "
            "do not claim causation, and write the summary in plain user language without statistical jargon. "
            "If evidence is insufficient, say so instead of guessing."
        )

    def reason_daily(self, bundle, candidates):
        user = canonical_json({"evidence_bundle": bundle, "hypothesis_candidates": candidates})
        content = self._chat(self._daily_system(candidates), user, operation="today")
        return _extract_json(content)

    def answer_question(self, question, bundle, candidates):
        system = self._daily_system(candidates) + " You are answering a user question grounded ONLY in the bundle."
        user = canonical_json({"question": question, "evidence_bundle": bundle, "hypothesis_candidates": candidates})
        content = self._chat(system, user, operation="qna")
        return _extract_json(content)

    def extract_context(self, text, today):
        system = (
            "Extract structured personal-health context events from the user's natural-language text. "
            "Return STRICT JSON: {\"events\": [{\"context_type\": string, \"body_part\": string|null}]}. "
            "context_type must be one of: HIGH_INTENSITY_TRAINING, ALCOHOL_USE, LATE_SLEEP, CAFFEINE, STRESS, "
            "TRAVEL, ILLNESS, SORE_THROAT, FEVER, NASAL_CONGESTION, MEDICATION, FATIGUE, FEELING_GOOD, "
            "DIET_CHANGE, SCHEDULE_CHANGE. Only include fields the user actually said; never invent values."
        )
        content = self._chat(
            system,
            json.dumps({"text": text, "today": today}, ensure_ascii=False),
            operation="context",
        )
        return _extract_json(content).get("events", [])


class RealMedGemmaMedicalModelAdapter:
    """Real MedGemma 1.5 4B Medical Critic over the Ollama HTTP API.

    Transport: Ollama `/api/chat` with `think=false` (suppresses the MedGemma thinking span)
    and `format` set to the L6 medical-review JSON schema (native structured output, which
    grammar-constrains the response to the contract enum + array fields).

    The same adapter serves future production: set MEDICAL_MODEL_MODE=remote and
    MEDICAL_MODEL_ENDPOINT=https://... to point it at a remote/cloud Ollama-compatible host.
    L6 core never depends on the local D:\\Ollama binary.
    """

    model_id = MEDGEMMA_MODEL_DEFAULT

    def __init__(self, mode=None, endpoint=None, model=None, timeout_s=None):
        self.mode = mode or os.environ.get("MEDICAL_MODEL_MODE", "local")
        self.endpoint = (endpoint or os.environ.get("MEDICAL_MODEL_ENDPOINT") or MEDGEMMA_OLLAMA_ENDPOINT_DEFAULT).rstrip("/")
        self.model = model or os.environ.get("MEDGEMMA_MODEL", MEDGEMMA_MODEL_DEFAULT)
        self.timeout_s = timeout_s or MEDICAL_MODEL_TIMEOUT_S
        self.last_meta = None
        self._identity = None

    def _chat_url(self):
        return f"{self.endpoint}/api/chat"

    def _tags_url(self):
        return f"{self.endpoint}/api/tags"

    def _parse_response(self, content):
        """Structured extraction: strip any leaked thinking span, then extract the JSON object."""
        cleaned = strip_thinking_trace(content)
        obj = _extract_json(cleaned)
        return normalize_medical_output(obj)

    def verify_model_identity(self):
        """Verify the Ollama server is actually serving the configured MedGemma model.

        Returns identity metadata (actual name, parameter size, quantization, family, digest)
        or raises RealModelUnavailable when the runtime/model cannot be verified.
        """
        request = urllib.request.Request(self._tags_url(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                tags = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RealModelUnavailable(f"Ollama runtime unreachable at {self.endpoint}: {exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RealModelUnavailable(f"Ollama identity verification failed: {exc}") from exc

        wanted = self.model.split(":")[0]
        for m in tags.get("models", []):
            if m.get("name", "").split(":")[0] == wanted:
                details = m.get("details", {}) or {}
                self._identity = {
                    "name": m.get("name"),
                    "parameter_size": details.get("parameter_size"),
                    "quantization": details.get("quantization_level"),
                    "format": details.get("format"),
                    "family": details.get("family"),
                    "digest": m.get("digest"),
                    "size": m.get("size"),
                    "capabilities": m.get("capabilities", []),
                    "modified_at": m.get("modified_at"),
                }
                return self._identity
        raise RealModelUnavailable(
            f"model {self.model!r} not present in Ollama /api/tags (available: "
            + ", ".join(m.get("name", "?") for m in tags.get("models", []))
            + ")"
        )

    def _chat(self, review_bundle, question_text=None):
        user_obj = {"medical_review_bundle": review_bundle}
        if question_text:
            user_obj["user_question"] = question_text
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": MEDICAL_CRITIC_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False, indent=2)},
            ],
            "stream": False,
            "think": False,
            "format": MEDICAL_OUTPUT_SCHEMA,
            "options": {"temperature": 0, "num_predict": 1200},
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self._chat_url(), data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        t0 = time.time()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                resp = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:1000]
            raise RealModelError(f"Ollama HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RealModelUnavailable(f"Ollama runtime unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RealModelError(f"Ollama call timed out after {self.timeout_s}s") from exc
        except OSError as exc:
            raise RealModelError(f"Ollama transport error: {exc}") from exc

        self.last_meta = {
            "model": resp.get("model"),
            "done_reason": resp.get("done_reason"),
            "eval_count": resp.get("eval_count"),
            "prompt_eval_count": resp.get("prompt_eval_count"),
            "total_duration_ns": resp.get("total_duration"),
            "load_duration_ns": resp.get("load_duration"),
            "latency_ms": int((time.time() - t0) * 1000),
        }
        message = resp.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RealModelError(f"Ollama returned empty message content: {resp}")
        return self._parse_response(content)

    def review(self, review_bundle, hypothesis_types=None, question_text=None):
        """Run a real MedGemma Medical Review. Returns the structured review object."""
        return self._chat(review_bundle, question_text)
