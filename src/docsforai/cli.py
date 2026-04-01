"""DocsForAI CLI."""
from __future__ import annotations

import asyncio
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
    help="Specialized documentation-site crawler, optimised for AI consumption.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True)


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


@app.command()
def crawl(
    url: Annotated[str, typer.Argument(help="URL of the documentation site to crawl.")],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Output directory.", show_default=True),
    ] = Path("./output"),
    fmt: Annotated[
        list[ExportFormat],
        typer.Option(
            "--format", "-f",
            help="Export format (repeatable). Choices: multi-md · single-md · jsonl",
            show_default=True,
        ),
    ] = [ExportFormat.MULTI_MD],  # noqa: B006
    site_type: Annotated[
        Optional[SiteType],
        typer.Option("--type", "-t", help="Force a specific site type (skip auto-detection)."),
    ] = None,
    concurrency: Annotated[int, typer.Option(help="Max concurrent HTTP requests.")] = 5,
    delay: Annotated[float, typer.Option(help="Seconds to wait between requests.")] = 0.1,
    timeout: Annotated[float, typer.Option(help="HTTP request timeout in seconds.")] = 30.0,
    max_pages: Annotated[int, typer.Option(help="Max pages to crawl (generic mode only).")] = 200,
) -> None:
    """Crawl a documentation site and export it for AI consumption."""
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
    for fmt in formats:
        written = export(site, output / fmt.value, fmt)
        all_written.extend(written)

    # ── Summary ────────────────────────────────────────────────────────────────
    table = Table(title="Crawl summary", show_header=True, header_style="bold magenta")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Site", site.title)
    table.add_row("Type", site_type.value)
    table.add_row("Pages collected", str(len(site.pages)))
    table.add_row("Files written", str(len(all_written)))
    console.print(table)
    console.print(f"\n[bold green]Done![/] Output → {output}")
