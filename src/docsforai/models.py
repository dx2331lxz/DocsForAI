"""Data models for DocsForAI."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SiteType(str, Enum):
    VITEPRESS = "vitepress"
    DOCSIFY = "docsify"
    MINTLIFY = "mintlify"
    GENERIC = "generic"


class ExportFormat(str, Enum):
    MULTI_MD = "multi-md"
    SINGLE_MD = "single-md"
    JSONL = "jsonl"


@dataclass
class NavItem:
    """A single item in the documentation navigation tree."""
    title: str
    url: str
    level: int = 0
    children: list[NavItem] = field(default_factory=list)


@dataclass
class DocPage:
    """A single documentation page with Markdown content and metadata."""
    url: str
    title: str
    content: str
    breadcrumb: list[str] = field(default_factory=list)
    order: int = 0


@dataclass
class DocSite:
    """The complete scraped documentation site."""
    title: str
    base_url: str
    site_type: SiteType
    pages: list[DocPage] = field(default_factory=list)
    description: str = ""
