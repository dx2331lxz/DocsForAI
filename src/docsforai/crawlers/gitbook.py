"""GitBook documentation site crawler."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup, Tag
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..converter import html_to_markdown
from ..models import DocPage, DocSite, SiteType
from .base import BaseCrawler

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class GitBookCrawler(BaseCrawler):
    """Crawls GitBook-hosted documentation sites.

    Strategy:
    1. Discover all page URLs via ``sitemap.xml`` (sitemapindex → per-section
       ``sitemap-pages.xml``).  Falls back to sidebar link extraction when
       the sitemap is unavailable.
    2. Concurrently fetch every page.
    3. Extract ``<main>`` content, cleaning up GitBook-specific noise:
       heading anchor icons (``div.hash``), external link arrow SVGs,
       and other UI chrome.
    """

    async def crawl(self) -> DocSite:
        async with self._make_client() as client:
            # Fetch start page for site title and sidebar fallback
            html = await self._fetch(client, self.start_url)
            if not html:
                raise RuntimeError(f"Could not fetch {self.start_url}")

            soup = self._parse_html(html)
            site_title = self._get_site_title(soup)

            # Discover pages via sitemap; fall back to sidebar
            urls = await self._discover_from_sitemap(client)
            if urls:
                flat = self._urls_to_flat(urls)
            else:
                flat = self._extract_nav_links(soup)

            # Ensure the start URL is always included
            start_norm = self.start_url.rstrip("/")
            if start_norm not in {url for _, url in flat}:
                flat.insert(0, ([], start_norm))

            pages = await self._crawl_all(client, flat)

        return DocSite(
            title=site_title,
            base_url=self.base_url,
            site_type=SiteType.GITBOOK,
            pages=pages,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Site title
    # ──────────────────────────────────────────────────────────────────────────

    def _get_site_title(self, soup: BeautifulSoup) -> str:
        # Prefer og:title or <title> (typically "Site Name | Brand")
        for meta in (
            soup.find("meta", property="og:site_name"),
            soup.find("meta", property="og:title"),
        ):
            if meta and meta.get("content"):
                text = meta["content"].strip()
                for sep in (" | ", " - ", " · "):
                    if sep in text:
                        return text.split(sep)[-1].strip()
                return text

        title_tag = soup.find("title")
        if title_tag:
            text = title_tag.get_text(strip=True)
            for sep in (" | ", " - ", " · "):
                if sep in text:
                    return text.split(sep)[-1].strip()
            return text

        return "Documentation"

    # ──────────────────────────────────────────────────────────────────────────
    # Page discovery via sitemap.xml
    # ──────────────────────────────────────────────────────────────────────────

    async def _discover_from_sitemap(
        self, client: httpx.AsyncClient
    ) -> list[str]:
        """Parse sitemap.xml (sitemapindex) and return all page URLs."""
        # Determine the docs root for sitemap URL
        parsed = urlparse(self.start_url)
        # Find the docs base path (e.g. /docs)
        path_parts = parsed.path.rstrip("/").split("/")
        # Heuristic: use the first non-empty path segment as docs root
        docs_root = f"/{path_parts[1]}" if len(path_parts) > 1 and path_parts[1] else ""
        sitemap_url = f"{self.base_url}{docs_root}/sitemap.xml"

        xml_text = await self._fetch(client, sitemap_url)
        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        all_urls: list[str] = []

        # Check if it's a sitemapindex or a urlset
        if root.tag.endswith("sitemapindex"):
            sub_locs = [
                loc.text
                for loc in root.findall("sm:sitemap/sm:loc", _SITEMAP_NS)
                if loc.text
            ]
            # Fetch all sub-sitemaps concurrently
            sub_xmls = await asyncio.gather(
                *[self._fetch(client, loc) for loc in sub_locs]
            )
            for sx in sub_xmls:
                if sx:
                    all_urls.extend(self._parse_urlset(sx))
        else:
            all_urls.extend(self._parse_urlset(xml_text))

        return all_urls

    @staticmethod
    def _parse_urlset(xml_text: str) -> list[str]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        return [
            loc.text.rstrip("/")
            for loc in root.findall("sm:url/sm:loc", _SITEMAP_NS)
            if loc.text
        ]

    def _urls_to_flat(self, urls: list[str]) -> list[tuple[list[str], str]]:
        """Convert a flat list of URLs to ``[(breadcrumb, url), ...]``."""
        parsed_start = urlparse(self.start_url)
        base_path = parsed_start.path.rstrip("/")

        results: list[tuple[list[str], str]] = []
        for url in urls:
            parsed = urlparse(url)
            # Build breadcrumb from the path relative to docs root
            rel_path = parsed.path.rstrip("/")
            if rel_path.startswith(base_path):
                rel_path = rel_path[len(base_path):]
            parts = [p for p in rel_path.strip("/").split("/") if p]
            # Clean up slug to title
            bc = [p.replace("-", " ").replace("_", " ").title() for p in parts]
            results.append((bc, url.rstrip("/")))

        return results

    # ──────────────────────────────────────────────────────────────────────────
    # Fallback: extract links from sidebar
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_nav_links(
        self, soup: BeautifulSoup
    ) -> list[tuple[list[str], str]]:
        """Extract page links from the GitBook sidebar."""
        toc = soup.find(attrs={"data-testid": "toc-scroll-container"})
        container = toc or soup.find("aside")

        if not container:
            return self._fallback_all_links(soup)

        seen: set[str] = set()
        results: list[tuple[list[str], str]] = []

        for a in container.find_all("a", href=True):
            href = a["href"]
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            if href.startswith("http") and not href.startswith(self.base_url):
                continue
            url = self._abs_url(href, self.start_url).rstrip("/")
            if url in seen:
                continue
            seen.add(url)
            title = a.get_text(strip=True)
            # Remove trailing icon text like "chevron-right"
            title = re.sub(r"(chevron-right|chevron-down|chevron-up)$", "", title).strip()
            if title:
                results.append(([title], url))

        return results

    def _fallback_all_links(
        self, soup: BeautifulSoup
    ) -> list[tuple[list[str], str]]:
        seen: set[str] = set()
        results: list[tuple[list[str], str]] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            url = self._abs_url(href, self.start_url).rstrip("/")
            if url not in seen and self._is_internal(url):
                seen.add(url)
                title = a.get_text(strip=True)
                if title:
                    results.append(([title], url))
        return results

    # ──────────────────────────────────────────────────────────────────────────
    # Page fetching
    # ──────────────────────────────────────────────────────────────────────────

    async def _crawl_all(
        self,
        client: httpx.AsyncClient,
        flat: list[tuple[list[str], str]],
    ) -> list[DocPage]:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Crawling GitBook pages…", total=len(flat))

            async def _fetch_one(
                breadcrumb: list[str], url: str, order: int
            ) -> DocPage | None:
                if url in self._visited:
                    progress.advance(task)
                    return None
                self._visited.add(url)
                html = await self._fetch(client, url)
                if not html:
                    progress.advance(task)
                    return None
                soup = self._parse_html(html)
                title, content = self._extract_content(soup, breadcrumb)
                progress.advance(task)
                return DocPage(
                    url=url,
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
    # Content extraction
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_content(
        self, soup: BeautifulSoup, breadcrumb: list[str]
    ) -> tuple[str, str]:
        """Return (title, markdown_content) from a GitBook page."""
        content_el = soup.find("main") or soup.find("article")

        title = ""
        if content_el:
            h1 = content_el.find("h1")
            if h1:
                title = h1.get_text(strip=True)

        if not title:
            title = breadcrumb[-1] if breadcrumb else ""

        if not content_el:
            return title, ""

        # ── GitBook-specific cleanup ─────────────────────────────────────────

        # 1. Remove heading anchor divs (div.hash) that produce "hashtag" text
        for el in content_el.select("div.hash"):
            el.decompose()

        # 2. Remove SVG icons that produce text like "arrow-up-right", "hashtag"
        for svg in content_el.find_all("svg"):
            svg.decompose()

        # 3. Remove navigation / chrome elements
        for sel in (
            "aside",
            "nav",
            "footer",
            "header",
            "script",
            "[data-testid='gb-trademark']",
            "[data-testid='toc-button']",
        ):
            for el in content_el.select(sel):
                el.decompose()

        md = html_to_markdown(str(content_el))
        return title, md
