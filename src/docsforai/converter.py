"""HTML → Markdown conversion with documentation-specific optimisations."""
from __future__ import annotations

import re
from typing import Any

from bs4 import Tag
from markdownify import MarkdownConverter


class _DocConverter(MarkdownConverter):
    """MarkdownConverter subclass tuned for documentation HTML."""

    def convert_pre(self, el: Tag, text: str, **kwargs: Any) -> str:
        code = el.find("code")
        if not code:
            return f"\n```\n{text.strip()}\n```\n\n"

        lang = ""
        for cls in code.get("class") or []:
            if cls.startswith("language-") or cls.startswith("lang-"):
                lang = cls.split("-", 1)[1]
                break
        # VitePress sometimes uses data-lang on <pre>
        if not lang:
            lang = el.get("data-lang") or code.get("data-lang") or ""

        code_text = code.get_text()
        return f"\n```{lang}\n{code_text.rstrip()}\n```\n\n"

    def convert_a(self, el: Tag, text: str, **kwargs: Any) -> str:
        href = el.get("href") or ""
        # Anchor-only links add no value in a flat Markdown file
        if href.startswith("#"):
            return text
        return super().convert_a(el, text, **kwargs)

    def convert_img(self, el: Tag, text: str, **kwargs: Any) -> str:
        src = el.get("src") or ""
        if not src or src.startswith("data:"):
            return ""
        alt = el.get("alt") or ""
        return f"![{alt}]({src})"


def _preprocess(soup: Tag) -> None:
    """In-place cleanup before Markdown conversion.

    * VitePress wraps each code block in ``<div class="language-sh …">``
      and adds a ``<span class="lang">sh</span>`` label element.
      If we leave it, markdownify emits the language string as a stray line of
      text right before the fenced code block.  We promote the language onto
      the ``<pre data-lang="…">`` attribute and remove the span so it is
      handled cleanly by ``convert_pre``.

    * Also removes copy-code buttons and similar decorative elements.
    """
    from bs4 import BeautifulSoup  # local import to avoid circular issues

    # Remove copy buttons
    for btn in soup.select("button.copy, button.vp-copy"):
        btn.decompose()

    # Promote language from wrapper div to <pre data-lang>
    for wrapper in soup.select("div[class*='language-']"):
        lang_span = wrapper.find("span", class_="lang")
        pre = wrapper.find("pre")
        if pre and lang_span:
            lang_value = lang_span.get_text(strip=True)
            if lang_value and not pre.get("data-lang"):
                pre["data-lang"] = lang_value
            lang_span.decompose()


def html_to_markdown(html: str | Tag) -> str:
    """Convert an HTML string or BeautifulSoup Tag to clean Markdown.

    Strips ``<script>``, ``<style>``, ``<nav>``, ``<footer>``, and ``<aside>``
    elements automatically.
    """
    from bs4 import BeautifulSoup

    if isinstance(html, str):
        soup: Tag = BeautifulSoup(html, "lxml").body or BeautifulSoup(html, "lxml")
    else:
        soup = html

    _preprocess(soup)

    converter = _DocConverter(
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "nav", "footer", "aside"],
    )
    result = converter.convert(str(soup))
    # Collapse runs of blank lines to at most two
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
