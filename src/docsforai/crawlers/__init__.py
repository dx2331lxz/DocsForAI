"""Crawler package — public factory function."""
from __future__ import annotations

from ..models import SiteType
from .base import BaseCrawler
from .docusaurus import DocusaurusCrawler
from .docsify import DocsifyCrawler
from .feishu import FeishuDocsCrawler
from .generic import GenericCrawler
from .mdbook import MdBookCrawler
from .mintlify import MintlifyCrawler
from .mkdocs import MkDocsCrawler
from .vitepress import VitePressCrawler


def make_crawler(site_type: SiteType, url: str, **kwargs) -> BaseCrawler:
    """Return the appropriate crawler for *site_type*."""
    match site_type:
        case SiteType.VITEPRESS:
            return VitePressCrawler(url, **kwargs)
        case SiteType.DOCSIFY:
            return DocsifyCrawler(url, **kwargs)
        case SiteType.MINTLIFY:
            return MintlifyCrawler(url, **kwargs)
        case SiteType.FEISHU_DOCS:
            return FeishuDocsCrawler(url, **kwargs)
        case SiteType.DOCUSAURUS:
            return DocusaurusCrawler(url, **kwargs)
        case SiteType.MDBOOK:
            return MdBookCrawler(url, **kwargs)
        case SiteType.MKDOCS:
            return MkDocsCrawler(url, **kwargs)
        case _:
            return GenericCrawler(url, **kwargs)


__all__ = [
    "make_crawler", "BaseCrawler", "VitePressCrawler", "DocsifyCrawler",
    "MintlifyCrawler", "FeishuDocsCrawler", "DocusaurusCrawler",
    "MdBookCrawler", "MkDocsCrawler", "GenericCrawler",
]
