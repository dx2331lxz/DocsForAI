# DocsForAI

![DocsForAI Banner](assets/docsforai-banner.png)

> **A lightweight documentation crawler optimised for AI consumption.**

[![PyPI version](https://img.shields.io/pypi/v/docsforai.svg)](https://pypi.org/project/docsforai/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[中文](README.md) | **English**

![1Panel Demo](assets/demo-1panel.gif)

DocsForAI automatically detects the documentation framework of any site, extracts clean Markdown content structured by section, and outputs it in formats ready for LLMs and vector databases.

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Supported Frameworks](#supported-frameworks)
- [Output Formats](#output-formats)
- [CLI Reference](#cli-reference)
- [Why Not a Generic Crawler?](#why-not-a-generic-crawler)
- [Project Structure](#project-structure)
- [Development](#development)
- [License](#license)

---

## Features

- 🔍 **Auto-detection** — Recognises 10 popular documentation frameworks with no configuration
- 🧹 **Clean content** — Framework-specific parsing removes nav bars, sidebars, footers, and other noise
- 📁 **Multiple outputs** — multi-MD (RAG), single-MD (LLM context), and JSONL (vector DB)
- ⚡ **Concurrent fetching** — Async HTTP with configurable concurrency and rate limiting
- 🛡️ **Cloudflare bypass** — Detects 403 challenges and automatically falls back to system `curl`
- 🪶 **Minimal dependencies** — Only 5 runtime packages

---

## Installation

**Requires:** Python 3.10+

```bash
pip install docsforai
```

Install from source (for development):

```bash
git clone https://github.com/dx2331lxz/DocsForAI.git
cd DocsForAI
pip install -e .
```

---

## Quick Start

```bash
# Basic crawl — auto-detect framework, output as separate MD files (default)
docsforai crawl https://vitepress.dev/guide -o ./output

# Single merged file — paste directly into an LLM context
docsforai crawl https://vitepress.dev/guide -f single-md -o ./output

# JSONL — ingest into a vector database or fine-tuning dataset
docsforai crawl https://docsify.js.org -f jsonl -o ./output

# Multiple formats at once
docsforai crawl https://vitepress.dev/guide -f multi-md -f jsonl -o ./output

# Force a framework type when auto-detection is incorrect
docsforai crawl https://example.com/docs --type vitepress
```

After the crawl, the terminal shows a summary:

```
  Detecting site type for https://agpt.co/docs… gitbook

         Crawl summary
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Metric          ┃   Value ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ Site            │ AutoGPT │
│ Type            │ gitbook │
│ Pages collected │     174 │
│ Files written   │     174 │
└─────────────────┴─────────┘
  Output    : ./output/autogpt
Done!
```

---

## Supported Frameworks

All frameworks are detected automatically — no configuration needed:

| Framework       | Detection signals                                     | Core strategy                                                                   | Tested on (pages)               |
| --------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------------- |
| **VitePress**   | `.VPSidebar` CSS class / generator meta               | Parse sidebar JSON; extract `.vp-doc`                                           | vitepress.dev                   |
| **Docsify**     | `$docsify` global variable                            | Fetch raw `.md` source files directly, skip HTML rendering                      | docsify.js.org                  |
| **Mintlify**    | `x-llms-txt` response header                          | Read `llms-full.txt` in one request for all content                             | mintlify.com/docs               |
| **Docusaurus**  | generator meta / `.theme-doc-sidebar-container`       | Parse sidebar; extract main content                                             | docusaurus.io/docs (92)         |
| **mdBook**      | `#mdbook-sidebar` / `ol.chapter`                      | Parse static `toc.html` for the full ordered chapter tree                       | rust-lang.github.io/mdBook (31) |
| **MkDocs**      | generator meta / `.md-nav--primary` / `#toc-collapse` | Material & default theme support; Cloudflare bypass built-in                    | docs.pydantic.dev (88)          |
| **Starlight**   | `#starlight__sidebar` / `.sl-markdown-content`        | Parse `<details>/<summary>` grouped nav; extract `[data-pagefind-body]`         | starlight.astro.build (35)      |
| **GitBook**     | generator meta `GitBook` / `gitbook.com` scripts      | Discover all pages via `sitemap.xml`; remove heading anchor icons and SVG noise | agpt.co/docs (174)              |
| **Feishu Docs** | Domain `open.feishu.cn`                               | Call Feishu internal API to fetch the full directory tree and raw Markdown      | Feishu Open Platform            |
| **Generic**     | Fallback for all other sites                          | BFS crawl of same-domain links; heuristic main-content detection                | Any docs site                   |

> Don't see the framework you need? Open an [Issue](https://github.com/dx2331lxz/DocsForAI/issues) or submit a PR.

---

## Output Formats

### multi-md (default)

One `.md` file per page, mirroring the original site's directory structure. Best for RAG pipelines and per-chapter management.
When you pass `-o ./output`, files are written directly to `./output/<site-name>/` without creating an extra `multi-md/` directory.

```
output/vitepress/
├── guide/
│   ├── getting-started.md
│   └── configuration.md
└── reference/
    └── api.md
```

Each file includes a YAML front matter header:

```markdown
---
title: "Getting Started"
url: "https://example.com/guide/getting-started"
breadcrumb: "Guide > Getting Started"
order: 3
---

# Getting Started
...
```

### single-md

All pages merged into one file in navigation order. Best for pasting directly into an LLM chat context.
When you pass `-o ./output`, the output file is `./output/<site-name>.md`.

### jsonl

One JSON record per line. Best for bulk import into vector databases or building fine-tuning datasets.
When you pass `-o ./output`, the output file is `./output/<site-name>.jsonl`.

```json
{"source": "https://...", "title": "Getting Started", "breadcrumb": ["Guide", "Getting Started"], "content": "# Getting Started\n...", "site": "VitePress", "site_type": "vitepress"}
```

---

## CLI Reference

```
docsforai crawl [OPTIONS] URL

Arguments:
  URL                    URL of the documentation site to crawl.
                         Any page works; the root or docs index is recommended.

Options:
  -o, --output   PATH    Base output directory.
                         multi-md  -> <output>/<site-name>/
                         single-md -> <output>/<site-name>.md
                         jsonl     -> <output>/<site-name>.jsonl
                         [default: ./output]

  -f, --format   FORMAT  Export format. Repeat to produce multiple formats.
                         Choices:
                           multi-md  — one .md per page under <output>/<site-name>/ (default)
                           single-md — all pages merged into <output>/<site-name>.md
                           jsonl     — one JSON record per line in <output>/<site-name>.jsonl

  -t, --type     TYPE    Force a framework type and skip auto-detection.
                         Useful when auto-detection is incorrect.
                         Choices: vitepress · docsify · mintlify · feishu-docs
                                  docusaurus · mdbook · mkdocs · starlight
                                  gitbook · generic

  --concurrency  INT     Max pages fetched in parallel.
                         Raise for speed; lower to avoid rate-limiting.
                         [default: 5]

  --delay        FLOAT   Seconds to sleep between requests (per worker).
                         Increase to avoid triggering rate limits.
                         [default: 0.1]

  --timeout      FLOAT   HTTP request timeout in seconds.
                         Increase for slow or very large sites.
                         [default: 30.0]

  --max-pages    INT     Max pages to collect (generic mode only).
                         [default: 200]

  -V, --version          Show version and exit.
  --help                 Show this message and exit.
```

**Examples:**

```bash
# Auto-detect, output as multiple MD files
docsforai crawl https://vitepress.dev/guide -o ./output

# Single merged file for LLM
docsforai crawl https://vitepress.dev/guide -f single-md -o ./output

# JSONL for vector DB
docsforai crawl https://docsify.js.org -f jsonl -o ./output

# Multiple formats at once
docsforai crawl https://vitepress.dev/guide -f multi-md -f jsonl -o ./output

# Force framework type
docsforai crawl https://example.com/docs --type mkdocs

# Higher concurrency for large sites
docsforai crawl https://agpt.co/docs --concurrency 10 -o ./output

# Cap generic BFS at 50 pages
docsforai crawl https://example.com/docs --type generic --max-pages 50
```

---

## Why Not a Generic Crawler?

Generic crawlers treat every site the same. Popular documentation frameworks have well-defined structural conventions that can be exploited directly:

| Scenario                   | Generic crawler        | DocsForAI                                   |
| -------------------------- | ---------------------- | ------------------------------------------- |
| Navigation hierarchy       | ❌ Must guess           | ✅ Reads sidebar structure directly          |
| Docsify raw content        | ❌ Parses rendered HTML | ✅ Fetches `.md` source files                |
| Mintlify full content      | ❌ Crawls page by page  | ✅ One request reads `llms-full.txt`         |
| Code block language tags   | ❌ Often lost           | ✅ Preserved via `language-*` class names    |
| Cloudflare-protected sites | ❌ Fails with 403       | ✅ Falls back to system `curl` automatically |
| Output formats             | ❌ Usually one          | ✅ multi-md / single-md / jsonl              |

---

## Project Structure

```
src/docsforai/
├── cli.py              # Typer CLI entry point
├── detector.py         # Framework auto-detection
├── converter.py        # HTML → Markdown (preserves code block language tags)
├── models.py           # Data models: DocSite / DocPage / SiteType
├── crawlers/
│   ├── base.py         # Abstract base class (concurrency, rate-limiting, curl fallback)
│   ├── vitepress.py
│   ├── docsify.py
│   ├── mintlify.py
│   ├── docusaurus.py
│   ├── mdbook.py
│   ├── mkdocs.py
│   ├── starlight.py
│   ├── gitbook.py
│   ├── feishu.py
│   └── generic.py
└── exporters/
    ├── multi_md.py     # Multi-file MD export
    ├── single_md.py    # Single-file MD export
    └── llm.py          # JSONL export
```

---

## Development

```bash
git clone https://github.com/dx2331lxz/DocsForAI.git
cd DocsForAI
pip install -e ".[dev]"
pytest
```

**Steps to add a new framework:**

1. Add a new value to the `SiteType` enum in `models.py`
2. Create a new crawler class in `crawlers/` inheriting from `BaseCrawler` and implementing `crawl()`
3. Add detection logic in `detector.py` (generator meta, unique CSS classes/IDs, etc.)
4. Register the new crawler in the `make_crawler()` factory in `crawlers/__init__.py`

---

## License

[MIT](LICENSE)
