"""Domain registry — the single extension point for new document assistants.

Register a new :class:`~sqwakvox.domains.base.DocumentDomain` here and it
appears in the TUI's "Agent Expert Type" selector, gets its own ingest,
guardrails, rendering, and (optionally) skills storage.  No core changes
required for future domains.
"""

from __future__ import annotations

from sqwakvox.domains import financial as _financial
from sqwakvox.domains import swe as _swe
from sqwakvox.domains.base import (
    DocumentDomain,
    GuardrailPipeline,
    IngestPlan,
    InputGuardrailResult,
    LoadedDocument,
    OutputGuardrailResult,
)

__all__ = [
    "DOMAINS",
    "DocumentDomain",
    "GuardrailPipeline",
    "IngestPlan",
    "InputGuardrailResult",
    "LoadedDocument",
    "OutputGuardrailResult",
    "get_domain",
    "list_domains",
    "register",
]

DOMAINS: dict[str, DocumentDomain] = {}


def register(domain: DocumentDomain) -> None:
    """Register a domain under its ``domain_id``."""
    DOMAINS[domain.domain_id] = domain


def get_domain(domain_id: str) -> DocumentDomain:
    """Resolve a domain id; unknown ids fall back to the financial domain.

    The fallback keeps old queued task messages and legacy callers working
    during rollout.
    """
    return DOMAINS.get(domain_id) or DOMAINS["financial"]


def list_domains() -> list[DocumentDomain]:
    """All registered domains, in registration order (financial first)."""
    return list(DOMAINS.values())


register(_financial.FINANCIAL_DOMAIN)
register(_swe.SWE_DOMAIN)
