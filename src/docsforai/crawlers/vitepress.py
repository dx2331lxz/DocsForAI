"""VitePress documentation site crawler."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup, Tag
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..converter import html_to_markdown
from ..models import DocPage, DocSite, NavItem, SiteType
from .base import BaseCrawler


class VitePressCrawler(BaseCrawler):
    """Crawls VitePress-generated static documentation sites.

    Strategy:
    1. Fetch the start page; extract the rendered ``.VPSidebar`` to obtain the
       full navigation tree with headings and URLs.
    2. Concurrently fetch every linked page.
    3. Extract the ``.vp-doc`` content area and convert it to Markdown.
    """

    async def crawl(self) -> DocSite:
        async with self._make_client() as client:
            html = await self._fetch(client, self.start_url)
            if not html:
                raise RuntimeError(f"Could not fetch {self.start_url}")

            soup = self._parse_html(html)
            site_title = self._get_site_title(soup)
            nav = self._extract_sidebar(soup)
            flat = self._flatten_nav(nav)

            # Ensure the start URL is included even if not in the sidebar
            start_norm = self._abs_url(self.start_url)
            if start_norm not in {url for _, url in flat}:
                flat.insert(0, ([], start_norm))

            pages = await self._crawl_all(client, flat)

        return DocSite(
            title=site_title,
            base_url=self.base_url,
            site_type=SiteType.VITEPRESS,
            pages=pages,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Sidebar parsing
    # ──────────────────────────────────────────────────────────────────────────

    def _get_site_title(self, soup: BeautifulSoup) -> str:
        for sel in (".VPNavBarTitle .title", ".site-title", "title"):
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                # Strip suffix like " | VitePress"
                return text.split("|")[0].split(" - ")[0].strip()
        return "Documentation"

    def _extract_sidebar(self, soup: BeautifulSoup) -> list[NavItem]:
        sidebar = soup.select_one(".VPSidebar")
        if sidebar:
            return self._parse_vp_items(sidebar)
        # Fallback: collect all unique in-site links from the page nav
        return self._nav_links_fallback(soup)

    def _parse_vp_items(self, container: Tag) -> list[NavItem]:
        """Recursively parse ``.VPSidebarItem`` elements into NavItem tree."""
        items: list[NavItem] = []
        for el in container.find_all("div", class_=lambda c: c and "VPSidebarItem" in c, recursive=False):
            items.extend(self._parse_single_vp_item(el))
        # If direct child traversal found nothing, try a flat search
        if not items:
            for el in container.select(".VPSidebarItem"):
                if not el.find_parent(class_="VPSidebarItem"):
                    items.extend(self._parse_single_vp_item(el))
        return items

    def _parse_single_vp_item(self, el: Tag) -> list[NavItem]:
        classes = el.get("class") or []
        level = 0
        for cls in classes:
            if cls.startswith("level-"):
                try:
                    level = int(cls.split("-")[1])
                except ValueError:
                    pass

        link = el.select_one("a.item, a")
        text_el = el.select_one(".text, p.text")

        title = ""
        url = ""
        if link:
            title = link.get_text(strip=True)
            href = link.get("href") or ""
            url = self._abs_url(href) if href else ""
        elif text_el:
            title = text_el.get_text(strip=True)

        if not title:
            return []

        children: list[NavItem] = []
        items_div = el.select_one(".items")
        if items_div:
            children = self._parse_vp_items(items_div)

        return [NavItem(title=title, url=url, level=level, children=children)]

    def _nav_links_fallback(self, soup: BeautifulSoup) -> list[NavItem]:
        seen: set[str] = set()
        items: list[NavItem] = []
        for a in soup.select("nav a, .sidebar a"):
            href = a.get("href") or ""
            if not href or href.startswith("#"):
                continue
            url = self._abs_url(href)
            if url not in seen and self._is_internal(url):
                seen.add(url)
                items.append(NavItem(title=a.get_text(strip=True), url=url))
        return items

    # ──────────────────────────────────────────────────────────────────────────
    # Nav flattening
    # ──────────────────────────────────────────────────────────────────────────

    def _flatten_nav(
        self,
        items: list[NavItem],
        breadcrumb: list[str] | None = None,
    ) -> list[tuple[list[str], str]]:
        """Return ``[(breadcrumb, absolute_url), ...]`` in sidebar order."""
        crumb = breadcrumb or []
        result: list[tuple[list[str], str]] = []
        for item in items:
            child_crumb = crumb + ([item.title] if item.title else [])
            if item.url:
                result.append((child_crumb, item.url))
            if item.children:
                result.extend(self._flatten_nav(item.children, child_crumb))
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Page fetching
    # ──────────────────────────────────────────────────────────────────────────

    async def _crawl_all(
        self,
        client: httpx.AsyncClient,
        flat: list[tuple[list[str], str]],
    ) -> list[DocPage]:
        pages: list[DocPage | None] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Crawling VitePress pages…", total=len(flat))

            async def _fetch_one(breadcrumb: list[str], url: str, order: int) -> DocPage | None:
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
                return DocPage(url=url, title=title, content=content, breadcrumb=breadcrumb, order=order)

            results = await asyncio.gather(
                *[_fetch_one(bc, url, i) for i, (bc, url) in enumerate(flat)]
            )
            pages = list(results)

        return [p for p in pages if p is not None]

    def _extract_content(self, soup: BeautifulSoup, breadcrumb: list[str]) -> tuple[str, str]:
        """Return (title, markdown_content) from a VitePress page."""
        content_el = (
            soup.select_one(".vp-doc")
            or soup.select_one(".VPDoc .content")
            or soup.select_one("main article")
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

        # Remove chrome that pollutes the Markdown output
        for el in content_el.select(".edit-link, .prev-next, .VPDocFooter, .aside, nav, .vp-sponsor"):
            el.decompose()

        return title, html_to_markdown(content_el)
