"""SWE-domain TUI renderer: source badge, TOC, and code-aware body."""

from __future__ import annotations

from sqwakvox.models import StructuredDocument


def render(doc: StructuredDocument) -> str:
    """Render a software-engineering document in the TUI render pane."""
    md = doc.raw_markdown or ""
    meta = doc.metadata
    source_type = str(meta.get("source_type", "file"))
    code_blocks = meta.get("code_blocks")
    code_count = len(code_blocks) if isinstance(code_blocks, list) else 0
    toc = meta.get("toc")
    toc_items = toc if isinstance(toc, list) else []

    parts: list[str] = [
        f"[bold]{doc.file_name}[/bold]",
        f"[dim]Source: {source_type} · {code_count} code block(s) · "
        f"{len(toc_items)} section(s)[/dim]\n",
    ]

    if toc_items:
        parts.append("[bold underline]Table of Contents[/bold underline]")
        for item in toc_items[:40]:
            if isinstance(item, dict):
                level = int(item.get("level", 1))
                title = str(item.get("title", ""))
            else:
                level, title = 1, str(item)
            parts.append(f"{'  ' * (level - 1)}• {title}")
        parts.append("")

    parts.append(_render_body(md))
    return "\n".join(parts)


def _render_body(md: str) -> str:
    """Markdown body with fenced code blocks visually separated."""
    out: list[str] = []
    in_fence = False
    for line in md.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            out.append("[dim]──── code ────[/dim]" if in_fence else "[dim]──── end code ────[/dim]")
            continue
        out.append(line)
    return "\n".join(out)
