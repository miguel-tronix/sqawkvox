"""Document-domain abstraction — the per-assistant profile for Sqwakvox.

A :class:`DocumentDomain` parameterises every layer that currently assumes a
financial document assistant: agent prompts, ingest, post-parse processing,
guardrails, rendering, and skills storage.  The view, presenter, Celery task
layer, and workers stay generic and ask "what does the active document's
domain say?".

Adding a new assistant = registering a new ``DocumentDomain`` in
:mod:`sqwakvox.domains` — no core changes required.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jinja2 import Template

from sqwakvox.models import StructuredDocument

if TYPE_CHECKING:
    pass

#: Loose typing for the controller to keep this module importable from the
#: view (which must not pull in the worker-side docling dependency graph).
Controller = Any


@dataclass
class IngestPlan:
    """How a source should be fed to Docling (built inside the worker).

    ``inputs`` is a list of concrete conversion inputs — file paths, URLs, or
    Docling ``DocumentStream`` objects — plus metadata describing how the
    domain should reassemble the result (EPUB chapters, crawled pages, ...).
    """

    kind: str
    inputs: list[Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class InputGuardrailResult:
    """Verdict of a domain's input guardrail stages for a user prompt."""

    safe: bool = True
    blocked_reason: str = ""
    #: Transformed (e.g. redacted) prompt text; ``None`` means unchanged.
    text: str | None = None


@dataclass
class OutputGuardrailResult:
    """Verdict of a domain's output guardrail stages for an agent response."""

    #: Transformed (e.g. redacted) response text; ``None`` means unchanged.
    text: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class GuardrailPipeline:
    """The input/output guardrail hooks a domain runs around the agent call."""

    validate_input: Callable[[str], InputGuardrailResult] | None = None
    validate_output: Callable[[str, dict[str, Any]], OutputGuardrailResult] | None = None


def _default_render(doc: StructuredDocument) -> str:
    """Fallback renderer: filename plus the raw markdown body."""
    return f"[bold]{doc.file_name}[/bold]\n\n{doc.raw_markdown or ''}"


@dataclass
class DocumentDomain:
    """Declarative profile for one document assistant (financial, swe, ...)."""

    domain_id: str
    display_name: str
    description: str
    #: Domain system-prompt / user-prompt templates (receive ``{{context}}``).
    system_instructions: Template | None = None
    user_prompt_template: Template | None = None
    #: Prepare a source for Docling; returns the IngestPlan.  Runs inside the
    #: shared docling worker where the Docling models already live.
    pre_convert: Callable[[str, dict[str, Any]], IngestPlan] | None = None
    #: Convert a planned source into a StructuredDocument (runs in the worker).
    convert: Callable[
        [Controller, str, IngestPlan, Callable[[], bool]], StructuredDocument | None
    ] | None = None
    #: Post-parse processing (runs on the per-document agent worker).
    #: Returns a JSON-serialisable payload, e.g. ``{"data_store": {...}}``.
    postprocess: Callable[[StructuredDocument, str], dict[str, Any]] | None = None
    #: Input/output guardrail pipeline for agent queries.
    guardrail_pipeline: Callable[[], GuardrailPipeline] | None = None
    #: TUI renderer for parsed documents.
    render: Callable[[StructuredDocument], str] | None = None
    #: Whether this domain can author/persist skill files (see
    #: :mod:`sqwakvox.domains.swe.skills`).
    skills_enabled: bool = False
    #: Skills storage root; ``None`` uses ``./skills/<domain_id>`` (cwd).
    skills_dir: str | None = None

    def build_context(self, doc: StructuredDocument) -> str:
        """The context string handed to the agent for this document."""
        return doc.raw_markdown or ""


@dataclass
class LoadedDocument:
    """View-side wrapper: a parsed document plus the domain that parsed it.

    Lets a single TUI session hold tabs from *different* domains at once.
    """

    domain_id: str
    structured: StructuredDocument
    source: str

    @property
    def file_name(self) -> str:
        return self.structured.file_name
