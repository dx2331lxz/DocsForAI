"""MkDocs (Material theme) documentation site crawler."""
from __future__ import annotations

import asyncio

import httpx
from bs4 import BeautifulSoup
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..converter import html_to_markdown
from ..models import DocPage, DocSite, SiteType
from .base import BaseCrawler


class MkDocsCrawler(BaseCrawler):
    """Crawls MkDocs sites (Material theme and compatible variants).

    Strategy:
    1. Fetch the start page; extract the ``nav.md-nav--primary`` sidebar to
       obtain the full ordered navigation with section breadcrumbs.
    2. Concurrently fetch every page linked in the nav.
    3. Extract the ``article.md-content__inner`` content area and convert to
       Markdown, stripping sidebar / footer chrome.
    """

    async def crawl(self) -> DocSite:
        async with self._make_client() as client:
            html = await self._fetch(client, self.start_url)
            if not html:
                raise RuntimeError(f"Could not fetch {self.start_url}")

            soup = self._parse_html(html)
            site_title = self._get_site_title(soup)

            # Ensure page_url ends with "/" so relative hrefs like "concepts/models/"
            # resolve correctly via urljoin (without it "latest" is treated as a file).
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
            site_type=SiteType.MKDOCS,
            pages=pages,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Site title
    # ──────────────────────────────────────────────────────────────────────────

    def _get_site_title(self, soup: BeautifulSoup) -> str:
        # MkDocs Material stores the site name as the top-level nav title label
        nav_title = soup.select_one("nav.md-nav--primary > .md-nav__title")
        if nav_title:
            title = nav_title.get_text(strip=True)
            if title:
                return title

        # Fallback: <title> — take the part after the last separator
        title_tag = soup.find("title")
        if title_tag:
            text = title_tag.get_text(strip=True)
            for sep in (" - ", " | ", " · "):
                if sep in text:
                    candidate = text.split(sep)[-1].strip()
                    if candidate:
                        return candidate
            if text:
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
        """Return ordered ``[(breadcrumb, abs_url), ...]`` from the sidebar nav."""
        primary = soup.find("nav", class_="md-nav--primary")
        if not primary:
            return self._fallback_links(soup, page_url)

        top_ul = primary.find("ul", class_="md-nav__list")
        if not top_ul:
            # Flat fallback: just grab all md-nav__link anchors
            return self._links_from_nav(primary, [], page_url)

        seen: set[str] = set()
        results: list[tuple[list[str], str]] = []

        for top_li in top_ul.find_all("li", class_="md-nav__item", recursive=False):
            label_el = top_li.find("label", class_="md-nav__title")
            section_name = label_el.get_text(strip=True) if label_el else ""

            # Top-level direct page link (no section container)
            top_a = top_li.find("a", class_="md-nav__link")
            if top_a and not label_el:
                href = top_a.get("href", "")
                if self._is_page_href(href):
                    url = self._abs_url(href, page_url)
                    if url not in seen:
                        seen.add(url)
                        title = top_a.get_text(strip=True)
                        results.append(([title], url))
                continue

            # Section with nested sub-pages
            sub_nav = top_li.find("nav", class_="md-nav")
            if sub_nav:
                for sub_a in sub_nav.find_all("a", class_="md-nav__link"):
                    href = sub_a.get("href", "")
                    if self._is_page_href(href):
                        url = self._abs_url(href, page_url)
                        if url not in seen:
                            seen.add(url)
                            title = sub_a.get_text(strip=True)
                            bc = [section_name, title] if section_name else [title]
                            results.append((bc, url))

        return results

    def _is_page_href(self, href: str) -> bool:
        """Return True if href looks like a relative documentation page link."""
        if not href:
            return False
        # Skip fragments, JavaScript, mailto, and absolute external URLs
        if href.startswith(("#", "javascript:", "mailto:")):
            return False
        if href.startswith(("http://", "https://")):
            return False
        return True

    def _links_from_nav(
        self,
        nav_el: BeautifulSoup,
        breadcrumb: list[str],
        page_url: str,
    ) -> list[tuple[list[str], str]]:
        seen: set[str] = set()
        results: list[tuple[list[str], str]] = []
        for a in nav_el.find_all("a", class_="md-nav__link"):
            href = a.get("href", "")
            if self._is_page_href(href):
                url = self._abs_url(href, page_url)
                if url not in seen:
                    seen.add(url)
                    title = a.get_text(strip=True)
                    results.append((breadcrumb + [title], url))
        return results

    def _fallback_links(
        self, soup: BeautifulSoup, page_url: str
    ) -> list[tuple[list[str], str]]:
        seen: set[str] = set()
        results: list[tuple[list[str], str]] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href or href.startswith(("#", "javascript:", "mailto:")):
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
            task = progress.add_task("Crawling MkDocs pages…", total=len(flat))

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
        """Return (title, markdown_content) from a MkDocs page."""
        content_el = (
            soup.select_one("article.md-content__inner")
            or soup.select_one("div.md-content__inner")
            or soup.select_one(".md-content")
            or soup.select_one("article")
            or soup.select_one("main")
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

        # Remove navigation and footer chrome
        for sel in (
            "nav",
            ".md-sidebar",
            ".md-footer",
            ".md-source",
            ".md-search",
            ".md-announce",
            ".md-skip",
            ".headerlink",
        ):
            for el in content_el.select(sel):
                el.decompose()

        return title, html_to_markdown(content_el)
