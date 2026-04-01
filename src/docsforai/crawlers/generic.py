"""Generic documentation site crawler.

Used as a fallback when the site type cannot be identified as VitePress or
Docsify.  Performs a breadth-first crawl starting from the given URL, limits
itself to the same host, and tries common content-area selectors to extract
the main text from each page.
"""
from __future__ import annotations

import asyncio
from collections import deque
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from ..converter import html_to_markdown
from ..models import DocPage, DocSite, SiteType
from .base import BaseCrawler

# Selectors to try for the main content area, in priority order
_CONTENT_SELECTORS = [
    "main article",
    "article",
    ".content",
    ".doc-content",
    ".markdown-body",
    ".page-content",
    "main",
    "#content",
    ".container",
]

# Patterns that suggest a URL is a navigation/utility page rather than docs
_SKIP_PATH_PATTERNS = {"/search", "/404", "/login", "/signup", "/register"}


class GenericCrawler(BaseCrawler):
    """BFS-based fallback crawler for unrecognised documentation sites."""

    def __init__(self, *args, max_pages: int = 200, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.max_pages = max_pages

    async def crawl(self) -> DocSite:
        async with self._make_client() as client:
            pages = await self._bfs_crawl(client)
            site_title = await self._get_title(client)

        return DocSite(
            title=site_title,
            base_url=self.base_url,
            site_type=SiteType.GENERIC,
            pages=pages,
        )

    async def _get_title(self, client: httpx.AsyncClient) -> str:
        html = await self._fetch(client, self.start_url)
        if not html:
            return "Documentation"
        soup = self._parse_html(html)
        tag = soup.find("title")
        return tag.get_text(strip=True).split("|")[0].strip() if tag else "Documentation"

    async def _bfs_crawl(self, client: httpx.AsyncClient) -> list[DocPage]:
        queue: deque[tuple[list[str], str]] = deque()
        queue.append(([], self._abs_url(self.start_url)))

        pages: list[DocPage] = []
        order = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Crawling…", total=None)

            while queue and len(pages) < self.max_pages:
                batch = []
                while queue and len(batch) < self.concurrency:
                    batch.append(queue.popleft())

                results = await asyncio.gather(
                    *[self._process_page(client, bc, url, order + i) for i, (bc, url) in enumerate(batch)]
                )
                order += len(batch)

                for page, child_links in results:
                    if page:
                        pages.append(page)
                        progress.advance(task)
                    for link in child_links:
                        if link not in self._visited:
                            queue.append(([], link))

        return pages

    async def _process_page(
        self,
        client: httpx.AsyncClient,
        breadcrumb: list[str],
        url: str,
        order: int,
    ) -> tuple[DocPage | None, list[str]]:
        if url in self._visited or not self._is_internal(url) or self._should_skip(url):
            return None, []
        self._visited.add(url)

        html = await self._fetch(client, url)
        if not html:
            return None, []

        soup = self._parse_html(html)
        child_links = self._collect_links(soup, url)
        title, content = self._extract_content(soup)

        if not content.strip():
            return None, child_links

        return (
            DocPage(url=url, title=title, content=content, breadcrumb=breadcrumb, order=order),
            child_links,
        )

    def _extract_content(self, soup: BeautifulSoup) -> tuple[str, str]:
        content_el = None
        for sel in _CONTENT_SELECTORS:
            content_el = soup.select_one(sel)
            if content_el:
                break

        title = ""
        if content_el:
            h1 = content_el.find("h1")
            title = h1.get_text(strip=True) if h1 else ""

        if not title:
            tag = soup.find("title")
            title = tag.get_text(strip=True).split("|")[0].strip() if tag else ""

        if not content_el:
            return title, ""

        for el in content_el.select("nav, header, footer, .sidebar, .toc"):
            el.decompose()

        return title, html_to_markdown(content_el)

    def _collect_links(self, soup: BeautifulSoup, current_url: str) -> list[str]:
        links: list[str] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href or href.startswith(("mailto:", "javascript:", "#")):
                continue
            abs_url = self._abs_url(href, current_url)
            if abs_url not in seen and self._is_internal(abs_url) and not self._should_skip(abs_url):
                seen.add(abs_url)
                links.append(abs_url)
        return links

    @staticmethod
    def _should_skip(url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(path.startswith(p) for p in _SKIP_PATH_PATTERNS)
