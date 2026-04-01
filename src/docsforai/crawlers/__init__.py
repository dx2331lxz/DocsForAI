"""Crawler package — public factory function."""
from __future__ import annotations

from ..models import SiteType
from .base import BaseCrawler
from .docsify import DocsifyCrawler
from .generic import GenericCrawler
from .vitepress import VitePressCrawler


def make_crawler(site_type: SiteType, url: str, **kwargs) -> BaseCrawler:
    """Return the appropriate crawler for *site_type*."""
    match site_type:
        case SiteType.VITEPRESS:
            return VitePressCrawler(url, **kwargs)
        case SiteType.DOCSIFY:
            return DocsifyCrawler(url, **kwargs)
        case _:
            return GenericCrawler(url, **kwargs)


__all__ = ["make_crawler", "BaseCrawler", "VitePressCrawler", "DocsifyCrawler", "GenericCrawler"]
