"""Mintlify documentation site crawler.

Mintlify is a SaaS documentation platform built on Next.js.  Every Mintlify
site exposes two LLM-friendly endpoints:

* ``/llms.txt``      — index of all pages with their raw-Markdown URLs
* ``/llms-full.txt`` — the full content of every page concatenated together

Strategy (fastest path first):
1. Try ``/llms-full.txt`` — if available, parse it into pages in a single
   HTTP request.  No further fetching required.
2. Fall back to ``/llms.txt`` + concurrent individual ``.md`` fetches — still
   much faster than HTML parsing because we get raw Markdown directly.
3. If neither is available, fall back to generic HTML crawling.

Mintlify also serves each page as raw Markdown at ``<url>.md``, so individual
page fetches are always clean and zero-conversion-overhead.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..models import DocPage, DocSite, SiteType
from .base import BaseCrawler

# Regex to split llms-full.txt into individual page sections.
# Each section starts with: # <Title>\nSource: <url>\n
# Use [^\n]+ for the title so it never spans multiple lines even in DOTALL mode.
_FULL_SECTION = re.compile(
    r"^#\s+([^\n]+)\nSource:\s*(https?://\S+)\n(.*?)(?=\n#\s+[^\n]+\nSource:|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Regex to parse a single llms.txt entry: - [Title](url)
_LLM_LINK = re.compile(r"^-\s+\[([^\]]+)\]\((https?://[^)]+\.md)\)", re.MULTILINE)


class MintlifyCrawler(BaseCrawler):
    """Crawls Mintlify-hosted documentation sites via llms.txt / llms-full.txt."""

    async def crawl(self) -> DocSite:
        # llms-full.txt can be large (>1 MB); ensure the timeout is generous.
        self.timeout = max(self.timeout, 60.0)
        base = self._site_base(self.start_url)

        async with self._make_client() as client:
            # Build candidate base URLs to search for llms.txt / llms-full.txt.
            # For docs hosted at a sub-path (e.g. example.com/docs/en/overview)
            # the files may live at any parent path.  Walk up the URL path
            # hierarchy to cover cases like /docs/llms-full.txt.
            candidates: list[str] = []
            p = urlparse(self.start_url)
            path = p.path.rstrip("/")
            while path:
                candidate = f"{p.scheme}://{p.netloc}{path}"
                if candidate not in candidates:
                    candidates.append(candidate)
                path = path.rsplit("/", 1)[0]
            root = f"{p.scheme}://{p.netloc}"
            if root not in candidates:
                candidates.append(root)

            site_title = await self._get_site_title(client, candidates)

            # ── Strategy 1: llms-full.txt (one request, all content) ──────────
            for candidate in candidates:
                full_url = urljoin(candidate.rstrip("/") + "/", "llms-full.txt")
                full_text = await self._fetch(client, full_url)
                if full_text and _FULL_SECTION.search(full_text):
                    pages = self._parse_full_txt(full_text)
                    return DocSite(
                        title=site_title,
                        base_url=base,
                        site_type=SiteType.MINTLIFY,
                        pages=pages,
                    )

            # ── Strategy 2: llms.txt index + individual .md fetches ───────────
            for candidate in candidates:
                index_url = urljoin(candidate.rstrip("/") + "/", "llms.txt")
                index_text = await self._fetch(client, index_url)
                if index_text:
                    flat = self._parse_llms_txt(index_text)
                    if flat:
                        pages = await self._crawl_all(client, flat)
                        return DocSite(
                            title=site_title,
                            base_url=base,
                            site_type=SiteType.MINTLIFY,
                            pages=pages,
                        )

        # ── Strategy 3: give up and return empty (caller falls back to generic) ─
        return DocSite(
            title=site_title,
            base_url=base,
            site_type=SiteType.MINTLIFY,
            pages=[],
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _site_base(url: str) -> str:
        """Strip path, keep scheme + host."""
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    @staticmethod
    def _url_dir(url: str) -> str:
        """Return the directory portion of a URL (scheme + host + path, no trailing slash on leaf)."""
        p = urlparse(url)
        path = p.path.rstrip("/")
        return f"{p.scheme}://{p.netloc}{path}"

    async def _get_site_title(self, client: httpx.AsyncClient, candidates: list[str]) -> str:
        """Parse site title from llms.txt first line (# SiteName) or <title> tag."""
        for candidate in candidates:
            index_url = urljoin(candidate.rstrip("/") + "/", "llms.txt")
            text = await self._fetch(client, index_url)
            if text:
                first_line = text.strip().splitlines()[0]
                if first_line.startswith("# "):
                    return first_line[2:].strip()
        # Fallback: HTML title tag from start page
        html = await self._fetch(client, self.start_url)
        if html:
            soup = self._parse_html(html)
            tag = soup.find("title")
            if tag:
                return tag.get_text(strip=True).split("|")[0].split(" - ")[0].strip()
        return "Documentation"

    # ──────────────────────────────────────────────────────────────────────────
    # Strategy 1 — parse llms-full.txt
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_full_txt(self, text: str) -> list[DocPage]:
        """Split the monolithic llms-full.txt into individual DocPage objects."""
        pages: list[DocPage] = []
        for order, m in enumerate(_FULL_SECTION.finditer(text)):
            title = m.group(1).strip()
            source_url = m.group(2).strip()
            content = m.group(3).strip()

            # Derive breadcrumb from URL path segments
            breadcrumb = self._breadcrumb_from_url(source_url)

            # Remove the metadata header injected by Mintlify
            # ("> ## Documentation Index\n> Fetch the complete documentation ...")
            content = re.sub(r"^>.*?\n\n", "", content, flags=re.DOTALL)
            # Strip code-block theme metadata: ```lang theme={...}  →  ```lang
            content = re.sub(r"(```\w*)\s+theme=\{[^}]*\}", r"\1", content)

            pages.append(DocPage(
                url=source_url.replace(".md", ""),
                title=title,
                content=content,
                breadcrumb=breadcrumb,
                order=order,
            ))
        return pages

    # ──────────────────────────────────────────────────────────────────────────
    # Strategy 2 — parse llms.txt index + fetch individual pages
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_llms_txt(self, text: str) -> list[tuple[list[str], str]]:
        """Return [(breadcrumb, md_url), ...] from llms.txt."""
        results: list[tuple[list[str], str]] = []
        for m in _LLM_LINK.finditer(text):
            title = m.group(1).strip()
            md_url = m.group(2).strip()
            breadcrumb = self._breadcrumb_from_url(md_url)
            if not breadcrumb or breadcrumb[-1] != title:
                breadcrumb = breadcrumb[:-1] + [title] if breadcrumb else [title]
            results.append((breadcrumb, md_url))
        return results

    async def _crawl_all(
        self,
        client: httpx.AsyncClient,
        flat: list[tuple[list[str], str]],
    ) -> list[DocPage]:
        import asyncio

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Crawling Mintlify pages…", total=len(flat))

            async def _fetch_one(breadcrumb: list[str], md_url: str, order: int) -> DocPage | None:
                if md_url in self._visited:
                    progress.advance(task)
                    return None
                self._visited.add(md_url)
                content = await self._fetch(client, md_url)
                progress.advance(task)
                if content is None:
                    return None
                # Strip Mintlify metadata header
                content = re.sub(r"^>.*?\n\n", "", content.strip(), flags=re.DOTALL)
                # Strip code-block theme metadata: ```lang theme={...}  →  ```lang
                content = re.sub(r"(```\w*)\s+theme=\{[^}]*\}", r"\1", content)
                title = self._extract_title(content) or (breadcrumb[-1] if breadcrumb else "")
                return DocPage(
                    url=md_url.replace(".md", ""),
                    title=title,
                    content=content,
                    breadcrumb=breadcrumb,
                    order=order,
                )

            results = await asyncio.gather(
                *[_fetch_one(bc, url, i) for i, (bc, url) in enumerate(flat)]
            )

        return [p for p in results if p is not None]

    # ──────────────────────────────────────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────────────────────────────────────

    def _breadcrumb_from_url(self, url: str) -> list[str]:
        """Convert ``https://docs.site.com/guide/getting-started.md``
        to ``["guide", "getting-started"]``.
        """
        path = urlparse(url).path.rstrip("/")
        # Remove extension
        path = re.sub(r"\.\w+$", "", path)
        parts = [p for p in path.split("/") if p]
        return [p.replace("-", " ").title() for p in parts]

    @staticmethod
    def _extract_title(md: str) -> str:
        for line in md.splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
        return ""
