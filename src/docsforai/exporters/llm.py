"""LLM-oriented exporters.

``jsonl`` — one JSON object per page; suitable for embedding pipelines,
fine-tuning datasets, and vector database ingestion.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..models import DocSite


def export_jsonl(site: DocSite, output_dir: Path) -> list[Path]:
    """Write ``{site_title}.jsonl`` — one JSON record per page."""
    output_dir.mkdir(parents=True, exist_ok=True)

    slug = site.title.lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c in "-_") or "docs"
    out_file = output_dir / f"{slug}.jsonl"

    rows: list[str] = []
    for page in sorted(site.pages, key=lambda p: p.order):
        record = {
            "source": page.url,
            "title": page.title,
            "breadcrumb": page.breadcrumb,
            "content": page.content,
            "site": site.title,
            "site_type": site.site_type.value,
        }
        rows.append(json.dumps(record, ensure_ascii=False))

    out_file.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return [out_file]
