"""Detect the type of documentation site from its HTML."""
from __future__ import annotations

import asyncio
import shutil
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .models import SiteType

_CURL_BIN: str | None = shutil.which("curl")


async def _fetch_with_curl(url: str, timeout: float = 30.0) -> str | None:
    """Fallback: fetch *url* via system ``curl`` when httpx is blocked."""
    if not _CURL_BIN:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            _CURL_BIN, "-sL", "--max-time", str(int(timeout)), url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0 and stdout:
            return stdout.decode("utf-8", errors="replace")
    except Exception:
        pass
    return None


async def detect_site_type(url: str, client: httpx.AsyncClient) -> SiteType:
    """Fetch the root page and infer whether it is VitePress, Docsify, Mintlify, Feishu, or generic."""
    # ── Feishu Open Platform ───────────────────────────────────────────────────
    from urllib.parse import urlparse as _urlparse
    _host = _urlparse(url).netloc.lower()
    if _host in ("open.feishu.cn", "open.larkoffice.com"):
        return SiteType.FEISHU_DOCS

    html: str | None = None
    resp_headers: dict[str, str] = {}

    try:
        resp = await client.get(url, follow_redirects=True)
        resp_headers = dict(resp.headers)
        if resp.status_code == 403 and "challenge" in resp.headers.get("cf-mitigated", ""):
            # Cloudflare JS challenge — fall back to system curl
            html = await _fetch_with_curl(url)
        else:
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPError:
        # Last resort: try curl
        html = await _fetch_with_curl(url)

    if not html:
        return SiteType.GENERIC

    # ── Mintlify ───────────────────────────────────────────────────────────────
    if "x-llms-txt" in resp_headers or "llms-txt" in resp_headers.get("link", ""):
        return SiteType.MINTLIFY

    soup = BeautifulSoup(html, "lxml")

    # ── VitePress ──────────────────────────────────────────────────────────────
    # 1. <meta name="generator" content="vitepress ...">
    generator = soup.find("meta", attrs={"name": "generator"})
    if generator and "vitepress" in (generator.get("content") or "").lower():
        return SiteType.VITEPRESS

    # 2. Characteristic CSS classes rendered by VitePress
    if soup.select(".VPSidebar, .VPDoc, .vp-doc, .VPNavBar"):
        return SiteType.VITEPRESS

    # 3. Asset URLs containing vitepress
    for tag in soup.find_all(["script", "link"]):
        src = tag.get("src") or tag.get("href") or ""
        if "vitepress" in src.lower() or "/.vitepress/" in src:
            return SiteType.VITEPRESS

    # ── mdBook ─────────────────────────────────────────────────────────────────
    # 1. Characteristic element: <nav id="mdbook-sidebar"> or <ol class="chapter">
    if soup.select("#mdbook-sidebar, ol.chapter, .mdbook-version"):
        return SiteType.MDBOOK

    # 2. Asset URLs containing "mdbook"
    for tag in soup.find_all(["script", "link"]):
        src = tag.get("src") or tag.get("href") or ""
        if "mdbook" in src.lower():
            return SiteType.MDBOOK

    # 3. Characteristic CSS id/class used by mdBook's default theme
    if soup.select("#mdbook-content, #mdbook-body-container"):
        return SiteType.MDBOOK

    # ── Docusaurus ─────────────────────────────────────────────────────────────
    if generator and "docusaurus" in (generator.get("content") or "").lower():
        return SiteType.DOCUSAURUS

    # 2. Characteristic CSS classes rendered by Docusaurus
    if soup.select(".navbar__brand, .theme-doc-sidebar-container, .menu__list, .docusaurus-mt-lg"):
        return SiteType.DOCUSAURUS

    # 3. Asset URLs or inline markers containing docusaurus
    for tag in soup.find_all(["script", "link"]):
        src = tag.get("src") or tag.get("href") or ""
        if "docusaurus" in src.lower():
            return SiteType.DOCUSAURUS
    for script in soup.find_all("script"):
        inline = script.string or ""
        if "docusaurus" in inline.lower():
            return SiteType.DOCUSAURUS

    # ── MkDocs (Material theme and compatible) ────────────────────────────────
    # 1. Generator meta tag
    gen_content = (generator.get("content") or "").lower() if generator else ""
    if any(x in gen_content for x in ("mkdocs", "zensical")):
        return SiteType.MKDOCS

    # 2. Characteristic CSS classes rendered by MkDocs Material
    if soup.select(".md-nav--primary, .md-content__inner, .md-typeset"):
        return SiteType.MKDOCS

    # 3. Asset URLs containing mkdocs
    for tag in soup.find_all(["script", "link"]):
        src = tag.get("src") or tag.get("href") or ""
        if "mkdocs" in src.lower():
            return SiteType.MKDOCS

    # ── Docsify ────────────────────────────────────────────────────────────────
    for script in soup.find_all("script"):
        src = script.get("src") or ""
        if "docsify" in src.lower():
            return SiteType.DOCSIFY
        inline = script.string or ""
        if "$docsify" in inline or "window.$docsify" in inline:
            return SiteType.DOCSIFY

    # ── Mintlify (HTML fallback) ───────────────────────────────────────────────
    # Check for mintlify-assets script paths or powered-by footer link
    for tag in soup.find_all(["script", "a"]):
        src = tag.get("src") or tag.get("href") or ""
        if "mintlify" in src.lower():
            return SiteType.MINTLIFY
    for el in soup.select("a[href*='mintlify.com']"):
        return SiteType.MINTLIFY

    return SiteType.GENERIC
