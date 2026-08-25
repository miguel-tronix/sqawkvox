"""SWE-domain guardrails: shared input checks plus a secret scanner.

Ingested web content is *untrusted data* — a scraped docs page can contain
prompt-injection text.  We scan at ingest time (see
:mod:`sqwakvox.domains.swe.ingest`) and additionally redact secrets from
agent output at chat time.
"""

from __future__ import annotations

import re
from typing import Any

from sqwakvox import guardrails as gr
from sqwakvox.domains.base import (
    GuardrailPipeline,
    OutputGuardrailResult,
    standard_validate_input,
)

#: Patterns that look like API keys / tokens in agent output.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9]{16,}\b"),  # OpenAI-style keys
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),  # Slack tokens
)

#: Phrases typical of prompt-injection embedded in (scraped) documents.
_INJECTION_PATTERNS: tuple[str, ...] = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard prior instructions",
    "disregard all previous instructions",
    "forget everything above",
    "new instructions:",
    "you are now",
)


def redact_secrets(text: str) -> str:
    """Replace secret-looking substrings with a placeholder."""
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[SECRET_REDACTED]", out)
    return out


def scan_document_injections(text: str) -> list[str]:
    """Return injection-style phrases found in ingested document text."""
    low = text.lower()
    return [p for p in _INJECTION_PATTERNS if p in low]


def _validate_output(text: str, data_store: dict[str, Any]) -> OutputGuardrailResult:
    """Output PII redaction plus secret redaction."""
    del data_store
    redacted = gr.PIIRedactor.redact_text(text)
    redacted = redact_secrets(redacted)
    warnings: list[str] = []
    if redacted != text:
        warnings.append("Sensitive data (PII or secrets) redacted from agent output.")
    return OutputGuardrailResult(text=redacted, warnings=warnings)


def swe_guardrails() -> GuardrailPipeline:
    return GuardrailPipeline(
        validate_input=standard_validate_input, validate_output=_validate_output
    )
