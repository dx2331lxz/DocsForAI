"""Single-file Markdown exporter.

Concatenates all pages into one ``.md`` file with a generated table of
contents.  Ideal for dropping directly into an LLM context window.
"""
from __future__ import annotations

from pathlib import Path

from ..models import DocSite, DocPage


def _toc_entry(page: DocPage) -> str:
    indent = "  " * max(0, len(page.breadcrumb) - 1)
    anchor = page.title.lower().replace(" ", "-")
    # Strip non-anchor-safe characters
    anchor = "".join(c for c in anchor if c.isalnum() or c in "-_")
    return f"{indent}- [{page.title}](#{anchor})"


def export(site: DocSite, output_dir: Path) -> list[Path]:
    """Write a single ``{site_title}.md`` file; return it as a one-element list."""
    output_dir.mkdir(parents=True, exist_ok=True)

    pages = sorted(site.pages, key=lambda p: p.order)

    lines: list[str] = [
        f"# {site.title}\n",
        f"> Source: {site.base_url}  \n",
        f"> Site type: `{site.site_type.value}`\n",
        "\n---\n",
        "\n## Table of Contents\n",
    ]

    # TOC
    for page in pages:
        lines.append(_toc_entry(page))
    lines.append("\n---\n")

    # Content
    for page in pages:
        depth = len(page.breadcrumb)
        heading = "#" * min(depth + 1, 4) if depth else "##"
        lines.append(f"\n{heading} {page.title}\n")
        if page.breadcrumb:
            lines.append(f"*{' > '.join(page.breadcrumb)}*\n")
        lines.append(f"\n{page.content}\n")
        lines.append("\n---\n")

    slug = site.title.lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c in "-_") or "docs"
    out_file = output_dir / f"{slug}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    return [out_file]
