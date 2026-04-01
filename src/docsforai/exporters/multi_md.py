"""Multi-file Markdown exporter.

Writes one ``.md`` file per page, organised in folders that mirror the
documentation hierarchy.  Each file includes a YAML front-matter block with
metadata useful for RAG pipelines.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..models import DocSite, DocPage


def _slugify(text: str) -> str:
    """Convert a string to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-") or "page"


def _frontmatter(page: DocPage) -> str:
    breadcrumb_str = " > ".join(page.breadcrumb) if page.breadcrumb else page.title
    return (
        "---\n"
        f'title: "{page.title.replace(chr(34), chr(39))}"\n'
        f'url: "{page.url}"\n'
        f'breadcrumb: "{breadcrumb_str}"\n'
        f"order: {page.order}\n"
        "---\n\n"
    )


def export(site: DocSite, output_dir: Path) -> list[Path]:
    """Write one Markdown file per page; return the list of written paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for page in sorted(site.pages, key=lambda p: p.order):
        # Build a folder path from the breadcrumb (all parts except the last)
        parts = page.breadcrumb[:-1] if len(page.breadcrumb) > 1 else []
        folder = output_dir.joinpath(*[_slugify(p) for p in parts])
        folder.mkdir(parents=True, exist_ok=True)

        # Filename from the page title (last breadcrumb entry)
        name = _slugify(page.breadcrumb[-1] if page.breadcrumb else page.title or "page")
        file_path = folder / f"{name}.md"

        # Avoid collisions by appending the order index
        if file_path.exists():
            file_path = folder / f"{name}-{page.order}.md"

        file_path.write_text(_frontmatter(page) + page.content + "\n", encoding="utf-8")
        written.append(file_path)

    return written
