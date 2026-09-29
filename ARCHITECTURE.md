# 架构设计与演进路线（方案文档）

> **提出**：2026-09-29 ｜ **状态**：📝 **方案稿 —— 待定方向后分期开发**
> **范围**：本文档回答「这套服务要做成什么形态、怎么和 Zotero 联动、要不要自建服务器」。
> **原则**：本文档**只做设计**，不做实现；所有开源方案**均已实测核实**（见 §附录 B）。

---

## 📌 摘要（一页读懂）

### 老板的诉求（一句话）
> 把现在这个「**只读的论文列表**」，做成一个能**被 Agent 调用、能和 Zotero 联动**的服务。

### 三个核心结论

| # | 结论 | 说明 |
|---|------|------|
| **1** | **缺的是一个「服务层」，不是更多数据源** | 现在是「单向只读静态展示」；要的是「双向、可查询、可写、可互通」。差距的本质是**一个 MCP 服务** |
| **2** | ⭐ **Zotero 同步不用自己搭** | Zotero 官方就有 Web API + 云同步。拿一个 API Key，任何地方都能读写 → **省掉整个同步模块** |
| **3** | ⭐ **Zotero 通用能力不用自己写** | 开源已有成熟 MCP（搜索/读全文/**标注读写**/管理集合）。**我们只需写本领域特有的 3 件事**：分类总览表、媒体动态、论文推送 |

### 怎么走（推荐路径）

```
第 1 期  配置化 + 密钥        ← 地基（后面每期都要加源，不做这个每次都要改代码）
第 2 期  MCP 服务（本地）      ← ⭐ 核心缺口，零安全风险、立刻可用
第 3 期  Zotero 联动          ← 建目录/分类/总览表/读标注
第 4 期  信息源 + 媒体页      ← ⚠️ 风险最高，放后面
第 5 期  网站写笔记 + 远程 MCP ← 涉及公网安全，可选
```

### 一句话
> **采集继续留在 GitHub Actions（免费免维护）；
> 新增一个 MCP Server 当服务层；
> Zotero 生态能复用的全部复用；
> 只自己写"含本领域知识的"那部分。**

---

## 🧭 目录

| 章节 | 内容 |
|------|------|
| [一、需求拆解](#一需求拆解boss-原话--可落地的-6-个模块) | 老板原话 → 7 项需求 → 6 个模块 |
| [二、现状与差距](#二现状与差距) | 现在有什么、缺什么 |
| [三、开源方案选型](#三开源方案选型-全部已逐一核实非凭印象) | 信息源 / Zotero / MCP 三层，全部实测核实 |
| [四、架构设计](#四-架构设计) | ⭐ 分层图 + 4 大关键决策 + 配置化 + MCP 工具设计 |
| [五、分期落地](#五分期落地每期一个可验收的产出) | 5 期，每期独立可用 |
| [六、风险与取舍](#六️-风险与取舍必须让-boss-知道) | 7 项风险 |
| [七、待决策项](#七-待-boss-决策的点) | ⭐ 需要老板定的 4 件事 |
| [八、本方案不做什么](#八️-明确本方案不做什么防止范围膨胀) | 6 项明确的"不做"，防范围膨胀 |
| [附录 A：术语表](#附录-a术语表) | MCP / Zotero / 传输方式等名词解释 |
| [附录 B：参考来源](#附录-b参考来源全部实测核实2026-09-29) | 全部实测核实记录 |
| [附录 C：变更记录](#附录-c变更记录) | 文档修订历史 |

---

## 一、需求拆解（Boss 原话 → 可落地的 6 个模块）

| # | 原话要点 | 拆成什么 |
|---|---------|---------|
| 1 | 「论文源与信息源这个需要扩展，一些数据源可能需要密钥等这些也要支持吧……可以有个可配置的文件配置数据源」 | **A. 数据源配置化 + 密钥管理** |
| 2 | 「集成一些信息源爬取的工具或技能……小红书、知乎、公众号等也可以支持一下作为可选项」 | **B. 信息源扩展（社媒，可选）** |
| 3 | 「可以在网页再建立一个页面支持查看媒体关于这个主题的最新消息情况，按照发表日期来进行梳理总结」 | **C. 网站新增「媒体动态」页** |
| 4 | 「希望这个项目支持 MCP 协议工具，提供一些 api 接口进行论文调研、论文整理等信息获取，便于 agent 或其他工具使用」 | **D. MCP 服务 + API** |
| 5 | 「便于支持 zotero 建立一个目录存放跟踪的这些论文，目录下再进行分类，另外每个分类有介绍性的总览所有论文的一张表」 | **E. Zotero 目录/分类/总览表** |
| 6 | 「网站上记录灵感标注笔记……和 zotero 联动支持 zotero 中查看与标注？自己在 zotero 的标注也希望能够提供 MCP 工具进行查看？」 | **F. 笔记/标注双向联动** |
| 7 | 「这个应该是要将 awesome 这个服务跑在一个地方，然后向外再提供一些接口？其他地方的 zotero 通过这些接口和部署服务的地方进行信息同步与交互？」 | **G. 部署拓扑决策** |

---

## 二、现状与差距

### 现状（2026-09-29）

```
采集   pipeline.py（4 采集源 + 3 增强源，全部**硬编码**在 .py 里）
存储   data/papers.db（SQLite）+ docs/data/papers.json（网站用）
展示   docs/index.html（Pages 静态页，**只读**）
调度   GitHub Actions（每周一，云端，零成本）
```

### 差距（对照需求）

| 需求 | 现状 | 差距 |
|------|------|------|
| A 配置化 | ❌ 零配置文件，全硬编码 | 加源要改代码；**没有密钥概念** |
| B 社媒源 | ❌ 只有学术源 | 完全没有 |
| C 媒体页 | ❌ 只有论文页 | 没有 |
| D MCP/API | ❌ 静态 JSON 而已 | **不能查询、不能写** |
| E Zotero | ❌ 无关联 | 没有 |
| F 笔记标注 | ❌ 网站纯只读 | **没有写能力** |
| G 部署 | ⚠️ Actions + Pages | Actions **不是常驻服务**，无法提供 API |

> ⭐ **一句话**：现在的系统是「**单向只读的静态展示**」，
> 而 Boss 要的是「**双向、可查询、可写、可与外部工具互通的服务**」。
> 差距的本质是 **一个「服务层」** —— 这是本次演进的**主线**。

---

## 三、开源方案选型（⭐ 全部已逐一核实，非凭印象）

### 3.1 信息源层

| 项目 | ⭐ | 许可 | 覆盖 | 结论 |
|------|----|------|------|------|
| **DIYgod/RSSHub** | 46.3k | AGPL-3.0 | 1000 个平台目录 | ⭐ **主力**。但⚠️ **实测其 知乎/小红书/微博 路由已不存在**（应平台压力下架） |
| **tmwgsicp/ForgeRSS** | 129 | AGPL-3.0 | 抖音/快手/**小红书**/B站/**知乎**/小宇宙/知识星球 | ⭐ 补齐 RSSHub 缺的中文平台 |
| **zhu327/rss** | 542 | 无 | 微博/微信公众号/**知乎日报** | 补公众号/知乎 |
| **yllhwa/RSSWorker** | 800 | MIT | Cloudflare Worker 上的 RSS 生成 | 备选（Serverless、零成本） |
| **JackyST0/hotpush** | 192 | MIT | 13+ 平台热榜聚合 | 备选（热榜 ≠ 内容） |
| **NanmiCoder/MediaCrawler** | 66.0k | ⚠️**非商业学习许可** | 小红书/抖音/快手/B站/微博/贴吧/**知乎** | ⚠️ **仅限学习研究，禁止大规模爬取/商用** —— 可用但要知道边界 |

> ⚠️ **必须诚实说明的三点**：
> 1. **RSSHub 已经不带知乎/小红书/微博了** —— 别指望它一个搞定。
> 2. **MediaCrawler 是「非商业学习许可证」**，且明确禁止大规模爬取 ——
>    个人学习研究可用，但**不能作为对外服务的数据源**，也不该高频跑。
> 3. **社媒反爬 + 法律风险真实存在** —— 所以这一块必须设计成
>    ⭐ **「可选模块 + 默认关闭 + 低频」**，且**与论文数据隔离**。

### 3.2 Zotero 联动层（⭐ 这条最省事，因为生态已经很成熟）

| 项目 | ⭐ | 许可 | 形态 | 能力 |
|------|----|------|------|------|
| **54yyyu/zotero-mcp** | 5.2k | MIT | MCP server（stdio）+ CLI | 搜索/读全文/**标注(高亮/区域框/笔记)**/按 DOI·URL·ISBN·BibTeX 写入/管理集合与标签 |
| **cookjohn/zotero-mcp** | 1.2k | MIT | ⭐ **Zotero 插件**（装进 Zotero，内置 MCP server，**Streamable HTTP :23120**） | 多维搜索/全文与快照/**标注分析（按颜色·标签·关键词）**/集合浏览/语义搜索/写入 |
| **papersgpt-for-zotero** | 2.7k | AGPL-3.0 | Zotero 插件 + 本地 RAG | 万篇 PDF/7 分钟建索引、19.5ms 检索、**全离线** |
| **blazickjp/arxiv-mcp-server** | 3.2k | Apache-2.0 | MCP server | arXiv LaTeX 分段读 + BibTeX |
| **Zotero 官方 API** | — | — | Web API v3 + Local API | `https://api.zotero.org`（API Key）｜ `http://localhost:23119/api/`（本地） |

#### ⭐ 已实测核实的四条硬事实（2026-09-29，避免设计建立在猜测上）

| # | 事实 | 证据 |
|---|------|------|
| **1** | ⭐ **Local API 真实存在且官方文档化** | 官方文档 `/support/dev/web_api/v3/local_api`：base URL `http://localhost:23119/api/`，**离线可用、无速率限制、通常比 Web API 快得多** |
| **2** | **Local API 需要在 Zotero 里显式开启** | Settings → Advanced → *"Allow other applications on this computer to communicate with Zotero"*；未开启时请求返回 **403** |
| **3** | **Local API 支持写入**（Zotero 10+） | 文档明确：Basics / **Write Requests (Zotero 10+)** / File Uploads / Full-Text Content 端点**在 localhost 前缀下行为一致** |
| **4** | ⭐ **「读标注」是真实现的，不是宣传** | `54yyyu/zotero-mcp` 源码含 `pdfannots_downloader.py` + `pdfannots_helper.py`，实测调用 **`pdfannots2json`**（mgmeyers/pdfannots2json v1.0.15）提取 PDF 标注 |
| **5** | **Zotero 免费额度 = 300 MB**（不是我凭印象） | 官方定价页实测：`300 MB Free ｜ 2 GB $20/year ｜ 6 GB $60/year ｜ Unlimited` |

> 🎯 **第 1-3 条决定了架构成立**：
> **「本地 Zotero 的标注」确实可以被程序读到** ——
> 这条路（Local API）**不依赖网络、不限速、还能写**，
> 所以"网站 ↔ Zotero 笔记双向"是可行的。
>
> ⚠️ **第 2 条是实施时的第一个坑**：**不开那个开关，一切返回 403**。
> 一定要写进操作步骤（否则会排查半天）。

> ⭐⭐ **关键结论**：**Boss 想要的 Zotero 能力，开源已经有现成的，不必自己造。**
> - 「目录存放 + 分类」→ MCP 的 **collections 管理**
> - 「每个分类的总览表」→ 生成 note 挂到 collection（**这个要我们自己写**，因为是本领域特有的）
> - 「Zotero 中查看与标注」→ 已支持
> - 「标注通过 MCP 查看」→ **已支持**（54yyyu / cookjohn 两者都能读标注）
>
> 🎯 **所以我们的角色是「桥」而不是「重造」**：
> 把**本领域论文列表**接进 Zotero，把**总览表**生成好，
> 其余交给成熟项目。

### 3.3 MCP 协议层

| 项目 | ⭐ | 用途 |
|------|----|------|
| **modelcontextprotocol/python-sdk** | 24.4k | 官方 Python SDK |
| **PrefectHQ/fastmcp** | 27.9k | ⭐ 写 MCP server 最省事的框架 |
| **modelcontextprotocol/servers** | 90.7k | 官方 server 合集（参考实现） |

**传输方式**（决定部署拓扑）：
```
stdio              本机进程间 → ⭐ 零风险、零配置（适合 Boss 本机 Agent）
Streamable HTTP    标准网络传输 → ⭐ 可远程（cookjohn 插件就用这个）
SSE                旧版，逐步被 Streamable HTTP 取代
```

---

## 四、⭐ 架构设计

### 4.1 核心判断：**分工，而不是把所有东西塞进一个服务器**

Boss 问的是「要不要把服务跑在一个地方，然后向外提供接口」。
**准确答案是：要提供服务层，但不必自建 Zotero 同步 —— 因为 Zotero 自己就有云。**

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ① 采集层 —— GitHub Actions（保持现状，零成本零维护）                    │
│     weekly-update.yml 每周一 10:17                                       │
│     论文源（OpenAlex/Crossref/OpenReview/HF）                            │
│     信息源（RSSHub / ForgeRSS）← 新增，可配置、可关闭                     │
│                          ↓ 产出                                          │
│     data/papers.db  +  docs/data/papers.json  +  media.json（新增）       │
└─────────────────────────────────────────────────────────────────────────┘
                                   ↓ git push
┌─────────────────────────────────────────────────────────────────────────┐
│  ② 存储/分发层 —— GitHub 仓库 + GitHub Pages（零成本）                   │
│     · Pages 静态站（只读展示，今天已有）                                  │
│     · ⭐ 同时充当 **API 的数据源**（裸 JSON over HTTPS，可被任何程序读）   │
└─────────────────────────────────────────────────────────────────────────┘
                    ↓ 读取                              ↑ 写（笔记/灵感）
┌─────────────────────────────────────────────────────────────────────────┐
│  ③ 服务层 —— ⭐ 本项目的 MCP Server（唯一新增的核心组件）                │
│                                                                         │
│   工具集：                                                               │
│     · search_papers / get_paper          论文调研                        │
│     · list_categories / category_overview ⭐ 分类总览表                  │
│     · latest_media                       媒体动态                        │
│     · export_bibtex / push_to_zotero     论文整理 → Zotero               │
│     · add_note / list_notes              灵感笔记                        │
│     · list_zotero_annotations            读 Zotero 标注                  │
│                                                                         │
│   两种跑法（同一个代码）：                                                │
│     stdio   → Boss 本机（给本地 Agent 用，零风险，立刻可用）              │
│     HTTP    → 阿里云 ECS（给手机/外部 Agent 用，需 token 认证）           │
└─────────────────────────────────────────────────────────────────────────┘
          ↓ MCP/HTTP                                    ↑ Zotero Web API
┌───────────────────────────────┐   ┌─────────────────────────────────────┐
│  ④ 终端层                      │   │  ⑤ Zotero（⭐ 用官方云做枢纽）       │
│     · 网站（Pages，展示+写笔记）│   │                                     │
│     · Boss 本机 Zotero 插件     │←→│   Web API: api.zotero.org（Key）     │
│     · Claude Code / Hermes      │   │   Local API: localhost:23119         │
│     · 其他任意 MCP 客户端       │   │   已装 cookjohn/zotero-mcp 插件       │
└───────────────────────────────┘   └─────────────────────────────────────┘
```

### 4.2 ⭐⭐ 关键设计决策（这几条决定"怎么搞"）

#### 决策 1：Zotero 同步**不自己搭** —— 用官方云

> Boss 设想的「服务跑在一处 + 其他地方 Zotero 同步交互」，
> **Zotero 官方已经提供了**：Zotero Web API + 客户端自带的云同步。
>
> ```
> 任何设备/服务 → Zotero Web API（api.zotero.org，API Key 认证）
>                       ↕ 官方自动同步
>               Boss 的 Zotero 桌面端
> ```
>
> ⭐ **所以我们不需要写同步逻辑** —— 只需要拿一个 **Zotero API Key**，
> 就能在任何地方（包括阿里云服务器）读写、读标注。
> 🎯 **这一个决策省掉了整个"同步服务"的工作量。**

**代价**：免费额度 300MB（附件）。论文条目本身很小，够用；
大附件建议只放链接不放 PDF，或升级存储。

#### 决策 2：**MCP 工具不重造 Zotero 能力** —— 分成两层

```
第 1 层  Zotero 通用能力（搜索/读全文/标注/写条目）
         → ⭐ 直接用现成的 zotero-mcp / Zotero 插件，别自己写
第 2 层  ⭐ 本领域特有逻辑（这才是我们要写的）
         · 把跟踪列表按分类推成 Zotero collection
         · 每类生成一张「总览表」note
         · 媒体动态
         · 本领域检索与过滤
         · 网站笔记 ↔ Zotero note
```

> ⭐ **判据**：**凡是不含"zero-shot navigation"知识的，都不该我们自己写。**

#### 决策 3：**笔记的"单一事实来源"放 Zotero**

> Boss 问「网站记录灵感 + Zotero 中查看与标注」——
> ⚠️ **两边都能写就会冲突**（同步是分布式难题）。
>
> ✅ **做法**：**Zotero 是笔记的主库**，网站只是它的一个视图 + 入口。
> ```
> 网站在线写笔记 → MCP/API → 写进 Zotero（note）
> Zotero 里标注     → MCP/API → 网站展示
> ```
> 🎯 **好处**：笔记天然和论文在同一个地方，不用维护两套数据。

#### 决策 4：社媒源**默认关闭、与论文隔离**

```
配置  media_sources.enabled = false   ← 默认关
数据  media.json（独立文件）           ← 不混进 papers.json
展示  /media 独立页面                  ← 不污染论文列表
```
> ⚠️ 理由：**法律风险 + 反爬不稳定 + 质量参差**。
> 做成"可选"而不是"默认"，才经得起时间。

### 4.3 落地形态：`config/` 目录（回答需求 A）

```
config/
├── sources.toml          # 数据源总配置（这是 Boss 要的"可配置文件"）
├── secrets.env.example   # 密钥模板（真文件不进 git）
└── profile/
    ├── default.toml      # 默认档：只跑学术源
    └── full.toml         # 全量档：含社媒
```

**sources.toml 长这样**（草案）：
```toml
[settings]
lookback_days = 21
budget_seconds = 600

# ── 学术源（默认启用）──
[sources.openalex]
enabled = true
type = "keyword"          # keyword = 按检索词逐个查
per_page = 200
interval = 1.5

[sources.crossref]
enabled = true
type = "keyword"
rows = 60

[sources.openreview]
enabled = true
type = "aggregate"        # aggregate = 每轮只调一次
conferences = ["ICLR", "NeurIPS", "CoRL"]
max_age_days = 550
terms = ["zero-shot navigation", "vision-language navigation"]

# ── 需要密钥的源（回答"密钥支持"）──
[sources.semanticscholar]
enabled = false
api_key_env = "SEMANTIC_SCHOLAR_API_KEY"   # ⭐ 从环境变量读，不写明文
note = "有 Key 才开搜索接口"

# ── 信息源 / 社媒（默认关闭）──
[media.rsshub]
enabled = false
base_url = "https://rsshub.app"
routes = ["wechat/freewechat/...", "36kr/newsflashes"]

[media.forgerss]
enabled = false
note = "补 RSSHub 缺的小红书/知乎"
```

**密钥管理**（⭐ 这是"支持密钥"的正确做法）：
```
本地  →  config/secrets.env（.gitignore 排除）或 ~/.hermes/secrets/
CI    →  GitHub Secrets（workflow 里 env: 注入）
服务器 →  环境变量 / systemd EnvironmentFile
代码   →  只读 os.environ["XXX"]，绝不硬编码
```

### 4.4 ⭐ MCP 工具设计（第 2 期的施工图）

> 分两组：**我们写的（本领域特有）** vs **复用现成的（Zotero 通用）**。

#### A. 我们写的工具（本领域特有）

| 工具 | 参数 | 返回 | 对应需求 |
|------|------|------|---------|
| `search_papers` | `query?`, `category?`, `topic?`, `venue?`, `year_from?`, `has_code?`, `min_citations?`, `limit?` | 论文列表（含标题/作者/年份/会议/级别/链接） | 论文调研 |
| `get_paper` | `id` 或 `title` | 单篇完整信息（含摘要、代码链接、项目页） | 论文调研 |
| `list_categories` | — | 全部分类 + 各类论文数 | 论文整理 |
| ⭐ `category_overview` | `category`, `format="markdown"` | ⭐ **该类论文的总览表**（按年份/会议分节 + 一句话简介） | ⭐ Boss 要的"分类总览表" |
| `latest_media` | `topic?`, `days=30`, `source?` | 媒体动态（标题/来源/日期/链接） | 媒体页 |
| ⭐ `export_bibtex` | `ids[]` 或筛选条件 | BibTeX 文本 | 论文整理 → Zotero |
| ⭐ `push_to_zotero` | `ids[]`, `category`, `create_collection=true` | 创建/复用 collection + 推入条目 | ⭐ Boss 要的"Zotero 建目录分类" |
| `add_note` | `content`, `attach_to?`（论文 id 或 collection） | 写入 Zotero note | 灵感笔记 |
| `list_notes` | `paper_id?`, `category?` | 笔记列表 | 灵感笔记 |
| ⭐ `list_zotero_annotations` | `paper_id?`, `collection?`, `color?` | ⭐ Zotero 里的标注/高亮 | ⭐ Boss 要的"看自己的标注" |

#### B. 复用现成的（不自己写）

```
搜索/读全文/Zotero 内部管理 → 54yyyu/zotero-mcp 或 cookjohn/zotero-mcp 插件
arXiv LaTeX 深读            → blazickjp/arxiv-mcp-server
```

> ⭐ **判据再强调一遍**：
> **凡是"换到任何领域都成立"的功能（读 Zotero、搜索、标注）→ 用现成的。**
> **凡是含"zero-shot navigation"知识的（分类/总览表/领域检索）→ 我们写。**

#### C. 传输与部署

```python
# 同一个代码，两种跑法（FastMCP 支持）
mcp = FastMCP("zero-shot-nav")

if __name__ == "__main__":
    if os.environ.get("MCP_TRANSPORT") == "http":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8211)
    else:
        mcp.run()          # stdio（默认，本地）
```

**配置示例**（Claude Code / Hermes 的 MCP 配置）：
```json
{
  "mcpServers": {
    "zsnav": {
      "command": "python3",
      "args": ["/home/yzq/projects/awesome-zero-shot-navigation/mcp_server/server.py"],
      "env": { "ZSNAV_DATA": "https://ser666.github.io/awesome-zero-shot-navigation/data/papers.json" }
    }
  }
}
```
> 💡 **注意 `ZSNAV_DATA`**：MCP server **不存数据**，
> 它直接读 Pages 上的 JSON —— 这样**采集（Actions）与服务（MCP）彻底解耦**，
> 服务重启不用重新抓数据，本地/云上跑同一个逻辑。

---

## 五、分期落地（每期一个可验收的产出）

> ⭐ **设计原则**：每期都**独立可用** —— 不要求"全做完才有价值"。

### 第 1 期 · 配置化 + 密钥（建议先做，地基）
```
产出  config/sources.toml + pipeline 读配置 + secrets 走环境变量
验收  改配置能开关一个源；无密钥时不崩（优雅降级）
成本  小（半天内）
```
> 📌 为什么先做：**后面每一期都要加源**，不做这个，每加一个源就改一次代码。

### 第 2 期 · MCP 服务（本地 stdio 版）
```
产出  mcp_server/（FastMCP）+ 6-8 个工具
      · search_papers / get_paper
      · list_categories / category_overview   ← ⭐ 总览表
      · export_bibtex
      · add_note / list_notes
验收  在 Claude Code / Hermes 里能直接调用查到论文、拿到分类总览表
成本  中
```
> ⭐ **这一期就是 Boss 要的「MCP 协议工具 + API 接口」的核心**。
> 先跑 stdio 本地版：**零安全风险、立刻能用**。

### 第 3 期 · Zotero 联动
```
产出  ① Boss 装 Zotero 插件（cookjohn/zotero-mcp）或 54yyyu/zotero-mcp
      ② 我们实现 push_to_zotero：按分类建 collection + 推论文
      ③ 我们实现 category_overview → 生成 Zotero note（总览表）
      ④ 读标注工具（list_zotero_annotations）
验收  Zotero 里出现「Zero-Shot Navigation」目录，下有分类，
      每类挂一张总览表；能读到自己在 Zotero 的标注
前置  ⭐ 需要 Boss 去 Zotero 官网申请一个 API Key（免费）
成本  中
```

### 第 4 期 · 信息源 + 媒体页
```
产出  config 里加 media 源 + /media 页面（按发表时间梳理）
验收  网页能看到该主题的媒体动态；论文列表不受影响
成本  中
```
> ⚠️ 放在这里而不是前面：**法律/稳定性风险最高，价值最不确定**，
> 先用 1-3 期把"论文主线"做扎实。

### 第 5 期 · 网站写能力 + 远程服务（可选）
```
产出  ① 网站笔记 UI → API → 写进 Zotero
      ② MCP 部署到阿里云（Streamable HTTP + token 认证）
验收  手机/外部 Agent 能查到、能写笔记
成本  中大（含安全加固）
前置  阿里云 ECS（已有）+ HTTPS（已有）
```

---

## 六、⚠️ 风险与取舍（必须让 Boss 知道）

| # | 风险 | 应对 |
|---|------|------|
| 1 | **社媒源的法律风险** —— MediaCrawler 是非商业学习许可；平台反爬与 ToS | ⭐ **默认关闭 + 低频 + 仅个人使用**；优先用 RSS 类（ForgeRSS）而非直接爬虫 |
| 2 | **RSSHub 已下架知乎/小红书/微博** | 用 ForgeRSS/zhu327 补，或接受"社媒覆盖不全" |
| 3 | **公网暴露 MCP 的安全** | 必须 token 认证 + 只读优先；写操作要独立密钥；别暴露 Zotero API Key |
| 4 | **Zotero 免费额度 300MB** | 只同步条目不放 PDF，或按需升级 |
| 5 | **三处都能写导致冲突**（网站/Zotero/MCP） | ⭐ **决策 3**：Zotero 是笔记唯一主库 |
| 6 | **复杂度膨胀** —— 容易做成"什么都有但都不好用" | ⭐ 每期独立可用；**默认关闭**新模块 |
| 7 | **我（小莫）的本机网络受限**（HuggingFace/arXiv 不通） | 采集仍在 Actions 跑；MCP 只读 Pages 的 JSON |

---

## 七、⭐ 待 Boss 决策的点

> 这 4 个定了，就可以开工第 1 期。

| # | 决策 | 选项 | 我的建议 |
|---|------|------|---------|
| **1** | **先做哪一期？** | ① 1→2→3 逐步 ② 直接上 2 ③ 全做 | ⭐ **① 1→2→3**（地基 → MCP → Zotero） |
| **2** | **社媒源要不要？** | ① 第 4 期做 ② 现在就做 ③ 不做 | ⭐ **① 放第 4 期**（风险最高、价值最不确定，先做扎实论文主线） |
| **3** | **愿不愿意申请 Zotero API Key + 装插件？** | ① 愿意 ② 先看看 | ⭐ **① 愿意**（都免费；不装的话第 3 期做不了） |
| **4** | **远程 MCP（公网）现在做还是以后？** | ① 以后 ② 现在 | ⭐ **① 以后**（本地版零安全风险，先验证有用再上公网） |

---

## 八、⚠️ 明确「本方案不做什么」（防止范围膨胀）

| 不做 | 为什么 |
|------|--------|
| ❌ 不自建 Zotero 同步服务 | 官方已有，重复造轮子 |
| ❌ 不自己写 Zotero 搜索/读全文/标注读写 | 开源已有成熟实现 |
| ❌ 不把采集搬到自建服务器 | Actions 免费免维护，没有迁移收益 |
| ❌ 不做社媒全平台的"全覆盖" | 法律风险 + 质量参差，**按时段抽查**比全覆盖更实用 |
| ❌ 不做用户系统 / 多租户 | 这是个人用的工具，不需要 |
| ❌ 不在第 1-3 期碰公网暴露 | 安全加固应当与"确有需求"同时发生 |

> ⭐ **原则**：**宁可少做一件，也不要做一个"什么都有但都不好用"的东西。**

---

## 附录 A：术语表

| 术语 | 解释 |
|------|------|
| **MCP**（Model Context Protocol） | 一个开放协议，让 AI 助手（Claude/Cursor/Hermes 等）能调用外部工具和数据。类比"给 AI 用的 USB 接口" |
| **MCP Server** | 实现了 MCP 协议的程序，对外提供一组"工具"（tool）供 AI 调用 |
| **MCP Client** | 调用方，如 Claude Code、Cursor、Hermes |
| **stdio 传输** | MCP 的一种跑法：Client 把 Server 当**本机子进程**启动，通过标准输入输出通信 —— **不出网、零风险** |
| **Streamable HTTP 传输** | MCP 的另一种跑法：Server 监听端口，Client 通过 HTTP 连 —— **可远程，需认证** |
| **Zotero Web API** | Zotero 官方云 API（`api.zotero.org`），用 API Key 认证，**任何地方**可读写 |
| **Zotero Local API** | Zotero 桌面端在本机开的 API（`localhost:23119/api/`）—— **离线可用、不限速**，但需在设置里手动开启 |
| **collection** | Zotero 里的「分类文件夹」，可嵌套 |
| **annotation** | Zotero PDF 阅读器里的标注（高亮、区域框、文字评论） |
| **BibTeX** | 学术引用格式，Zotero 导入论文的标准格式之一 |
| **聚合式源**（aggregate） | 一次请求返回一批的源（如 OpenReview 按会议查），区别于「按关键词逐个查」的平台式源 |
| **挂载式设计** | 本项目相关的个人方法论：把方法挂在固定流程/环境上，不依赖临场记忆（见 `~/Growth/insights/`） |

---

## 附录 B：参考来源（全部实测核实，2026-09-29）

### 核实方式
> 本文档中所有 star 数、许可证、能力描述**均通过 GitHub API 实测取得**；
> 「读标注」等关键能力**通过阅读源码确认实现**（非依据 README 宣传）；
> Zotero API 能力通过**官方文档页面实测确认**。

### 信息源

| 项目 | 实测结论 |
|------|---------|
| RSSHub — github.com/DIYgod/RSSHub ⭐46.3k AGPL-3.0 | ✅ 1000 个平台目录；⚠️ **实测其知乎/小红书/微博路由已不存在** |
| ForgeRSS — github.com/tmwgsicp/ForgeRSS ⭐129 AGPL-3.0 | ✅ 补抖音/快手/小红书/B站/知乎/小宇宙/知识星球 |
| zhu327/rss — github.com/zhu327/rss ⭐542 | ✅ 微博/微信公众号/知乎日报（无 License 声明） |
| RSSWorker — github.com/yllhwa/RSSWorker ⭐800 MIT | 备选：Cloudflare Worker 上跑，零成本 |
| hotpush — github.com/JackyST0/hotpush ⭐192 MIT | 备选：13+ 平台热榜（热榜 ≠ 内容） |
| MediaCrawler — github.com/NanmiCoder/MediaCrawler ⭐66.0k | ⚠️ **许可为「非商业学习许可证 1.1」**，原文禁止大规模爬取/商用 |

### Zotero 联动

| 项目 | 实测结论 |
|------|---------|
| 54yyyu/zotero-mcp ⭐5.2k MIT | ✅ MCP server + CLI；含 `pdfannots_downloader.py` / `pdfannots_helper.py`（**实测调用 pdfannots2json v1.0.15 提取标注**） |
| cookjohn/zotero-mcp ⭐1.2k MIT | ✅ **Zotero 插件形态**，内置 MCP server（Streamable HTTP，默认端口 23120） |
| papersgpt-for-zotero ⭐2.7k AGPL-3.0 | ✅ Zotero 插件 + 本地 RAG（宣称 10k PDF/7 分钟索引、19.5ms 检索、全离线） |
| arxiv-mcp-server ⭐3.2k Apache-2.0 | ✅ arXiv LaTeX 分段读 + BibTeX |
| **Zotero 官方 API** | ✅ **Local API 官方文档化**：`http://localhost:23119/api/`，离线可用/无速率限制；⚠️ **需在 Zotero 设置里显式开启，否则 403**；✅ 支持写入（Zotero 10+）<br>✅ 免费额度 **300 MB**（官方定价页实测：300MB Free / 2GB $20/年 / 6GB $60/年） |

### MCP 生态

| 项目 | 实测结论 |
|------|---------|
| python-sdk ⭐24.4k MIT | 官方 Python SDK |
| fastmcp ⭐27.9k Apache-2.0 | ⭐ 写 MCP server 最省事的框架（本方案选用） |
| servers ⭐90.7k | 官方 server 合集（参考实现） |

---

## 附录 C：变更记录

| 日期 | 变更 |
|------|------|
| **2026-09-29** | 首次成文。源于 Boss 提出的 7 项需求（数据源扩展/密钥/社媒/媒体页/MCP/Zotero 联动/部署拓扑）。产出：需求拆解、现状差距、三层方案选型（**全部实测核实**）、分层架构 + 4 大关键决策、10 个 MCP 工具设计、5 期落地计划、7 项风险、6 项"不做"。**状态：方案稿，待定方向后开发。** |
