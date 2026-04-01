"""mdBook documentation site crawler."""
from __future__ import annotations

import asyncio
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..converter import html_to_markdown
from ..models import DocPage, DocSite, NavItem, SiteType
from .base import BaseCrawler


class MdBookCrawler(BaseCrawler):
    """Crawls mdBook-generated static documentation sites.

    Strategy:
    1. Fetch ``toc.html`` (the static noscript table-of-contents iframe)
       to get the fully-ordered, nested chapter list — this is always
       present regardless of JavaScript.
    2. Concurrently fetch each chapter page and extract
       ``#mdbook-content main`` as the content area.
    3. Breadcrumb is reconstructed from the ToC hierarchy.
    """

    async def crawl(self) -> DocSite:
        async with self._make_client() as client:
            # toc.html sits next to the start page
            toc_url = urljoin(self.start_url.rstrip("/") + "/", "toc.html")
            toc_html = await self._fetch(client, toc_url)

            # Also fetch the start page for the site title
            start_html = await self._fetch(client, self.start_url)
            if not start_html:
                raise RuntimeError(f"Could not fetch {self.start_url}")

            soup_start = self._parse_html(start_html)
            site_title = self._get_site_title(soup_start)

            if toc_html:
                toc_soup = self._parse_html(toc_html)
                nav = self._parse_toc(toc_soup)
            else:
                # Fallback: collect links from the start page sidebar area
                nav = self._nav_links_fallback(soup_start)

            flat = self._flatten_nav(nav)

            # Ensure the start URL is included, but avoid duplicating when
            # toc.html already lists index.html (equivalent to the start URL)
            start_norm = self._abs_url(self.start_url)
            start_index_norm = start_norm.rstrip("/") + "/index.html"
            existing_urls = {url for _, url in flat}
            if start_norm not in existing_urls and start_index_norm not in existing_urls:
                flat.insert(0, ([], start_norm))

            pages = await self._crawl_all(client, flat)

        return DocSite(
            title=site_title,
            base_url=self.base_url,
            site_type=SiteType.MDBOOK,
            pages=pages,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Site title
    # ──────────────────────────────────────────────────────────────────────────

    def _get_site_title(self, soup: BeautifulSoup) -> str:
        # mdBook sets <title>Chapter Title - Book Title</title>
        tag = soup.find("title")
        if tag:
            raw = tag.get_text(strip=True)
            # "Introduction - mdBook Documentation"  →  "mdBook Documentation"
            parts = raw.split(" - ")
            if len(parts) >= 2:
                return parts[-1].strip()
            return raw.strip()
        return "Documentation"

    # ──────────────────────────────────────────────────────────────────────────
    # ToC parsing
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_toc(self, soup: BeautifulSoup) -> list[NavItem]:
        """Parse ``ol.chapter`` from toc.html into a NavItem tree."""
        root = soup.select_one("ol.chapter")
        if root is None:
            return []
        return self._parse_chapter_ol(root, level=1, breadcrumb=[])

    def _parse_chapter_ol(
        self, ol: Tag, level: int, breadcrumb: list[str]
    ) -> list[NavItem]:
        items: list[NavItem] = []
        current_part: str = ""

        for li in ol.find_all("li", recursive=False):
            if not isinstance(li, Tag):
                continue

            # Part/section separator heading (no link)
            if "part-title" in (li.get("class") or []):
                current_part = li.get_text(strip=True)
                continue

            if "chapter-item" not in " ".join(li.get("class") or []):
                continue

            # Find the link
            a = li.select_one("a")
            if a is None:
                # Separator / spacer item
                continue

            import re
            title = a.get_text(separator=" ", strip=True)
            # Strip leading number like "1." or "4.1."
            title = re.sub(r"^\d+(\.\d+)*\.\s*", "", title).strip()
            # Collapse whitespace introduced by inline elements (e.g. <code>)
            title = re.sub(r"\s{2,}", " ", title)

            href = str(a.get("href") or "")
            # toc.html uses relative hrefs (e.g. "guide/installation.html")
            # resolve relative to the book root (start_url dir)
            url = ""
            if href and not href.startswith("#"):
                base = self.start_url.rstrip("/") + "/"
                url = urljoin(base, href).split("#")[0].rstrip("/")

            # Recurse into nested ol.section
            children: list[NavItem] = []
            sub_ol = li.select_one("ol.section")
            if sub_ol is not None:
                children = self._parse_chapter_ol(sub_ol, level + 1, breadcrumb + [title])

            # Build breadcrumb including part title
            crumb_parts = []
            if current_part:
                crumb_parts.append(current_part)
            crumb_parts.append(title)

            items.append(NavItem(
                title=title,
                url=url,
                level=level,
                children=children,
            ))

        return items

    def _nav_links_fallback(self, soup: BeautifulSoup) -> list[NavItem]:
        """Collect chapter links from the rendered sidebar (JS-populated, may be empty)."""
        seen: set[str] = set()
        items: list[NavItem] = []
        for a in soup.select(".sidebar a, ol.chapter a, nav a"):
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

    # ──────────────────────────────────────────────────────────────────────────
    # Nav flattening  (part titles carried into breadcrumb)
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
            task = progress.add_task("Crawling mdBook pages…", total=len(flat))

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

    def _extract_content(
        self, soup: BeautifulSoup, breadcrumb: list[str]
    ) -> tuple[str, str]:
        """Return (title, markdown_content) for a single mdBook page."""
        # Primary content area
        content_el = (
            soup.select_one("#mdbook-content main")
            or soup.select_one(".content main")
            or soup.select_one("main")
        )

        title = ""
        if content_el:
            h1 = content_el.find("h1")
            if h1 and isinstance(h1, Tag):
                import re as _re
                title = _re.sub(r"\s{2,}", " ", h1.get_text(separator=" ", strip=True))

        if not title:
            tag = soup.find("title")
            if tag:
                raw = tag.get_text(strip=True)
                title = raw.split(" - ")[0].strip()

        if not title:
            title = breadcrumb[-1] if breadcrumb else ""

        if not content_el:
            return title, ""

        # Remove nav chrome
        for sel in (
            ".nav-chapters",
            "nav.nav-wrapper",
            ".mobile-nav-chapters",
            ".page-detail",
            "#mdbook-print-float",
        ):
            for el in content_el.select(sel):
                el.decompose()

        return title, html_to_markdown(content_el)
