"""Abstract base crawler."""
from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..models import DocSite

# System curl is used as a fallback when httpx is blocked (e.g. Cloudflare).
_CURL_BIN: str | None = shutil.which("curl")


class BaseCrawler(ABC):
    """Base class for all documentation site crawlers."""

    _HEADERS = {
        "User-Agent": "DocsForAI/0.1 (https://github.com/dx2331lxz/DocsForAI)",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    def __init__(
        self,
        base_url: str,
        *,
        concurrency: int = 5,
        delay: float = 0.1,
        timeout: float = 30.0,
    ) -> None:
        parsed = urlparse(base_url)
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.start_url = base_url.rstrip("/")
        self.concurrency = concurrency
        self.delay = delay
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(concurrency)
        self._visited: set[str] = set()
        # When True, all fetches go through curl subprocess (set after httpx 403).
        self._use_curl: bool = False

    @abstractmethod
    async def crawl(self) -> DocSite:
        """Crawl the site and return structured documentation."""
        ...

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self._HEADERS,
            timeout=self.timeout,
            follow_redirects=True,
        )

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Rate-limited fetch; returns response text or None on error.

        When httpx receives a 403 with ``cf-mitigated: challenge`` headers the
        crawler permanently switches to a ``curl`` subprocess fallback for all
        subsequent requests on this crawler instance.
        """
        url = url.split("#")[0]
        if not url:
            return None
        async with self._semaphore:
            if self.delay > 0:
                await asyncio.sleep(self.delay)

            if self._use_curl:
                return await self._fetch_curl(url)

            try:
                resp = await client.get(url)
                # Detect Cloudflare JS challenge (403 + cf-mitigated header)
                if resp.status_code == 403 and "challenge" in resp.headers.get("cf-mitigated", ""):
                    if _CURL_BIN:
                        self._use_curl = True
                        return await self._fetch_curl(url)
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPError:
                return None

    async def _fetch_curl(self, url: str) -> str | None:
        """Fetch *url* using system ``curl`` in a subprocess."""
        if not _CURL_BIN:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                _CURL_BIN, "-sL", "--max-time", str(int(self.timeout)), url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0 and stdout:
                return stdout.decode("utf-8", errors="replace")
        except Exception:
            pass
        return None

    def _abs_url(self, href: str, from_url: str = "") -> str:
        """Resolve href to an absolute URL, stripping fragments."""
        base = from_url or self.start_url
        return urljoin(base, href).split("#")[0].rstrip("/")

    def _is_internal(self, url: str) -> bool:
        return urlparse(url).netloc == urlparse(self.base_url).netloc

    @staticmethod
    def _parse_html(html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")
