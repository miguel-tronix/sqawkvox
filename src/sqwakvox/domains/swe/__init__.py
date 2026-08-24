"""Software Engineering (SWE) document domain — the first non-financial
assistant.

Ingests PDFs, EPUBs, and online library documentation; post-processes into
TOC + code-block index; runs shared guardrails plus secret redaction; and can
author reusable skill files (see :mod:`sqwakvox.domains.swe.skills`).
"""

from __future__ import annotations

from sqwakvox.domains.base import DocumentDomain
from sqwakvox.domains.swe import ingest
from sqwakvox.domains.swe import render as swe_render
from sqwakvox.domains.swe.guardrails import swe_guardrails
from sqwakvox.domains.swe.prompts import (
    SWE_SYSTEM_INSTRUCTIONS_TEMPLATE,
    SWE_USER_PROMPT_TEMPLATE,
)

SWE_DOMAIN = DocumentDomain(
    domain_id="swe",
    display_name="Software Engineering",
    description=(
        "Software-engineering reference material: library docs, PDF/EPUB books "
        "(e.g. Martin Fowler), and tutorials."
    ),
    system_instructions=SWE_SYSTEM_INSTRUCTIONS_TEMPLATE,
    user_prompt_template=SWE_USER_PROMPT_TEMPLATE,
    pre_convert=ingest.build_ingest_plan,
    convert=ingest.convert,
    postprocess=ingest.postprocess,
    guardrail_pipeline=swe_guardrails,
    render=swe_render.render,
    skills_enabled=True,
)
