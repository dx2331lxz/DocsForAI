"""Starlight (Astro) documentation site crawler."""
from __future__ import annotations

import asyncio

import httpx
from bs4 import BeautifulSoup
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..converter import html_to_markdown
from ..models import DocPage, DocSite, SiteType
from .base import BaseCrawler


class StarlightCrawler(BaseCrawler):
    """Crawls Starlight-powered documentation sites (built on Astro).

    Strategy:
    1. Fetch the start page; extract ``nav.sidebar`` which contains all pages
       grouped under ``<details>/<summary>`` section headers.
    2. Concurrently fetch every linked page.
    3. Extract ``[data-pagefind-body]`` (or ``main``) as the content area,
       stripping nav chrome and on-this-page sidebars.
    """

    async def crawl(self) -> DocSite:
        async with self._make_client() as client:
            html = await self._fetch(client, self.start_url)
            if not html:
                raise RuntimeError(f"Could not fetch {self.start_url}")

            soup = self._parse_html(html)
            site_title = self._get_site_title(soup)

            page_url = self.start_url.rstrip("/") + "/"
            flat = self._extract_nav_links(soup, page_url)

            # Ensure the start URL is always included
            start_norm = self._abs_url(page_url)
            if start_norm not in {url for _, url in flat}:
                flat.insert(0, ([], start_norm))

            pages = await self._crawl_all(client, flat)

        return DocSite(
            title=site_title,
            base_url=self.base_url,
            site_type=SiteType.STARLIGHT,
            pages=pages,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Site title
    # ──────────────────────────────────────────────────────────────────────────

    def _get_site_title(self, soup: BeautifulSoup) -> str:
        # Starlight puts the site name in the header logo / site title link
        for sel in (
            ".site-title",
            "header a[rel='home']",
            ".header a",
            "header .sl-flex a",
        ):
            el = soup.select_one(sel)
            if el:
                title = el.get_text(strip=True)
                if title:
                    return title

        # Fallback: <title>
        title_tag = soup.find("title")
        if title_tag:
            text = title_tag.get_text(strip=True)
            for sep in (" - ", " | ", " · "):
                if sep in text:
                    return text.split(sep)[-1].strip()
            return text

        return "Documentation"

    # ──────────────────────────────────────────────────────────────────────────
    # Nav parsing
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_nav_links(
        self,
        soup: BeautifulSoup,
        page_url: str,
    ) -> list[tuple[list[str], str]]:
        """Return ordered ``[(breadcrumb, abs_url), ...]`` from ``nav.sidebar``."""
        sidebar = soup.select_one("nav.sidebar") or soup.select_one("#starlight__sidebar")
        if not sidebar:
            return self._fallback_links(soup, page_url)

        seen: set[str] = set()
        results: list[tuple[list[str], str]] = []
        current_section = ""

        for el in sidebar.find_all(["summary", "a"]):
            if el.name == "summary":
                current_section = el.get_text(strip=True)
            elif el.name == "a":
                href = el.get("href", "")
                if not href or href.startswith("#") or href.startswith("mailto:"):
                    continue
                # Skip external links (GitHub, Discord, etc.)
                if href.startswith("http://") or href.startswith("https://"):
                    if not href.startswith(self.base_url):
                        continue
                    # Absolute internal link → normalise
                    url = href.rstrip("/")
                else:
                    url = self._abs_url(href, page_url)

                if url in seen:
                    continue
                seen.add(url)
                title = el.get_text(strip=True)
                bc = [current_section, title] if current_section else [title]
                results.append((bc, url))

        return results

    def _fallback_links(
        self,
        soup: BeautifulSoup,
        page_url: str,
    ) -> list[tuple[list[str], str]]:
        seen: set[str] = set()
        results: list[tuple[list[str], str]] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            url = self._abs_url(href, page_url)
            if url not in seen and self._is_internal(url):
                seen.add(url)
                results.append(([a.get_text(strip=True)], url))
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
            task = progress.add_task("Crawling Starlight pages…", total=len(flat))

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
        """Return (title, markdown_content) from a Starlight page."""
        # data-pagefind-body is Starlight's designated content region
        content_el = (
            soup.select_one("[data-pagefind-body]")
            or soup.select_one(".sl-markdown-content")
            or soup.select_one("main")
            or soup.select_one("article")
        )

        title = ""
        if content_el:
            h1 = content_el.find("h1")
            if h1:
                title = h1.get_text(strip=True)

        if not title:
            title = breadcrumb[-1] if breadcrumb else ""

        if not content_el:
            return title, ""

        # Remove chrome that doesn't belong in the output
        for sel in (
            "nav",
            "aside",
            ".right-sidebar",
            ".right-sidebar-container",
            ".sl-hidden",
            ".not-content",
            ".edit-this-page",
            ".prev-next",
            "footer",
            ".feedback-prompt",
        ):
            for el in content_el.select(sel):
                el.decompose()

        return title, html_to_markdown(content_el)
