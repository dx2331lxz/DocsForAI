# DocsForAI

> **专为文档网站设计的轻量爬虫，让 AI 可以高质量地阅读任何文档。**

[![PyPI version](https://img.shields.io/pypi/v/docsforai.svg)](https://pypi.org/project/docsforai/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 为什么不用通用爬虫？

通用爬虫把所有网站一视同仁。而 VitePress、Docsify 等文档站有明确的结构约定：

| 特性                        | 通用爬虫            | DocsForAI                     |
| --------------------------- | ------------------- | ----------------------------- |
| 导航层级                    | ❌ 需要猜            | ✅ 直接读侧边栏 / llms.txt     |
| Docsify 原始 Markdown       | ❌ 解析渲染后的 HTML | ✅ 直接拉取 `.md` 文件         |
| Mintlify 一次请求获全量内容 | ❌ 逐页爬取          | ✅ 解析 `llms-full.txt` 即完成 |
| 代码块语言标注              | ❌ 常丢失            | ✅ 保留 `language-*` 类名      |
| 输出格式                    | ❌ 单一              | ✅ 多 MD / 单 MD / JSONL       |
| 依赖                        | 通常很重            | ✅ 仅 5 个运行时依赖           |

---

## 安装

```bash
pip install docsforai
```

或从源码安装：

```bash
git clone https://github.com/dx2331lxz/DocsForAI.git
cd DocsForAI
pip install -e .
```

---

## 快速开始

```bash
# 爬取 VitePress 文档，输出为多个 MD 文件（默认）
docsforai crawl https://vitepress.dev/guide -o ./output

# 输出为单一大文件（直接投喂给 LLM）
docsforai crawl https://vitepress.dev/guide -f single-md -o ./output

# 输出为 JSONL（适合向量数据库 / 微调数据集）
docsforai crawl https://docsify.js.org -f jsonl -o ./output

# 同时导出多种格式
docsforai crawl https://vitepress.dev/guide -f multi-md -f jsonl -o ./output

# 强制指定站点类型（跳过自动检测）
docsforai crawl https://example.com/docs --type vitepress
```

---

## 支持的站点类型

### VitePress
- 自动识别 `.VPSidebar` 结构，完整还原章节层级
- 提取 `.vp-doc` 内容区，剔除导航栏、页脚等噪音
- 保留代码块的语言标注

### Docsify
- 直接解析 `_sidebar.md` 获取目录树
- **跳过 HTML 渲染**，直接拉取原始 `.md` 文件，速度最快、内容最准
- 正确处理哈希路由（`/#/guide` → `/guide.md`）

### Mintlify
- 通过响应头 `x-llms-txt` 自动识别
- **优先读取 `llms-full.txt`**：一次 HTTP 请求即获取所有页面的完整内容，无需遍历
- 回退到 `llms.txt` 索引 + 并发拉取各页 `.md` 原始文件
- 零 HTML 解析，内容干净准确

### Docusaurus
- Docusaurus 是一个常见的文档框架；DocsForAI 当前通过 `docusaurus` 爬虫兼容 Docusaurus 网站。
- 测试：已对 `https://docusaurus.io/docs` 运行爬虫（`docsforai crawl https://docusaurus.io/docs -o ./output_docusaurus`），采集到 92 页并写入 `output_docusaurus/multi-md/`。

### Feishu (飞书开放平台)
- 专用爬虫：通过飞书开放平台暴露的内部 API 拉取完整的目录树和原始 Markdown（`/document/<fullpath>.md`）。
- 优点：一次性读取目录树并并发下载所有 `.md`，内容干净且保留原始 Markdown 结构。

### Generic（通用兜底）
- BFS 广度优先遍历同域链接
- 启发式识别主内容区（`main`、`article`、`.content` 等）
- 可通过 `--max-pages` 限制爬取深度


---

## 导出格式

| 格式       | CLI 参数       | 适用场景               |
| ---------- | -------------- | ---------------------- |
| 多 MD 文件 | `-f multi-md`  | RAG 检索、按章节管理   |
| 单 MD 文件 | `-f single-md` | 直接粘贴到 LLM 上下文  |
| JSONL      | `-f jsonl`     | 向量数据库、微调数据集 |

**multi-md** 输出示例：
```
output/multi-md/
├── guide/
│   ├── getting-started.md
│   └── configuration.md
└── reference/
    └── api.md
```

**JSONL** 记录示例：
```json
{"source": "https://...", "title": "Getting Started", "breadcrumb": ["Guide", "Getting Started"], "content": "# Getting Started\n...", "site": "VitePress", "site_type": "vitepress"}
```

---

## 完整 CLI 参数

```
docsforai crawl [OPTIONS] URL

Arguments:
  URL  文档站点 URL

Options:
  -o, --output   PATH    输出目录 [default: ./output]
  -f, --format   FORMAT  导出格式，可重复使用 (multi-md|single-md|jsonl)
  -t, --type     TYPE    强制站点类型 (vitepress|docsify|mintlify|generic)
  --concurrency  INT     最大并发请求数 [default: 5]
  --delay        FLOAT   请求间隔秒数 [default: 0.1]
  --timeout      FLOAT   HTTP 超时秒数 [default: 30.0]
  --max-pages    INT     最大爬取页数，仅 generic 模式 [default: 200]
  -V, --version          显示版本
```

---

## 项目结构

```
src/docsforai/
├── cli.py              # Typer CLI 入口
├── detector.py         # 自动识别站点类型
├── converter.py        # HTML → Markdown（保留代码块语言标注）
├── models.py           # 数据模型：DocSite / DocPage / NavItem
├── crawlers/
│   ├── base.py         # 抽象基类（限速、并发控制）
│   ├── vitepress.py    # VitePress 专用爬虫
│   ├── docsify.py      # Docsify 专用爬虫（直取 .md 源文件）
│   ├── mintlify.py     # Mintlify 专用爬虫（llms-full.txt / llms.txt）
│   └── generic.py      # 通用 BFS 兜底爬虫
└── exporters/
    ├── multi_md.py     # 多 MD 文件导出
    ├── single_md.py    # 单 MD 文件导出
    └── llm.py          # JSONL 导出
```

---

## 运行时依赖

| 包                        | 用途                 |
| ------------------------- | -------------------- |
| `httpx`                   | 异步 HTTP 客户端     |
| `beautifulsoup4` + `lxml` | HTML 解析            |
| `markdownify`             | HTML → Markdown 转换 |
| `typer` + `rich`          | CLI 界面             |

---

## 开发安装

```bash
git clone https://github.com/dx2331lxz/DocsForAI.git
cd DocsForAI
pip install -e ".[dev]"
pytest
```