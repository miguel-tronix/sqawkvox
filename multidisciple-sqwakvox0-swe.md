# Multidisciple Sqwakvox — SWE Document Assistant Architecture

Status: **Phase 1 ✅ implemented · Phase 2 ✅ implemented** · Author: sqwakvox0 · Scope: architecture
for the first non-financial document assistant (Software Engineering), designed so further domains slot in cheaply.

---

## 1. Goal and context

Sqwakvox is currently a **financial** document assistant: load a PDF, Docling parses it, a financial
guardrail pipeline (math verification, PII) wraps an agent grounded in the parsed markdown. We now want
**multiple document assistants** living in one TUI:

1. **Financial** (existing) — tables, sparklines, math cross-validation, calc-stats MCP tools.
2. **Software Engineering (SWE)** — the first new domain. Ingests:
   - **online documentation for software libraries** (URLs / docs sites),
   - **PDFs and EPUBs of software-engineering collections** (e.g. Martin Fowler),
   - and can **store skill files created with it** (the `.agents/skills/*/SKILL.md` convention).
3. **Future domains** — legal, medical, academic, ... must require *no* core rework.

The recent view/presenter/backend split is the enabler: the Celery task layer, the shared Docling
ingest worker, and the per-document agent workers are already domain-agnostic. What is not domain-
agnostic today is a handful of **hard-coded financial assumptions**:

| Layer | Financial assumption baked in today |
|---|---|
| `agent.py` | `STANDARD_SYSTEM_INSTRUCTIONS_TEMPLATE` / `UNIFIED_USER_PROMPT_TEMPLATE` say *"Financial Document Assistant"* |
| `controller.py` | `execute_agent` always runs financial guardrails (math cross-check) |
| `backend/tasks.py` | `build_financial_data_store` / `cross_validate` are financial-only post-parse tasks |
| `renderer.py` | `DocumentRenderPane` hard-codes a *"Financial Data"* table title + sparkline trends |
| `app.py` | one ingest path, one context (`doc.raw_markdown`), one agent dispatch |
| `mcp_servers.json` | one global tool set (calc-stats is financial) |
| `telemetry.py` | `doc_*` / `agent_*` metrics with no domain attribute |

**The core idea:** introduce a `DocumentDomain` abstraction — a declarative, registry-driven "assistant
profile" that parameterises every seam above. Each domain owns its prompts, ingest, post-parse steps,
guardrail pipeline, renderer, tool set, and skills storage. The core (view, presenter, tasks, workers,
agent loop) stays generic and only ever asks *"what does the active document's domain say?"*.

---

## 2. Target architecture at a glance

```
                        View — Textual TUI (app.py)
   sidebar: domain selector · doc source · model · API key · MCP · skills list
   tabs:    per-document tabs, each tagged with its domain_id
                         │  submit + poll (AsyncResult)
                         ▼
                     Presenter (presenter.py)          ← generic; no domain logic
                         │  Redis broker
                         ▼
                 Celery workers (worker_manager.py)
   ┌───────────────────────────────┐     ┌────────────────────────────────────┐
   │ sqwakvox.docling (×1, shared) │     │ sqwakvox.doc<N> (×tabs, per doc)  │
   │  convert_document(domain_id)  │     │  domain_postprocess(domain_id)    │
   │  └→ Docling + domain ingest   │     │  execute_agent(domain_id)          │
   └───────────────────────────────┘     └────────────────────────────────────┘
                         │                    │
                         ▼                    ▼
              Domain registry (domains/)  ┌── Domain MCP tools ──┐
   DOMAINS = { financial, swe, ... }      │ skills / retrieval  │
   each: prompts · ingest · post-parse ·  │ fetch / sqlite /    │
         guardrails · render · tools ·    │ charts / calc-stats │
         skills_dir                       └─────────────────────┘
```

The two-worker topology from the previous change is **kept as-is**: the shared `sqwakvox.docling`
worker converts all documents of every domain (Docling stays loaded once); the per-document agent
workers run post-parse and agent tasks in isolation. The domain is just another task argument.

---

## 3. The core abstraction: `DocumentDomain`

New package `src/sqwakvox/domains/`. The registry is the single extension point for future domains.

### 3.1 Registry (`domains/__init__.py`)

```python
DOMAINS: dict[str, DocumentDomain] = {}
def register(domain: DocumentDomain) -> None: ...
def get_domain(domain_id: str) -> DocumentDomain: ...          # raises KeyError -> default fallback
def auto_detect(source: str, hint: str | None) -> str: ...     # extension/URL rules + user hint
def list_domains() -> list[DocumentDomain]: ...
```

- `financial` and `swe` are registered at import time in `__init__.py`.
- `get_domain` falls back to the `financial` domain for unknown ids so old task messages / queues
  keep working during rollout (backward compatibility).

### 3.2 The domain interface (`domains/base.py`)

```python
@dataclass
class DocumentDomain:
    domain_id: str                       # "swe"
    display_name: str                    # "Software Engineering"
    description: str                     # shown in the TUI selector
    # --- identity / detection ---
    accepts_extensions: tuple[str, ...]  # (".pdf", ".epub", ".md")
    accepts_url: bool                    # True for SWE docs sites
    # --- prompts (replaces hard-coded templates in agent.py) ---
    system_instructions: Template        # per-domain system prompt, receives {{context}}
    user_prompt_template: Template       # fallback when model lacks a system role
    # --- ingest (runs on the shared docling worker) ---
    def pre_convert(self, source: str, options: dict) -> IngestPlan: ...
    def post_convert(self, source: str, docling_result, plan: IngestPlan,
                     is_cancelled) -> StructuredDocument: ...
    # --- post-parse (runs on the per-doc agent worker) ---
    def postprocess(self, doc: StructuredDocument, source: str) -> dict: ...
    # --- context handed to the agent ---
    def build_context(self, doc: StructuredDocument) -> str: ...
    # --- guardrails (input + output stages) ---
    def guardrail_pipeline(self) -> GuardrailPipeline: ...
    # --- rendering in the TUI ---
    def render(self, doc: StructuredDocument) -> str: ...
    # --- tools & skills ---
    mcp_tool_groups: tuple[str, ...]     # keys into mcp_servers.json tool groups
    skills_enabled: bool                 # SWE: True — attach the skills MCP server
    skills_dir: Path | None              # storage root (None = system default)
```

`IngestPlan` (new, `domains/base.py`) decouples "how to prepare a source for Docling" from
"how to post-process its output":

```python
@dataclass
class IngestPlan:
    # One or more concrete inputs for DocumentConverter.convert()
    inputs: list[Any]                    # path | URL | DocumentStream (epub chapters, crawled pages)
    kind: str                            # "single" | "epub" | "site" | "pdf"
    metadata: dict[str, Any]             # source_type, url, language hints, toc...
```

**Why a plan object instead of just calling `converter.convert(source)`?** Docling has no EPUB
support (verified: no epub handler in docling 2.95) and fetches only single pages. SWE sources need
pre-processing (EPUB → chapter streams, docs-site → crawled page list) that must happen *inside* the
shared docling worker, where the models already live. The plan lets the domain tell the generic
`convert_document` task exactly what to feed Docling and how to reassemble the result.

### 3.3 `LoadedDocument` view-side wrapper

`app.py` currently stores `StructuredDocument` per source. Add a small wrapper so every tab carries
its domain:

```python
@dataclass
class LoadedDocument:
    domain_id: str
    structured: StructuredDocument
    source: str
```

`loaded_documents: dict[str, LoadedDocument]` (source → wrapper). All existing lookups
(`doc.file_name`, `doc.raw_markdown`) keep working via `.structured`; the active tab's
`domain_id` comes from the wrapper, so **mixed-domain tabs coexist** (financial PDF + Fowler EPUB).

---

## 4. Changes to the existing seams (per layer)

### 4.1 `backend/tasks.py` — task signatures gain `domain_id`

- `convert_document(source, domain_id, options=None)` — resolves the domain, builds the `IngestPlan`,
  feeds Docling, calls `domain.post_convert`, returns `model_dump()` **plus** `metadata["domain_id"]`.
- **Replace** `build_financial_data_store` + `cross_validate` with one generic
  `domain_postprocess(domain_id, document_dump, source)` that calls `domain.postprocess(doc, source)`
  and returns a domain-agnostic `{"payload": ...}` (financial payload = data store + cross-validation
  results; SWE payload = toc + code-block index + injection scan verdict).
- `execute_agent(..., domain_id)` — the worker resolves the domain to build prompts and the
  guardrail pipeline (see §4.3/§4.4). `domain_id` is a plain string, JSON-safe, no broker impact.

The old financial task names can be kept as thin wrappers over the new generic task for one release
(`build_financial_data_store = domain_postprocess(domain_id="financial", ...)`) so nothing else
breaks.

### 4.2 `presenter.py` — stay generic, just pass the id through

`submit_task` is already generic. Only the typed wrappers change:

- `parse_document(source, domain_id, queue, options=None, ...)`
- `postprocess_document(domain_id, document, queue, ...)` (new wrapper)
- `execute_agent(..., domain_id, ...)`

The view computes `domain_id` (auto-detect + user hint) and the queue (`_active_queue()`,
`_docling_queue()`) exactly as today.

### 4.3 `agent.py` — parameterise the prompts

`render_prompt(model_id, context, prompt)` gains `domain: DocumentDomain`. The two hard-coded
financial templates move into `domains/financial.py`; `AnyAgentOrchestrator` only ever renders
`domain.system_instructions` / `domain.user_prompt_template`. `execute_query` takes
`domain_id` (or the resolved domain) and passes it through. The MCP tool set passed to the agent is
already a parameter — the view now supplies the **domain-filtered** tool list (§4.7).

### 4.4 `controller.py` — domain-driven guardrails

`execute_agent` currently hard-wires: any-guardrail input check → PII → agent → PII → financial math
cross-check → audit log. Refactor into a `GuardrailPipeline` (`domains/base.py`):

```python
@dataclass
class GuardrailPipeline:
    input_stages: list[GuardrailStage]    # AnyGuardrailValidator, PIIRedactor, [SWE: secret scan]
    output_stages: list[GuardrailStage]   # PIIRedactor, [financial: FinancialRuleEngine.cross_check]
    audit: bool = True                    # AuditLogger on every operation (unchanged)
```

- `financial.guardrail_pipeline()` — exactly today's stages (math cross-check included).
- `swe.guardrail_pipeline()` — common stages **plus**:
  - **Ingested-doc injection scan** (on `post_convert`, not per-chat): scraped web docs may contain
    "ignore previous instructions" text; flag/sanitise it before it becomes agent context.
  - **Secret scan** on chat output (API keys, tokens) — reuse `PIIRedactor` patterns; new
    `SecretScanner` in `domains/swe/guardrails.py`.
  - No math stage.

`AgentResult` is unchanged; new stage verdicts flow into the existing `math_discrepancies`-style list
(reuse a generic `warnings: list[str]` on `AgentResult`).

### 4.5 `renderer.py` — dispatch per domain

`DocumentRenderPane.update_document(doc)` becomes
`update_document(doc, domain_id)`:

- `financial` → current behaviour (Unicode tables, sparklines, "Financial Data" title — moved to
  `domains/financial.py` as `render()`).
- `swe` → a code-aware renderer: markdown headings/TOC, fenced code blocks rendered as syntax-
  highlighted blocks (Textual `RichLog` markup), source-type badge (PDF / EPUB / docs site), and an
  optional skills strip (list of skills relevant to the loaded doc). Lives in `domains/swe/render.py`.

### 4.6 `models.py` — no migration needed

`StructuredDocument.metadata: dict` already exists and is the carrier for domain extras:
`metadata["domain_id"]`, `metadata["source_type"]`, `metadata["toc"]`,
`metadata["code_blocks"]`, `metadata["language"]`, `metadata["url"]`. Financial docs keep their
existing shape exactly.

### 4.7 `app.py` — view changes

- **Domain selector** in the sidebar ("Document type: Auto / Financial / Software Engineering"),
  `hint` fed into `auto_detect(source, hint)`; the chosen `domain_id` is stored per source
  (`_doc_domains: dict[str, str]`), so re-loading a source reuses the choice.
- **Load flow** (`_handle_parse` / `_dispatch_parse`): resolve domain → `ensure_worker(doc_queue)` +
  `ensure_docling_worker()` (unchanged) → `parse_document(source, domain_id, queue=docling_queue)`.
  Status bar text becomes domain-aware ("Docling ingest worker ready (queue: sqwakvox.docling)" +
  "Parsing as Software Engineering document…").
- **Switch flow** (`_switch_to_document`): read `LoadedDocument.domain_id`; pass it to the renderer,
  the chat dispatch, and the guardrail/audit path (`document_id` already namespaced by file name).
- **Chat flow** (`_dispatch_agent`): `execute_agent(..., domain_id=active.domain_id)`; build the
  data-store step via `postprocess_document(domain_id, ...)` instead of `build_data_store`.
- **MCP filtering** (see §4.8) and a new **Skills pane** (list skills for the active domain;
  SWE shows `.agents/skills`/`~/.sqwakvox/skills`).
- `_update_ui_state` status text: "Status: Parsing (SWE)…" vs financial wording.

### 4.8 `mcp_servers.json` — per-domain tool groups

Extend the config with an optional `domains` tag on each server; **untagged servers stay global**:

```json
{
  "mcpServers": {
    "calc-stats":   { "...": "...", "domains": ["financial"] },
    "fetch":        { "...": "...", "domains": ["swe"] },
    "skills":       { "command": ".venv/bin/python",
                      "args": ["-m", "sqwakvox.mcp_skills_server"],
                      "domains": ["swe"] },
    "retrieval":    { "...": "...", "domains": ["swe"], "enabled": false },
    "sqlite":       { "...": "..." },
    "ascii-charts": { "...": "..." }
  }
}
```

The view filters `self.mcp_configs` by the active domain before handing them to the agent task
(reuse the existing `_load_mcp_servers` machinery; add a `tool_group` concept only if a server needs
sharing across domains — otherwise the `domains` array is enough).

---

## 5. The SWE domain (first new domain)

### 5.1 Source handling

| Source | Path through `IngestPlan` | Phase |
|---|---|---|
| **PDF** (Fowler-style books, papers) | Docling directly (`kind="pdf"`) | 1 |
| **EPUB** | Docling has **no EPUB support** → `pre_convert` parses with `ebooklib`: walk the spine, convert each chapter XHTML → Markdown (HTML→MD converter, e.g. `html2text` or per-chapter Docling on an HTML `DocumentStream`), concatenate with a generated TOC. `kind="epub"`. | 1 |
| **URL — single docs page** | `converter.convert(url)` (Docling fetches one page) → Markdown. `kind="single"`. | 1 |
| **URL — docs site (multi-page)** | `pre_convert` fetches the root page (or `sitemap.xml`), extracts internal doc links, fetches each (depth + domain constrained, polite rate-limit), converts each page, concatenates into one `StructuredDocument` with a heading-based TOC. `kind="site"`. | 2 |
| **Plain Markdown / text** | Docling accepts markdown/ASCII directly. | 1 |

Docling pipeline option `do_code_enrichment=True` (verified available in docling 2.95) is enabled for
the SWE domain so fenced code blocks / inline code come out enriched.

### 5.2 Post-parse (`swe.postprocess`)

1. **TOC**: walk `#`/`##` headings → `metadata["toc"]`.
2. **Code-block index**: parse fenced blocks from `raw_markdown` → `metadata["code_blocks"]`
   (`[{"language": "python", "start_line": n, "snippet": "…"}]`, capped snippet length).
3. **Language detection** from extensions/content (`metadata["language"]`).
4. **Injection scan** of ingested content (flag/sanitise "ignore previous instructions"-style text).
5. **Size accounting**: if `len(raw_markdown) > CONTEXT_LIMIT` (≈ 60–100k chars), mark
   `metadata["needs_retrieval"] = True` — see §5.4.

### 5.3 SWE system prompt (shape)

```
You are a helpful Software Engineering Document Assistant.
You are grounded in software-engineering reference material (books, library docs, tutorials).
Always ground answers in the document context. When the user asks for code, prefer
snippets from the loaded document; cite section/TOC anchors when you can.
You may create or update reusable skill files with the skills tool — one concern per
skill, concise, with YAML frontmatter (name, description). Never put secrets in skills.
--- DOCUMENT CONTEXT ---
{{ context }}
------------------------
```

Rendered through `domain.system_instructions` / `domain.user_prompt_template` exactly like the
financial ones today (system-role support respected via `ModelProvider.supports_system_role`).

### 5.4 Long books and context

Fowler's books exceed any single context window. Phase 2 introduces **chunked retrieval** behind the
existing `StructuredDocument.metadata`:

- `domains/swe/retrieval.py`: heading-aware chunker (split on `##`), per-document index in SQLite
  **FTS5** (BM25) at `~/.sqwakvox/swe/index.db` (no new infra; sqlite MCP server pattern already
  exists).
- `mcp_retrieval_server.py`: `search_document(document_id, query, k)` tool (same MCP server pattern
  as `mcp_calc_server.py`).
- `swe.build_context(doc)`: returns TOC + first N chunks for docs marked `needs_retrieval`, plus a
  note that the agent must use the `retrieval` tool for the rest. Small docs keep the current
  full-context behaviour.

Phase 1 ships without retrieval (context = full markdown, guard-railed by the size cap warning in
the TUI); the interface (`build_context`) is in place so Phase 2 is additive.

### 5.5 SWE guardrails

- Input: `AnyGuardrailValidator` + `PIIRedactor` (shared).
- Output: `PIIRedactor` + **`SecretScanner`** (new; pattern-based API-key/token detection).
- Ingest: injection scan (above).
- Audit: `AuditLogger` unchanged (document_id + operation + risk score).

### 5.6 SWE rendering

`domains/swe/render.py`: header badge (source type), TOC listing, markdown body with code blocks
rendered via Textual markup, and no financial table formatting (plain tables stay as markdown or get
a simple grid). Charts remain available through the global ascii-charts tool.

---

## 6. Skills subsystem (SWE's differentiator)

### 6.1 Format — reuse the existing convention

Skills are Markdown files with YAML frontmatter, exactly matching the repo's existing
`.agents/skills/*/SKILL.md` files (create-readonly-db-role, git-worktree, prod-push, …):

```markdown
---
name: my-skill
description: One-line, invocation-oriented description of when to use this skill.
---

# My Skill

Procedural body — concise, imperative, no fluff.
```

### 6.2 Storage layout

```
~/.sqwakvox/skills/<skill-name>/SKILL.md     # system default (domain-scoped below)
~/.sqwakvox/skills/swe/<skill-name>/SKILL.md # SWE domain root
.agents/skills/<skill-name>/SKILL.md         # repo-local skills (read-only by default)
```

Resolution order for the SWE domain: repo `.agents/skills` (read-only, already present) → then
`~/.sqwakvox/skills/swe/` (writable). `domain.skills_dir` defaults to this; override via
`SQWAKVOX_SKILLS_DIR`. A `skills.json` index (name → description → path) is maintained alongside for
fast listing without scanning.

### 6.3 `mcp_skills_server.py` — the agent can create/read/update skills during chat

New stdio MCP server (same pattern as `mcp_calc_server.py`; launched via
`.venv/bin/python -m sqwakvox.mcp_skills_server`, registered for the `swe` domain):

| Tool | Behaviour |
|---|---|
| `list_skills()` | names + descriptions (from `skills.json`) |
| `read_skill(name)` | full SKILL.md body |
| `create_skill(name, description, content)` | validates frontmatter + body, writes to the writable root, updates index |
| `update_skill(name, content)` | idempotent edit |
| `delete_skill(name)` | remove from writable root only |
| `search_skills(query)` | grep over names/descriptions |

Server-side validation: frontmatter must be parseable and contain `name`/`description`; body size
capped (e.g. 32 KB); **secret scan** on content before write; name must match `[a-z0-9-]+`.

This is what "store skills files created with it" means concretely: the user asks the SWE assistant
"turn that troubleshooting workflow into a skill", the agent calls `create_skill`, and the file lands
on disk in the standard format — immediately usable by any other agent/tooling that reads that
convention (the repo's own `.agents` skills are already consumed by other tools).

### 6.4 Skills in the TUI

- A **Skills pane** in the sidebar for domains with `skills_enabled`: list from
  `list_skills`-equivalent local call (no agent needed), open a skill to view it.
- Skills are *not* stuffed into the agent prompt (context budget); the agent uses the MCP tools
  on demand. Optional later: auto-attach top-2 relevant skills (by description keyword match) to the
  system prompt — flagged as a Phase 3 tuning item.

---

## 7. Worker / queue impact

- **Shared Docling worker** (`sqwakvox.docling`): unchanged topology. `convert_document` now runs the
  domain's `pre_convert`/`post_convert` inside it, so EPUB/site processing reuses loaded Docling
  models. The docling worker's `DocumentConverter` is configured per call (pipeline options like
  `do_code_enrichment` come from the domain plan).
- **Per-doc agent workers**: unchanged; they run `domain_postprocess` + `execute_agent`.
- **No new queue kinds.** `worker_manager.py` untouched (queue names stay `sqwakvox.docling` /
  `sqwakvox.doc<N>`). The one shared ingest worker already serves every domain.
- `SQWAKVOX_MANAGED_WORKERS=0` bring-your-own-worker path keeps working (tasks carry `domain_id` as
  plain JSON args).

---

## 8. Telemetry

- Add a `domain` attribute to the existing `doc_ingest_*`, `agent_execution_*`,
  `guardrail_*`, `cross_validate_*` counters/histograms (`{"domain": domain_id, ...}`) — backward
  compatible (new attribute, old series unaffected for financial docs).
- New metrics (only when domain registers them, keep base metrics lean):
  - `sqwakvox.swe.ingest.epub_chapters`, `sqwakvox.swe.ingest.site_pages` (counters)
  - `sqwakvox.skills.created`, `sqwakvox.skills.read` (counters)
  - `sqwakvox.retrieval.queries`, `sqwakvox.retrieval.duration` (Phase 2)
- Span names gain the domain: `sqwakvox.swe.document.convert` etc. (keep the old span name for the
  financial domain so dashboards don't break).

---

## 9. Phased rollout

### Phase 1 — SWE MVP (single-domain addition, no core rework)
- `domains/` package: `base.py` (`DocumentDomain`, `IngestPlan`, `GuardrailPipeline`, `LoadedDocument`),
  `financial.py` (move existing prompts/guardrails/render), `swe/` (prompts, render, postprocess).
- Task signature updates: `convert_document(domain_id)`, `domain_postprocess`, `execute_agent(domain_id)`.
- `mcp_skills_server.py` + skills storage (§6) — **this is the SWE differentiator and lands in Phase 1**.
- View: domain selector, per-source domain, renderer dispatch, MCP domain filtering.
- SWE ingest: PDF, single-page URL, EPUB (ebooklib), Markdown. `do_code_enrichment` on.
- Tests: registry, ingest plans (epub extraction with fixture EPUB), skills server (tmp dir),
  routing (domain_id through presenter), renderer per-domain, guardrail pipeline composition.

### Phase 2 — scale & site crawling ✅
- Docs-site crawler (`kind="site"`, sitemap-aware, same-host, depth/page caps) — opt-in via the
  TUI's **"Crawl docs site"** checkbox or `options={"crawl": True}` (auto-detects docs-root URLs).
- Chunked retrieval: heading-aware chunker + SQLite FTS5 (BM25) index at
  `~/.sqwakvox/swe/index.db` + `mcp_retrieval_server.py` (`search_document` /
  `index_info` / `list_indexed_documents`) + `build_context` override for `needs_retrieval` docs
  (TOC + first chunks + search instructions).
- `SecretScanner` on outputs (Phase 1); injection-scan feedback surfaced in the TUI after each
  SWE parse, alongside section/code-block/chunk counts and the retrieval notice.

### Phase 3 — third domain & tuning
- Pick a second new domain (e.g. legal/medical) to prove the registry: expected effort is
  **prompts + guardrails + render + tool group** only — no core changes.
- Skills relevance auto-attach; domain-aware chat-log namespacing (`~/.sqwakvox_chat_logs/<domain>/`);
  per-domain model defaults.

---

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| **Context overflow** with Fowler-scale books | Phase 2 retrieval; Phase 1 size cap with clear TUI warning (`needs_retrieval` flag) |
| **EPUB extraction quality** (chapter HTML edge cases) | ebooklib + HTML→MD conversion; fixture-based tests; graceful per-chapter failure (skip + log, don't fail the whole doc) |
| **Prompt injection via scraped docs** | Ingest-time injection scan; treat docs as untrusted data, not instructions |
| **Skills abuse / secrets in skills** | Secret scan + size cap + frontmatter validation at the skills server; writable root separate from repo skills |
| **Docs-site crawl runaway** | depth/domain caps, rate limiting, sitemap-first, hard page budget per run |
| **Old task messages on queues** | `get_domain` fallback to financial; old task names kept as wrappers for one release |
| **Docling worker doing heavier work per doc** (epub/site) | Concurrency already bounded (2); tasks remain soft-time-limited; crawling is Phase 2 and can move to a separate queue if it ever contends with parsing |
| **Mixed-domain tabs** | domain_id rides on `LoadedDocument` per source; every dispatch reads the active tab's domain — no global "current assistant" state |

---

## 11. Testing strategy

- **Unit**: `test_domains.py` (registry, auto-detect table, fallback), `test_swe_ingest.py`
  (epub fixture → chapters → markdown + TOC; code-block extraction; injection scan),
  `test_skills_server.py` (CRUD against tmp dir, validation, secret scan), guardrail pipeline
  composition per domain.
- **Integration** (existing patterns in `tests/test_app.py` / `test_presenter.py`):
  `_dispatch_parse` with `domain_id="swe"` routes to `sqwakvox.docling` and records the id;
  `_dispatch_agent` passes the active tab's domain; MCP list filtered per domain.
- **E2E (manual/smoke)**: load a Fowler-style EPUB → SWE renderer shows TOC + code blocks →
  ask for a snippet → agent grounds in book → ask "save that as a skill" → SKILL.md appears under
  `~/.sqwakvox/skills/swe/` and shows in the Skills pane.
- **CI**: extend `tests/` as above; `ruff check src/` + `mypy src/` must stay clean (the new
  `domains/` package is plain Python, no new deps beyond `ebooklib` — add to `pyproject.toml`).

---

## 12. File-by-file change summary

**New files**
```
src/sqwakvox/domains/__init__.py        # registry + auto_detect + imports
src/sqwakvox/domains/base.py            # DocumentDomain, IngestPlan, GuardrailPipeline, LoadedDocument
src/sqwakvox/domains/financial.py       # prompts, guardrails, render (moved from core)
src/sqwakvox/domains/swe/__init__.py    # SWEDomain (registration, tool groups, skills config)
src/sqwakvox/domains/swe/ingest.py      # epub parser, site plan, code/TOC extraction
src/sqwakvox/domains/swe/prompts.py     # system instructions
src/sqwakvox/domains/swe/guardrails.py  # SecretScanner, injection scanner
src/sqwakvox/domains/swe/render.py      # SWE renderer
src/sqwakvox/domains/swe/retrieval.py   # Phase 2: chunker + FTS5 index
src/sqwakvox/mcp_skills_server.py       # skills CRUD MCP server
src/sqwakvox/mcp_retrieval_server.py    # Phase 2: retrieval MCP server
```

**Changed files**
```
src/sqwakvox/backend/tasks.py           # domain_id args; domain_postprocess replaces financial tasks
src/sqwakvox/controller.py              # execute_agent uses domain.guardrail_pipeline() + prompts
src/sqwakvox/agent.py                   # templates move to domains; render_prompt(domain)
src/sqwakvox/presenter.py               # typed wrappers gain domain_id
src/sqwakvox/app.py                     # domain selector, LoadedDocument, renderer/MCP dispatch, skills pane
src/sqwakvox/renderer.py                # dispatch to domain.render()
src/sqwakvox/telemetry.py               # domain attribute on metrics + skills metrics
src/sqwakvox/models.py                  # (no structural change; metadata carries domain extras)
mcp_servers.json(.example)              # domains tags + skills server entry
pyproject.toml                          # + ebooklib (Phase 1), later: no new deps for FTS5
tests/                                  # new + updated as per §11
README.md                               # multi-domain docs + SWE usage
```

---

## 13. Decisions to confirm before implementation

1. **Default domain mapping**: `.epub`/URL → SWE, `.pdf` → Financial with a manual override — or make
   the selector always explicit (no silent guessing)?
2. **Skills writable root**: `~/.sqwakvox/skills/swe/` (recommended, machine-scoped) vs repo
   `.agents/skills/` (versionable, but repo coupling).
3. **Financial guardrail/prompt extraction**: move the existing financial templates into
   `domains/financial.py` in the same change (clean) vs keep them in `agent.py` and only add SWE
   ones (smaller diff, debt later). Recommended: move now — it is the whole point of the registry.
4. **Phase 1 EPUB quality bar**: accept `ebooklib` + HTML→MD as Phase 1 with graceful per-chapter
   failure, or defer EPUB to Phase 2 (crawler + retrieval land together)? Recommended: include EPUB
   in Phase 1 — it is a headline SWE source and the code paths are small.
