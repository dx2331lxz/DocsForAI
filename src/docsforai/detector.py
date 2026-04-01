"""Detect the type of documentation site from its HTML."""
from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from .models import SiteType


async def detect_site_type(url: str, client: httpx.AsyncClient) -> SiteType:
    """Fetch the root page and infer whether it is VitePress, Docsify, or generic."""
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError:
        return SiteType.GENERIC

    html = resp.text
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

    # ── Docsify ────────────────────────────────────────────────────────────────
    for script in soup.find_all("script"):
        src = script.get("src") or ""
        if "docsify" in src.lower():
            return SiteType.DOCSIFY
        inline = script.string or ""
        if "$docsify" in inline or "window.$docsify" in inline:
            return SiteType.DOCSIFY

    return SiteType.GENERIC
