"""Next.js documentation site crawler.

Handles custom Next.js-based documentation sites that use MDX content
(e.g. tiptap.dev/docs). These sites typically feature:
- ``_next/static/`` asset paths
- ``.mdx-content`` class for the main content area
- A ``sitemap.xml`` for page discovery
- Sidebar navigation with structured links
"""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Tag
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..converter import html_to_markdown
from ..models import DocPage, DocSite, SiteType
from .base import BaseCrawler


class NextDocsCrawler(BaseCrawler):
    """Crawls Next.js-based documentation sites with .mdx-content areas.

    Strategy:
    1. **Sitemap** – fetch ``sitemap.xml`` to discover all doc pages.
    2. **Sidebar fallback** – if no sitemap, extract links from the sidebar.
    3. **Per-page extraction** – use ``.mdx-content`` for content, extract
       breadcrumb from JSON-LD or ``data-breadcrumb`` attributes.
    """

    async def crawl(self) -> DocSite:
        async with self._make_client() as client:
            html = await self._fetch(client, self.start_url)
            if not html:
                raise RuntimeError(f"Could not fetch {self.start_url}")

            soup = self._parse_html(html)
            site_title = self._get_site_title(soup)

            # ── URL discovery ──────────────────────────────────────────────
            urls = await self._discover_from_sitemap(client)
            if not urls:
                urls = self._extract_sidebar_links(soup)
            if not urls:
                urls = [self._abs_url(self.start_url)]

            pages = await self._crawl_all(client, urls)

        return DocSite(
            title=site_title,
            base_url=self.base_url,
            site_type=SiteType.NEXTDOCS,
            pages=pages,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Site title
    # ──────────────────────────────────────────────────────────────────────────

    def _get_site_title(self, soup: BeautifulSoup) -> str:
        title_tag = soup.find("title")
        if title_tag:
            raw = title_tag.get_text(strip=True)
            # Typically "Page Title | Site Name" or "Page Title - Site Name"
            for sep in ("|", " - ", " – "):
                if sep in raw:
                    return raw.rsplit(sep, 1)[-1].strip()
            return raw.strip()
        return "Documentation"

    # ──────────────────────────────────────────────────────────────────────────
    # Sitemap-based URL discovery
    # ──────────────────────────────────────────────────────────────────────────

    async def _discover_from_sitemap(self, client: httpx.AsyncClient) -> list[str]:
        """Return all doc URLs under start_url from sitemap.xml."""
        # Try sitemap at the docs root and at the site root
        prefix = self.start_url.rstrip("/")
        candidates = [
            f"{prefix}/sitemap.xml",
            f"{self.base_url}/sitemap.xml",
        ]

        all_locs: list[str] = []
        for sitemap_url in candidates:
            text = await self._fetch(client, sitemap_url)
            if text and "<urlset" in text:
                all_locs = re.findall(r"<loc>(.*?)</loc>", text)
                break

        if not all_locs:
            return []

        # Filter to only URLs under our start prefix
        filtered: list[str] = []
        for loc in all_locs:
            loc = loc.strip().rstrip("/")
            if loc == prefix or loc.startswith(prefix + "/"):
                filtered.append(loc)

        return filtered

    # ──────────────────────────────────────────────────────────────────────────
    # Sidebar link extraction (fallback)
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_sidebar_links(self, soup: BeautifulSoup) -> list[str]:
        """Extract documentation links from the sidebar navigation."""
        # Look for sidebar: a sticky div with overflow-auto that contains links
        sidebar = None
        for div in soup.find_all("div"):
            classes = " ".join(div.get("class", []))
            if "overflow-auto" in classes and "sticky" in classes:
                links = div.find_all("a", href=True)
                if len(links) > 5:
                    sidebar = div
                    break

        if sidebar is None:
            # Fallback: try nav elements
            for nav in soup.find_all("nav"):
                links = nav.find_all("a", href=True)
                if len(links) > 5:
                    sidebar = nav
                    break

        if sidebar is None:
            return []

        seen: set[str] = set()
        urls: list[str] = []
        for a in sidebar.find_all("a", href=True):
            href = a["href"]
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            url = self._abs_url(href)
            if url not in seen and self._is_internal(url):
                seen.add(url)
                urls.append(url)
        return urls

    # ──────────────────────────────────────────────────────────────────────────
    # Breadcrumb extraction
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_breadcrumb(soup: BeautifulSoup) -> list[str]:
        """Extract breadcrumb from JSON-LD or data-breadcrumb attributes."""
        # 1. Try JSON-LD BreadcrumbList
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if data.get("@type") == "BreadcrumbList":
                    items = sorted(data.get("itemListElement", []),
                                   key=lambda x: x.get("position", 0))
                    crumb = [item.get("item", {}).get("name", "") for item in items]
                    crumb = [c for c in crumb if c]
                    if crumb:
                        return crumb
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue

        # 2. Try data-breadcrumb attributes
        crumb_els = soup.select("[data-breadcrumb]")
        if crumb_els:
            sorted_els = sorted(crumb_els, key=lambda e: int(e.get("data-breadcrumb", "0")))
            crumb = [el.get_text(strip=True) for el in sorted_els]
            return [c for c in crumb if c]

        return []

    # ──────────────────────────────────────────────────────────────────────────
    # Page fetching
    # ──────────────────────────────────────────────────────────────────────────

    async def _crawl_all(self, client: httpx.AsyncClient, urls: list[str]) -> list[DocPage]:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Crawling Next.js docs…", total=len(urls))

            async def _fetch_one(url: str, order: int) -> DocPage | None:
                if url in self._visited:
                    progress.advance(task)
                    return None
                self._visited.add(url)
                html = await self._fetch(client, url)
                if not html:
                    progress.advance(task)
                    return None
                soup = self._parse_html(html)
                title, content = self._extract_content(soup)
                breadcrumb = self._extract_breadcrumb(soup)
                progress.advance(task)
                if not content.strip():
                    return None
                return DocPage(
                    url=url, title=title, content=content,
                    breadcrumb=breadcrumb, order=order,
                )

            results = await asyncio.gather(
                *[_fetch_one(url, i) for i, url in enumerate(urls)]
            )

        return [p for p in results if p is not None]

    # ──────────────────────────────────────────────────────────────────────────
    # Content extraction
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_content(self, soup: BeautifulSoup) -> tuple[str, str]:
        """Return (title, markdown_content) from a Next.js docs page."""
        # Title: prefer h1#page-title or any h1, then <title> tag
        title = ""
        h1 = soup.select_one("h1#page-title") or soup.find("h1")
        if h1 and isinstance(h1, Tag):
            title = h1.get_text(strip=True)

        if not title:
            title_tag = soup.find("title")
            if title_tag:
                raw = title_tag.get_text(strip=True)
                title = raw.split("|")[0].split(" - ")[0].strip()

        # Content: .mdx-content, then fall back to main, article
        content_el = (
            soup.select_one(".mdx-content")
            or soup.select_one("main article")
            or soup.select_one("article")
            or soup.select_one("main")
        )

        if not content_el:
            return title, ""

        # Remove non-content elements
        for sel in (
            "nav", "header", "footer", ".sidebar", ".toc",
            "button", "[aria-label='Copy']", "[aria-label='Copy markdown']",
            ".feedback", "[class*='feedback']",
        ):
            for el in content_el.select(sel):
                el.decompose()

        return title, html_to_markdown(content_el)
