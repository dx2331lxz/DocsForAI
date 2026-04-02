"""DocsForAI CLI."""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .crawlers import make_crawler
from .detector import detect_site_type
from .exporters import export
from .models import ExportFormat, SiteType

app = typer.Typer(
    name="docsforai",
    help=(
        "Specialized documentation-site crawler optimised for AI consumption.\n\n"
        "Automatically detects the documentation framework (VitePress, Docsify, GitBook, "
        "MkDocs, Docusaurus, mdBook, Starlight, Mintlify, Feishu, or generic) and exports "
        "clean Markdown / JSONL output ready for LLMs and vector databases."
    ),
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True)


def _site_slug(title: str) -> str:
    """Convert a site title to a filesystem-safe slug."""
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or "docs"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"DocsForAI {__version__}")
        raise typer.Exit()


@app.callback()
def _global(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", "-V", callback=_version_callback, is_eager=True, help="Show version and exit."),
    ] = None,
) -> None:
    pass


_CRAWL_EPILOG = (
    "[bold cyan]Examples[/bold cyan]\n\n"
    "  [dim]# Basic crawl — auto-detect framework, output as separate MD files[/dim]\n"
    "  docsforai crawl https://vitepress.dev/guide -o ./output\n\n"
    "  [dim]# Single merged file — paste directly into an LLM context[/dim]\n"
    "  docsforai crawl https://vitepress.dev/guide [yellow]-f single-md[/yellow] -o ./output\n\n"
    "  [dim]# JSONL — ingest into a vector database or fine-tuning dataset[/dim]\n"
    "  docsforai crawl https://docsify.js.org [yellow]-f jsonl[/yellow] -o ./output\n\n"
    "  [dim]# Multiple formats at once[/dim]\n"
    "  docsforai crawl https://vitepress.dev/guide [yellow]-f multi-md -f jsonl[/yellow] -o ./output\n\n"
    "  [dim]# Force framework type (useful when auto-detection fails)[/dim]\n"
    "  docsforai crawl https://example.com/docs [yellow]--type mkdocs[/yellow]\n\n"
    "  [dim]# Crawl a GitBook site with higher concurrency[/dim]\n"
    "  docsforai crawl https://agpt.co/docs [yellow]--concurrency 10[/yellow] -o ./output\n\n"
    "  [dim]# Generic BFS crawl capped at 50 pages[/dim]\n"
    "  docsforai crawl https://example.com/docs [yellow]--type generic --max-pages 50[/yellow]"
)


@app.command(epilog=_CRAWL_EPILOG)
def crawl(
    url: Annotated[
        str,
        typer.Argument(
            help="URL of the documentation site to crawl. Any page on the site works; the root or docs index is recommended."
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output", "-o",
            help=(
                "Base directory where output files will be written.\n\n"
                "  [green]multi-md[/green]  — writes to <output>/<site-name>/...\n"
                "  [green]single-md[/green] — writes <output>/<site-name>.md\n"
                "  [green]jsonl[/green]     — writes <output>/<site-name>.jsonl"
            ),
            show_default=True,
        ),
    ] = Path("./output"),
    fmt: Annotated[
        list[ExportFormat],
        typer.Option(
            "--format", "-f",
            help=(
                "Export format. Repeat the flag to produce multiple formats simultaneously.\n\n"
                "  [green]multi-md[/green]  — one .md file per page under <output>/<site-name>/ (default)\n"
                "  [green]single-md[/green] — all pages merged into <output>/<site-name>.md\n"
                "  [green]jsonl[/green]     — one JSON record per line in <output>/<site-name>.jsonl"
            ),
            show_default=True,
        ),
    ] = [ExportFormat.MULTI_MD],  # noqa: B006
    site_type: Annotated[
        Optional[SiteType],
        typer.Option(
            "--type", "-t",
            help=(
                "Force a specific framework and skip auto-detection.\n\n"
                "Auto-detection works for most sites. Use this flag only when the detected\n"
                "type is wrong or the site uses an unusual configuration.\n\n"
                "Supported values: vitepress · docsify · mintlify · feishu-docs ·\n"
                "docusaurus · mdbook · mkdocs · starlight · gitbook · generic"
            ),
        ),
    ] = None,
    concurrency: Annotated[
        int,
        typer.Option(help="Maximum number of pages fetched in parallel. Raise for faster crawls, lower to be polite."),
    ] = 5,
    delay: Annotated[
        float,
        typer.Option(help="Seconds to sleep between requests (per worker). Increase to avoid rate-limiting."),
    ] = 0.1,
    timeout: Annotated[
        float,
        typer.Option(help="HTTP request timeout in seconds. Increase for slow or large documentation sites."),
    ] = 30.0,
    max_pages: Annotated[
        int,
        typer.Option(help="Maximum pages to collect. Only applied when --type generic is used."),
    ] = 200,
) -> None:
    """Crawl a documentation site and export clean Markdown / JSONL for AI use.

    The framework is detected automatically from the page HTML.
    Supported: VitePress · Docsify · Mintlify · GitBook · MkDocs · Docusaurus · mdBook · Starlight · Feishu · Generic
    """
    asyncio.run(
        _run_crawl(url, output, fmt, site_type, concurrency, delay, timeout, max_pages)
    )


async def _run_crawl(
    url: str,
    output: Path,
    formats: list[ExportFormat],
    forced_type: SiteType | None,
    concurrency: int,
    delay: float,
    timeout: float,
    max_pages: int,
) -> None:
    import httpx

    console.print(Panel(f"[bold cyan]DocsForAI[/] [dim]v{__version__}[/]", expand=False))

    # ── Detect site type ───────────────────────────────────────────────────────
    if forced_type:
        site_type = forced_type
        console.print(f"  Site type : [bold]{site_type.value}[/] (forced)")
    else:
        console.print(f"  Detecting site type for [link={url}]{url}[/link]…", end=" ")
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            site_type = await detect_site_type(url, client)
        console.print(f"[bold green]{site_type.value}[/]")

    # ── Crawl ──────────────────────────────────────────────────────────────────
    console.print(f"  Output    : {output}")
    console.print(f"  Formats   : {', '.join(f.value for f in formats)}\n")

    crawler_kwargs: dict = dict(concurrency=concurrency, delay=delay, timeout=timeout)
    if site_type == SiteType.GENERIC:
        crawler_kwargs["max_pages"] = max_pages

    crawler = make_crawler(site_type, url, **crawler_kwargs)

    try:
        site = await crawler.crawl()
    except Exception as exc:
        err_console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(1) from exc

    if not site.pages:
        err_console.print("[yellow]Warning:[/] No pages were collected.")
        raise typer.Exit(1)

    # ── Export ─────────────────────────────────────────────────────────────────
    all_written: list[Path] = []
    destinations: list[Path] = []
    site_slug = _site_slug(site.title)
    for fmt in formats:
        if fmt == ExportFormat.MULTI_MD:
            target = output / site_slug
            destination = target
        elif fmt == ExportFormat.SINGLE_MD:
            target = output
            destination = output / f"{site_slug}.md"
        else:
            target = output
            destination = output / f"{site_slug}.jsonl"

        written = export(site, target, fmt)
        all_written.extend(written)
        destinations.append(destination)

    # ── Summary ────────────────────────────────────────────────────────────────
    table = Table(title="Crawl summary", show_header=True, header_style="bold magenta")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Site", site.title)
    table.add_row("Type", site_type.value)
    table.add_row("Pages collected", str(len(site.pages)))
    table.add_row("Files written", str(len(all_written)))
    console.print(table)
    for destination in destinations:
        console.print(f"  Output    : {destination}")
    console.print("\n[bold green]Done![/]")
