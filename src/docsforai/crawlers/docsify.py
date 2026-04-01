"""Docsify documentation site crawler.

Docsify is special: it renders Markdown files client-side, meaning the raw
``.md`` source files are always available directly on the server.  This crawler
fetches them without any HTML-to-Markdown conversion.

Strategy:
1. Fetch ``_sidebar.md`` to get the navigation tree.
2. Derive the raw ``.md`` URL for each page referenced in the sidebar.
3. Fetch all ``.md`` files concurrently and assemble them into DocPage objects.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse

import httpx
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..models import DocPage, DocSite, NavItem, SiteType
from .base import BaseCrawler


class DocsifyCrawler(BaseCrawler):

    async def crawl(self) -> DocSite:
        async with self._make_client() as client:
            # Normalise: strip hash routing fragment (/#/guide → /)
            base = self._docsify_base(self.start_url)
            sidebar_url = urljoin(base + "/", "_sidebar.md")
            sidebar_md = await self._fetch(client, sidebar_url)

            nav: list[NavItem] = []
            if sidebar_md:
                nav = self._parse_sidebar_md(sidebar_md, base)

            flat = self._flatten_nav(nav)

            # If no sidebar, crawl the root page at least
            if not flat:
                flat = [([], base + "/README.md")]

            site_title = await self._get_site_title(client, base)
            pages = await self._crawl_all(client, flat, base)

        return DocSite(
            title=site_title,
            base_url=self.base_url,
            site_type=SiteType.DOCSIFY,
            pages=pages,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _docsify_base(url: str) -> str:
        """Strip hash routing from Docsify URLs.

        ``https://example.com/#/guide`` → ``https://example.com``
        """
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    async def _get_site_title(self, client: httpx.AsyncClient, base: str) -> str:
        """Try to extract the site title from the Docsify index.html."""
        html = await self._fetch(client, base + "/")
        if not html:
            return "Documentation"
        soup = self._parse_html(html)
        # Docsify stores name in $docsify config or <title>
        for script in soup.find_all("script"):
            inline = script.string or ""
            m = re.search(r'name\s*:\s*["\']([^"\']+)["\']', inline)
            if m:
                return m.group(1)
        title = soup.find("title")
        if title:
            return title.get_text(strip=True)
        return "Documentation"

    # ──────────────────────────────────────────────────────────────────────────
    # Sidebar parsing
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_sidebar_md(self, md: str, base: str) -> list[NavItem]:
        """Parse Docsify ``_sidebar.md`` into a NavItem tree.

        Supports nested lists of the form:
        ```
        - [Title](path)
          - [Sub](path/sub)
        ```
        """
        lines = md.splitlines()
        root: list[NavItem] = []
        stack: list[tuple[int, list[NavItem]]] = [(-1, root)]  # (indent, container)

        for line in lines:
            # Skip HTML comments and empty lines
            stripped = line.rstrip()
            if not stripped or stripped.startswith("<!--"):
                continue

            # Measure indent level (2 spaces or 1 tab = 1 level)
            indent = len(line) - len(line.lstrip())
            level = indent // 2

            # Match markdown list item with optional link
            m = re.match(r"\s*[-*]\s+(?:\[([^\]]*)\]\(([^)]*)\)|(.+))", stripped)
            if not m:
                continue

            if m.group(1) is not None:
                title = m.group(1).strip()
                path = m.group(2).strip()
                url = self._resolve_docsify_path(path, base) if path and path != "/" else ""
            else:
                title = m.group(3).strip()
                url = ""

            if not title:
                continue

            item = NavItem(title=title, url=url, level=level)

            # Pop stack until we find the correct parent
            while len(stack) > 1 and stack[-1][0] >= level:
                stack.pop()

            stack[-1][1].append(item)
            stack.append((level, item.children))

        return root

    def _resolve_docsify_path(self, path: str, base: str) -> str:
        """Convert a Docsify sidebar path to an absolute URL for the raw .md file."""
        # Strip leading slashes / hash routing prefix
        path = path.lstrip("/#")
        if not path:
            return urljoin(base + "/", "README.md")

        # Remove any existing .md extension before we re-add it
        if path.endswith(".md"):
            raw_path = path
        elif path.endswith("/"):
            raw_path = path + "README.md"
        else:
            raw_path = path + ".md"

        return urljoin(base + "/", raw_path)

    # ──────────────────────────────────────────────────────────────────────────
    # Flattening + fetching
    # ──────────────────────────────────────────────────────────────────────────

    def _flatten_nav(
        self,
        items: list[NavItem],
        breadcrumb: list[str] | None = None,
    ) -> list[tuple[list[str], str]]:
        crumb = breadcrumb or []
        result: list[tuple[list[str], str]] = []
        for item in items:
            child_crumb = crumb + ([item.title] if item.title else [])
            if item.url:
                result.append((child_crumb, item.url))
            if item.children:
                result.extend(self._flatten_nav(item.children, child_crumb))
        return result

    async def _crawl_all(
        self,
        client: httpx.AsyncClient,
        flat: list[tuple[list[str], str]],
        base: str,
    ) -> list[DocPage]:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Crawling Docsify pages…", total=len(flat))

            async def _fetch_one(breadcrumb: list[str], url: str, order: int) -> DocPage | None:
                if url in self._visited:
                    progress.advance(task)
                    return None
                self._visited.add(url)
                md = await self._fetch(client, url)
                if md is None:
                    progress.advance(task)
                    return None
                title = self._extract_title(md) or (breadcrumb[-1] if breadcrumb else "")
                progress.advance(task)
                return DocPage(url=url, title=title, content=md, breadcrumb=breadcrumb, order=order)

            results = await asyncio.gather(
                *[_fetch_one(bc, url, i) for i, (bc, url) in enumerate(flat)]
            )

        return [p for p in results if p is not None]

    @staticmethod
    def _extract_title(md: str) -> str:
        """Return the first H1 heading in a Markdown file, or empty string."""
        for line in md.splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
        return ""
