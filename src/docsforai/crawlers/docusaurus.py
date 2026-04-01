"""Docusaurus documentation site crawler."""
from __future__ import annotations

import asyncio
import re

import httpx
from bs4 import BeautifulSoup, Tag
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..converter import html_to_markdown
from ..models import DocPage, DocSite, NavItem, SiteType
from .base import BaseCrawler


class DocusaurusCrawler(BaseCrawler):
    """Crawls Docusaurus v2/v3-generated static documentation sites.

    Strategy (three-layer, depth-first on doc architecture):
    1. **Sitemap** – fetch ``/sitemap.xml`` to discover *all* pages for the
       target version, including those hidden behind collapsed sidebar sections.
    2. **Version filtering** – inspect the rendered navbar version-switcher to
       exclude sibling-version paths (e.g. ``/docs/2.x/``) from the list.
    3. **Per-page breadcrumb** – extract the ``<nav aria-label="Breadcrumbs">``
       element on every page to reconstruct the exact hierarchy without relying
       on the partially-rendered sidebar at all.
    Fallback: if sitemap is unavailable, parse the visible sidebar + BFS.
    """

    async def crawl(self) -> DocSite:
        async with self._make_client() as client:
            html = await self._fetch(client, self.start_url)
            if not html:
                raise RuntimeError(f"Could not fetch {self.start_url}")

            soup = self._parse_html(html)
            site_title = self._get_site_title(soup)

            # ── URL discovery ──────────────────────────────────────────────
            sitemap_urls = await self._discover_from_sitemap(client, soup)

            if sitemap_urls:
                # Sitemap gives us complete flat list; breadcrumb extracted per page
                flat: list[tuple[list[str], str]] = [([], url) for url in sitemap_urls]
            else:
                # Fallback: visible sidebar (may be incomplete due to collapsed items)
                nav = self._extract_sidebar(soup)
                flat = self._flatten_nav(nav)
                start_norm = self._abs_url(self.start_url)
                if start_norm not in {url for _, url in flat}:
                    flat.insert(0, ([], start_norm))

            pages = await self._crawl_all(client, flat, use_page_breadcrumb=bool(sitemap_urls))

        return DocSite(
            title=site_title,
            base_url=self.base_url,
            site_type=SiteType.DOCUSAURUS,
            pages=pages,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Sitemap-based URL discovery
    # ──────────────────────────────────────────────────────────────────────────

    async def _discover_from_sitemap(
        self, client: httpx.AsyncClient, first_page_soup: BeautifulSoup
    ) -> list[str]:
        """Return all doc URLs for the current version from sitemap.xml."""
        sitemap_url = f"{self.base_url}/sitemap.xml"
        text = await self._fetch(client, sitemap_url)
        if not text or "<urlset" not in text:
            return []

        all_locs = re.findall(r"<loc>(.*?)</loc>", text)
        prefix = self.start_url.rstrip("/")

        # Detect version sub-paths to exclude (e.g. /docs/2.x/, /docs/3.0.0-beta/)
        excluded = self._detect_other_version_prefixes(first_page_soup, prefix)

        filtered: list[str] = []
        for loc in all_locs:
            loc = loc.rstrip("/")
            # Must be under the start URL
            if loc != prefix and not loc.startswith(prefix + "/"):
                continue
            # Exclude other version trees
            if any(loc.startswith(ep) for ep in excluded):
                continue
            filtered.append(loc)

        return filtered

    def _detect_other_version_prefixes(
        self, soup: BeautifulSoup, current_prefix: str
    ) -> list[str]:
        """Detect versioned sub-paths (e.g. /docs/2.x) to exclude from sitemap."""
        excluded: list[str] = []
        # Docusaurus version-switcher lives in the navbar dropdown
        for a in soup.select(".navbar__item a, .dropdown__menu a"):
            if not isinstance(a, Tag):
                continue
            href = str(a.get("href") or "")
            if not href:
                continue
            url = self._abs_url(href).rstrip("/")
            # Only care about links under current prefix
            if not url.startswith(current_prefix + "/"):
                continue
            rel = url[len(current_prefix):].lstrip("/")
            seg = rel.split("/")[0] if rel else ""
            # Version segments look like: 2.x  3.0.0  3.0.0-beta  next  canary
            if seg and (re.match(r"^\d+\.", seg) or seg in ("next", "canary")):
                excl = current_prefix.rstrip("/") + "/" + seg
                if excl not in excluded:
                    excluded.append(excl)
        return excluded

    # ──────────────────────────────────────────────────────────────────────────
    # Site title
    # ──────────────────────────────────────────────────────────────────────────

    def _get_site_title(self, soup: BeautifulSoup) -> str:
        for sel in (".navbar__title", ".navbar__brand", "title"):
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                return text.split("|")[0].split(" - ")[0].strip()
        return "Documentation"

    # ──────────────────────────────────────────────────────────────────────────
    # Sidebar extraction (fallback only)
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_sidebar(self, soup: BeautifulSoup) -> list[NavItem]:
        sidebar = (
            soup.select_one(".theme-doc-sidebar-container nav")
            or soup.select_one("nav.menu")
            or soup.select_one(".sidebar_node_modules")
        )
        if sidebar:
            items = self._parse_menu_list(sidebar, level=1)
            if items:
                return items
        return self._nav_links_fallback(soup)

    def _parse_menu_list(self, container: Tag, level: int) -> list[NavItem]:
        items: list[NavItem] = []
        root_list = container.select_one("ul.menu__list")
        if root_list is None:
            root_list = container
        for li in root_list.select(":scope > li.menu__list-item"):
            if not isinstance(li, Tag):
                continue
            item = self._parse_menu_item(li, level)
            if item:
                items.append(item)
        return items

    def _parse_menu_item(self, li: Tag, level: int) -> NavItem | None:
        link = li.select_one("a.menu__link")
        button = li.select_one("div.menu__list-item-collapsible")

        title = ""
        url = ""
        if link is not None:
            title = link.get_text(strip=True)
            href = str(link.get("href") or "")
            if href and not href.startswith("#"):
                url = self._abs_url(href)
        elif button is not None:
            inner = button.select_one(".menu__link")
            title = inner.get_text(strip=True) if inner is not None else button.get_text(strip=True)

        if not title:
            return None

        children: list[NavItem] = []
        sub_ul = li.select_one(":scope > ul.menu__list")
        if sub_ul is not None:
            for child_li in sub_ul.select(":scope > li.menu__list-item"):
                if not isinstance(child_li, Tag):
                    continue
                child = self._parse_menu_item(child_li, level + 1)
                if child:
                    children.append(child)

        return NavItem(title=title, url=url, level=level, children=children)

    def _nav_links_fallback(self, soup: BeautifulSoup) -> list[NavItem]:
        seen: set[str] = set()
        items: list[NavItem] = []
        for a in soup.select("nav a, .sidebar a, .menu a"):
            if not isinstance(a, Tag):
                continue
            href = str(a.get("href") or "")
            if not href or href.startswith("#"):
                continue
            url = self._abs_url(href)
            if url not in seen and self._is_internal(url):
                seen.add(url)
                items.append(NavItem(title=a.get_text(strip=True), url=url))
        return items

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

    # ──────────────────────────────────────────────────────────────────────────
    # Per-page breadcrumb extraction
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_page_breadcrumb(soup: BeautifulSoup) -> list[str]:
        """Extract hierarchy from the page's own breadcrumb nav."""
        crumb: list[str] = []
        # Primary: aria-labelled breadcrumbs nav
        for el in soup.select("nav[aria-label='Breadcrumbs'] .breadcrumbs__item"):
            if not isinstance(el, Tag):
                continue
            # Skip the home icon item (no text)
            text = el.get_text(strip=True)
            if text:
                crumb.append(text)
        if crumb:
            return crumb
        # Fallback: any .breadcrumbs__item
        for el in soup.select(".breadcrumbs__item"):
            if not isinstance(el, Tag):
                continue
            text = el.get_text(strip=True)
            if text:
                crumb.append(text)
        return crumb

    # ──────────────────────────────────────────────────────────────────────────
    # Page fetching
    # ──────────────────────────────────────────────────────────────────────────

    async def _crawl_all(
        self,
        client: httpx.AsyncClient,
        flat: list[tuple[list[str], str]],
        *,
        use_page_breadcrumb: bool = False,
    ) -> list[DocPage]:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Crawling Docusaurus pages…", total=len(flat))

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
                # When using sitemap, derive breadcrumb from the page itself
                if use_page_breadcrumb:
                    breadcrumb = self._extract_page_breadcrumb(soup)
                title, content = self._extract_content(soup, breadcrumb)
                progress.advance(task)
                return DocPage(
                    url=url, title=title, content=content,
                    breadcrumb=breadcrumb, order=order,
                )

            results = await asyncio.gather(
                *[_fetch_one(bc, url, i) for i, (bc, url) in enumerate(flat)]
            )

        return [p for p in results if p is not None]

    # ──────────────────────────────────────────────────────────────────────────
    # Content extraction
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_content(self, soup: BeautifulSoup, breadcrumb: list[str]) -> tuple[str, str]:
        """Return (title, markdown_content) from a Docusaurus page."""
        content_el = (
            soup.select_one("article.theme-doc-markdown")
            or soup.select_one(".theme-doc-markdown")
            or soup.select_one("article")
            or soup.select_one(".markdown")
            or soup.select_one("main")
        )

        title = ""
        if content_el:
            h1 = content_el.find("h1")
            if h1 and isinstance(h1, Tag):
                title = h1.get_text(strip=True)

        if not title:
            page_title = soup.find("title")
            if page_title:
                raw = page_title.get_text(strip=True)
                title = raw.split("|")[0].strip()

        if not title:
            title = breadcrumb[-1] if breadcrumb else ""

        if not content_el:
            return title, ""

        for sel in (
            ".theme-doc-footer",
            ".pagination-nav",
            "nav.pagination-nav",
            ".theme-doc-toc-mobile",
            ".theme-doc-toc-desktop",
            ".theme-doc-breadcrumbs",
            ".edit-this-page",
            ".last-updated",
        ):
            for el in content_el.select(sel):
                el.decompose()

        return title, html_to_markdown(content_el)
