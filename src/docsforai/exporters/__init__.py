"""Exporters package — dispatch by ExportFormat."""
from __future__ import annotations

from pathlib import Path

from ..models import DocSite, ExportFormat
from . import llm, multi_md, single_md


def export(site: DocSite, output_dir: Path, fmt: ExportFormat) -> list[Path]:
    """Export *site* in the requested *fmt* under *output_dir*."""
    match fmt:
        case ExportFormat.MULTI_MD:
            return multi_md.export(site, output_dir)
        case ExportFormat.SINGLE_MD:
            return single_md.export(site, output_dir)
        case ExportFormat.JSONL:
            return llm.export_jsonl(site, output_dir)
        case _:
            raise ValueError(f"Unknown export format: {fmt}")


__all__ = ["export"]
