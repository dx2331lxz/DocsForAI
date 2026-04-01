"""Feishu (Lark) Open Platform documentation crawler.

The Feishu open platform docs (open.feishu.cn/document) is a React SPA with
no server-side-rendered navigation.  However, it exposes two clean interfaces:

1. ``GET /api/tools/docment/directory_list``
   Returns a full JSON navigation tree (every section + document, with
   ``fullPath`` and ``id`` for each entry).

2. ``GET /document/<fullpath>.md``
   Returns clean Markdown for any page identified by its tree ``fullPath``.

3. ``GET /api/tools/document/detail?fullPath=<url-path>``
   Resolves a user-facing URL path to the document's canonical tree
   ``fullPath`` and ``directoryId``.

Strategy:
1. Resolve start URL via the detail API -> get ``directoryId``.
2. Fetch the complete navigation tree.
3. Find which top-level section contains that ``directoryId``.
4. Flatten the entire sub-tree of that section into (breadcrumb, fullPath) pairs.
5. Concurrently fetch ``/document/{fullPath}.md`` for every document.
"""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlparse

import httpx
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..models import DocPage, DocSite, SiteType
from .base import BaseCrawler


class FeishuDocsCrawler(BaseCrawler):
    """Crawls open.feishu.cn/document via the internal directory-list API."""

    _DIRECTORY_API = "/api/tools/docment/directory_list"
    _DETAIL_API = "/api/tools/document/detail"

    def __init__(self, *args, max_pages: int = 0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.max_pages = max_pages  # 0 means unlimited

    # -------------------------------------------------------------------------

    async def crawl(self) -> DocSite:
        async with self._make_client() as client:
            # 1. Resolve start URL -> canonical directoryId
            start_path = self._url_to_fullpath(self.start_url)
            dir_id, start_full_path = await self._resolve_document(client, start_path)

            # 2. Fetch the full navigation tree
            tree_items = await self._fetch_tree(client)
            if not tree_items:
                return DocSite(title="飞书开放平台", base_url=self.base_url,
                               site_type=SiteType.FEISHU_DOCS, pages=[])

            # 3. Find the top-level section that contains dir_id / start_full_path
            section, section_crumb, site_title = self._pick_section(
                tree_items, dir_id, start_full_path
            )

            if section is None:
                # Last resort: single-page crawl
                fp = start_full_path or start_path
                md = await self._fetch_md(client, fp)
                if md:
                    title = self._extract_title(md) or "Documentation"
                    page = DocPage(url=self.start_url, title=title,
                                   content=self._clean_md(md), order=0)
                    return DocSite(title=title, base_url=self.base_url,
                                   site_type=SiteType.FEISHU_DOCS, pages=[page])
                return DocSite(title="飞书开放平台", base_url=self.base_url,
                               site_type=SiteType.FEISHU_DOCS, pages=[])

            # 4. Flatten section into (breadcrumb, fullPath) pairs
            flat = self._flatten_tree(section.get("items", []), section_crumb)
            if section.get("type") == "DocumentType" and section.get("fullPath"):
                flat.insert(0, (section_crumb, section["fullPath"]))

            # 5. Respect --max-pages
            if self.max_pages and len(flat) > self.max_pages:
                flat = flat[: self.max_pages]

            # 6. Concurrent Markdown fetches
            pages = await self._crawl_all(client, flat)

        return DocSite(
            title=site_title,
            base_url=self.base_url,
            site_type=SiteType.FEISHU_DOCS,
            pages=pages,
        )

    # -------------------------------------------------------------------------
    # API helpers
    # -------------------------------------------------------------------------

    def _url_to_fullpath(self, url: str) -> str:
        """Extract the path portion after /document from the start URL."""
        parsed = urlparse(url)
        path = parsed.path
        if path.startswith("/document"):
            path = path[len("/document"):]
        if not path:
            path = "/"
        return path

    async def _resolve_document(
        self, client: httpx.AsyncClient, path: str
    ) -> tuple[str | None, str | None]:
        """Call the detail API to get (directoryId, canonical fullPath)."""
        url = f"{self.base_url}{self._DETAIL_API}?fullPath={path}"
        text = await self._fetch(client, url)
        if not text:
            return None, None
        try:
            data = json.loads(text)
            if data.get("code") == 0:
                doc = data["data"]["document"]
                return str(doc.get("directoryId", "")), doc.get("fullPath")
        except (json.JSONDecodeError, KeyError):
            pass
        return None, None

    async def _fetch_tree(self, client: httpx.AsyncClient) -> list | None:
        url = self.base_url + self._DIRECTORY_API
        text = await self._fetch(client, url)
        if not text:
            return None
        try:
            data = json.loads(text)
            if data.get("code") == 0:
                return data["data"]["items"]
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    # -------------------------------------------------------------------------
    # Tree navigation
    # -------------------------------------------------------------------------

    def _pick_section(
        self,
        items: list,
        dir_id: str | None,
        full_path: str | None,
    ) -> tuple[dict | None, list[str], str]:
        """Return (top_section_node, breadcrumb, title).

        Priority:
        1. Find the top-level section whose sub-tree contains *dir_id*.
        2. Fall back to finding the top-level section containing *full_path*.
        """
        if dir_id:
            for top in items:
                if self._subtree_has_id(top, dir_id):
                    return top, [top["name"]], top.get("name", "飞书开放平台")

        if full_path:
            for top in items:
                if self._subtree_has_fullpath(top, full_path):
                    return top, [top["name"]], top.get("name", "飞书开放平台")

        return None, [], "飞书开放平台"

    def _subtree_has_id(self, node: dict, target_id: str) -> bool:
        if str(node.get("id", "")) == target_id:
            return True
        for child in node.get("items", []):
            if self._subtree_has_id(child, target_id):
                return True
        return False

    def _subtree_has_fullpath(self, node: dict, target: str) -> bool:
        if node.get("fullPath") == target:
            return True
        return any(self._subtree_has_fullpath(c, target) for c in node.get("items", []))

    def _flatten_tree(
        self, items: list, crumb: list[str]
    ) -> list[tuple[list[str], str]]:
        """Return [(breadcrumb, fullPath), ...] for every DocumentType leaf."""
        result = []
        for node in items:
            name = node.get("name", "")
            child_crumb = crumb + ([name] if name else [])
            if node.get("type") == "DocumentType" and node.get("fullPath"):
                result.append((child_crumb, node["fullPath"]))
            result.extend(self._flatten_tree(node.get("items", []), child_crumb))
        return result

    # -------------------------------------------------------------------------
    # Page fetching
    # -------------------------------------------------------------------------

    async def _crawl_all(
        self,
        client: httpx.AsyncClient,
        flat: list[tuple[list[str], str]],
    ) -> list[DocPage]:
        pages: list[DocPage | None] = [None] * len(flat)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Crawling Feishu docs...", total=len(flat))

            async def _fetch_one(idx: int, breadcrumb: list[str], fullpath: str) -> None:
                md = await self._fetch_md(client, fullpath)
                if md:
                    title = (self._extract_title(md)
                             or (breadcrumb[-1] if breadcrumb else "Untitled"))
                    pages[idx] = DocPage(
                        url=f"{self.base_url}/document{fullpath}",
                        title=title,
                        content=self._clean_md(md),
                        breadcrumb=breadcrumb,
                        order=idx,
                    )
                progress.advance(task)

            await asyncio.gather(*[
                _fetch_one(i, crumb, fp) for i, (crumb, fp) in enumerate(flat)
            ])

        return [p for p in pages if p is not None]

    async def _fetch_md(self, client: httpx.AsyncClient, fullpath: str) -> str | None:
        """Fetch raw Markdown via /document/<fullpath>.md"""
        url = f"{self.base_url}/document{fullpath}.md"
        return await self._fetch(client, url)

    # -------------------------------------------------------------------------
    # Content helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _extract_title(md: str) -> str:
        m = re.match(r"^#\s+([^\n]+)", md.lstrip())
        return m.group(1).strip() if m else ""

    @staticmethod
    def _clean_md(md: str) -> str:
        # Strip Feishu :::html fenced blocks (table DSL not useful for AI)
        md = re.sub(r":::html\n.*?:::", "", md, flags=re.DOTALL)
        md = re.sub(r"\n{3,}", "\n\n", md)
        return md.strip()
