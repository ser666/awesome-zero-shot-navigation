# 完整使用说明（从零到上手）

> **一句话**：这是一个**自动跟踪「零样本具身导航（Zero-Shot Navigation）」论文**的系统 ——
> 云端每周自动采集、过滤、分级、去重，生成一个可检索的网站，
> 并能把新论文**自动推送到你本机的 Zotero**（按分类放好）。
>
> **你大概率只需要第一步：打开网页。** 其余能力想看再看。

| | |
|---|---|
| 🌐 **网站（最常用）** | <https://ser666.github.io/awesome-zero-shot-navigation/> |
| 📦 仓库 | <https://github.com/ser666/awesome-zero-shot-navigation> |
| 🕙 自动更新 | 每周一 10:17（北京时间），跑在 GitHub Actions 云端 |
| 💰 成本 | **0 元**（公开仓库的 Actions 免费 + Pages 免费） |

---

## 目录

- [0. 它替你做什么（三件事）](#0-它替你做什么三件事)
- [1. 三层结构（一张图看懂）](#1-三层结构一张图看懂)
- [2. 数据全貌（真实统计）](#2-数据全貌真实统计)
- [3. 用法 A：网页检索（零门槛）](#3-用法-a网页检索零门槛)
- [4. 用法 B：Zotero 自动入库 ⭐](#4-用法-bzotero-自动入库-)
- [5. 用法 C：接入 AI Agent（MCP）](#5-用法-c接入-ai-agentmcp)
- [6. 用法 D：命令行](#6-用法-d命令行)
- [7. 自动化与运维（不需要你本机）](#7-自动化与运维不需要你本机)
- [8. 配置：改行为、加数据源、密钥](#8-配置改行为加数据源密钥)
- [9. 常见问题](#9-常见问题)
- [10. 功能清单与文档地图](#10-功能清单与文档地图)

---

## 0. 它替你做什么（三件事）

```
① 不用自己找论文
   4 个学术源自动采集 → 相关性过滤（只留本领域）→ 跨源去重
   → 会议分级（CCF-A / 机器人主会）→ 每周一次，全自动

② 不用自己整理
   自动归入 10 个任务分类 + 37 个子专题标签（Zero-Shot / Real Robot / Memory…）
   自动补：引用数、开放获取 PDF 链接、代码仓库链接
   ⚠️ 只认「发表/上线日期」—— 未决定状态（投稿中/被拒/撤稿）绝不算已发表

③ 不用手工搬进文献库
   新论文按分类**自动推送到本机 Zotero**，形成目录树，幂等不重复
   ⭐ 走 Zotero 自带本地接口，**不用云端、不用账号、不用装插件**
```

---

## 1. 三层结构（一张图看懂）

```
┌─────────────────────────────────────────────────────────────────────┐
│  ① 采集层   scripts/            ← 跑在 GitHub Actions 云端，零依赖  │
│     OpenAlex · Crossref · OpenReview · HuggingFace                  │
│     过滤 → 去重 → 分级 → 补链接 → 写库                              │
│                          ↓                                          │
│              data/papers.db + docs/data/papers.json + README.md     │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│  ② 服务层   zenav/              ← 只读数据，不自己存                 │
│     同一套业务逻辑，三个入口共用：                                   │
│        · MCP 服务   → 给 AI Agent 调用                              │
│        · 命令行 CLI → 给人 / 脚本 / 定时任务                        │
│        · Python API → 给自己的程序                                  │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│  ③ 终端层   你要用它的地方                                          │
│     网站（GitHub Pages） ｜ Zotero（本机文献库） ｜ AI Agent        │
└─────────────────────────────────────────────────────────────────────┘
```

> 💡 **关键设计**：采集层与服务层**完全解耦** —— 服务层读的是网站上的
> `papers.json`，所以它可以在任何地方跑（不依赖服务器、不依赖数据库）。

---

## 2. 数据全貌（真实统计）

> 以下为 **2026-09-30** 实际数据（`generated_at` 同）。

| 指标 | 数值 |
|------|------|
| 论文总数 | **770** |
| 时间跨度 | 2019-01 → 2027-01 |
| 任务分类 | **10** 个 |
| 子专题标签 | **37** 个 |
| 重要会议（A 级） | **94** 篇 |
| 有代码链接 | 95 篇 |
| 有开放获取 PDF | 514 篇 |
| 有 DOI | 710 篇 |
| 各源贡献 | OpenAlex 620 ｜ Crossref 160 ｜ OpenReview 51 ｜ HuggingFace 1 |

**10 个任务分类**（按论文数）：

| 论文数 | 分类 |
|-------:|------|
| 171 | Object-Goal Navigation (ObjectNav) |
| 158 | Vision-and-Language Navigation (VLN) |
| 103 | Other |
| 92 | Aerial VLN |
| 85 | LLM / VLM Navigation Agents |
| 59 | Semantic & Open-Vocabulary Navigation |
| 37 | Social Navigation |
| 25 | Image & Point-Goal Navigation |
| 22 | Exploration |
| 18 | Multi-Object Navigation |

**最热子专题**（Top 8）：Benchmark 376 ｜ Zero-Shot 363 ｜ Real Robot 301 ｜
Simulation 299 ｜ Efficiency 294 ｜ Reasoning 286 ｜ VLM / MLLM 244 ｜ LLM-based 190

> ⚠️ **和 `yinzhongqi.com/papers` 不是一回事**：
> 那边是**每日**跑在阿里云服务器上的论文调研系统（另一个项目）；
> 本项目是**每周**跑在 GitHub 上的 awesome list + 网站。两者互不依赖。

---

## 3. 用法 A：网页检索（零门槛）

**打开链接就行，不用装任何东西**：

```
https://ser666.github.io/awesome-zero-shot-navigation/
```

**页面能力**：

```
左栏（固定侧边栏）
  · 关键词搜索框
  · 10 个任务分类（点击筛选）
  · 37 个子专题标签（点击筛选，可叠加）
  · 会议筛选（含分级）
  · 年份范围
  · 只看「有代码」/「开放获取」

右侧结果流
  · 卡片按**发表时间**倒序（最新在最前）
  · 每张卡：年份 · 会议/期刊（分级加粗）· 标题 · 作者 · 分类 · 子专题
  · 直接给：PDF 链接 · 代码仓库 · DOI · 引用数
  · 可展开**完整英文摘要**
```

> 💡 想找「最近有代码的 ObjectNav 论文」：
> 左栏点 `Object-Goal Navigation (ObjectNav)` + 勾 `有代码` → 结果按时间倒序。

---

## 4. 用法 B：Zotero 自动入库 ⭐

### 4.1 先回答那个关键问题：**需要 Zotero 插件吗？**

> ## ❌ 不需要插件，这个项目也**不提供**插件。
>
> **因为 Zotero 自己就带了这个接口。**

Zotero 桌面端（**10 或更新版本**）自带一个**本地 API**，
监听 `127.0.0.1:23119`，**官方支持写入**。我们直接调用它，
所以不需要任何中间层：

```
本项目  ──HTTP──▶  127.0.0.1:23119（你本机的 Zotero）
                   ↑ Zotero 官方自带，不是我们造的
```

### 4.2 为什么不做插件？（四条理由）

| 理由 | 说明 |
|------|------|
| **接口官方已有** | Zotero 10+ 的本地 API 就能建目录、建条目，插件是**多余的一层** |
| **维护成本高** | 插件要用 JavaScript/XUL 开发 + 构建 + 签名，还得随 Zotero 每次升级适配 |
| **安装门槛高** | 用户要手动装 `.xpi`、重启 Zotero、以后还要手动更新 |
| **权限风险大** | 插件跑在 Zotero 进程内部、权限更大；走 API 则边界清晰、可随时停用 |

> 🎯 **结论**：能做插件，但**没必要** —— 官方接口已经覆盖我们的需求
> （推送条目 + 建分类目录），而且更稳、更安全、更省事。

### 4.3 三条路对比（万一你想知道全貌）

| | ⭐ **本地 API**（本项目默认） | 云端 Web API | 自建 Zotero 插件 |
|---|---|---|---|
| 需要 zotero.org 账号 | ❌ | ✅ | ❌ |
| 需要 API Key | ❌ | ✅ | ❌ |
| 装插件 | ❌ | ❌ | ✅（要自己装） |
| 完全离线 | ⭐ ✅ | ❌ | ✅ |
| 数据经过 Zotero 服务器 | ⭐ **不经过** | 会经过 | 不经过 |
| 限速 / 速度 | 无 / 8–60 ms | 有 / 500–1500 ms | 无 / 极快 |
| 需要开发与维护 | ❌ 无 | ❌ 无 | ✅ 要写 + 要跟版本 |
| 版本要求 | **Zotero 10+** | 无 | 随 Zotero 内核 |

### 4.4 怎么用（一次性 3 分钟 + 之后一条命令）

```
① 安装 Zotero 10 或更新版本
     https://www.zotero.org/download        （免费，不用注册账号）

② 打开 Zotero → 设置（Edit → Settings）→ 高级（Advanced）→
   勾选 "Allow other applications on this computer to communicate with Zotero"
   ⚠️ 不勾选，所有请求返回 403

③ Zotero 保持运行，然后在项目目录执行：

     make zotero-status      # 看连接：应显示「本地 API / 不用云端 / 连接正常」
     make zotero-push        # 演练：只报告将要做什么，**不写入任何东西**
     make zotero-push-yes    # 真正写入
```

**首次真实写入时，Zotero 会弹一个确认框**：

| 选项 | 结果 |
|------|------|
| **Always Allow** | ⭐ **选这个** —— 签发持久 key，以后不再弹 |
| Allow | 只签发**一次性** key ⇒ **每次写入都再弹一次** |
| Deny | 拒绝（403） |

> 💡 选 "Allow" 后"每次都弹"是官方设计的**预期行为**（不是故障）：
> 一次性 key 用完即失效，下次写入会返回 401，程序自动重新授权 → 又弹一次。

**入库后的样子**：

```
📁 Zero-Shot Navigation                    ← 顶层目录（自动建）
   ├── 📁 Object-Goal Navigation (ObjectNav)
   ├── 📁 Vision-and-Language Navigation (VLN)
   ├── 📁 Aerial VLN
   ├── 📁 LLM / VLM Navigation Agents
   └── 📁 ...（按论文分类自动建子目录）
```

**条目类型按论文形态自动判断**：

```
会议论文 → conferencePaper
期刊论文 → journalArticle
预印本   → preprint
⚠️ 没有「已发表证据」的一律归 preprint（不在你库里过度声称）
```

**安全与幂等**：

```
· 已推过的不重复推（DOI → arXiv → 标题指纹 三重去重）
· 进度记在 data/zotero_state.json；单篇失败不影响其它篇，
  且失败的**不会被记为已推**（下次自动重试）
· 单次运行默认最多推 50 条（防误操作灌爆）
· 授权 key 存在 data/zotero_local_keys.json：
  · 权限 0600  · 已在 .gitignore 中  · 按 Zotero 实例 ID 分区
  ⚠️ 它等同于「改写你文献库的权力」—— 不要外传、不要提交
```

### 4.5 想定期自动推（可选）

```bash
# crontab：每周一 11:00（在云端采集完成之后）
0 11 * * 1 cd /path/to/repo && python3 -m zenav.interfaces.cli zotero push --yes >> /tmp/zotero_push.log 2>&1
```

> ⚠️ 本地 API 只听 `127.0.0.1` ⇒ **推送必须跑在装 Zotero 的那台机器上**。
> 所以最终形态是「**读远程数据 + 写本地库**」—— **连服务器都不需要**。

---

## 5. 用法 C：接入 AI Agent（MCP）

本项目内置 **MCP 服务**，任何支持 MCP 的 Agent（Claude Code / Hermes / Cursor…）
都能直接检索论文、取详情、导 BibTeX。

### 5.1 客户端配置

```json
{
  "mcpServers": {
    "zero-shot-nav": {
      "command": "/绝对路径/.venv/bin/python",
      "args": ["-m", "zenav.interfaces.mcp_server"],
      "cwd": "/绝对路径/awesome-zero-shot-navigation"
    }
  }
}
```

### 5.2 暴露的 10 个工具

| 工具 | 做什么 |
|------|--------|
| `get_dataset_info` | 库概况：总数、分类、子专题、会议分布、时间跨度 |
| `list_categories` | 列出 10 个任务分类 + 各自论文数 |
| `list_topics` | 列出 37 个子专题标签 + 数量 |
| `search_papers` | ⭐ 多条件检索（关键词 / 分类 / 子专题 / 会议 / 年份 / 有代码 / 开放获取 / 引用数） |
| `get_paper` | 按 DOI / arXiv / 标题取**单篇完整信息**（含摘要全文） |
| `latest_papers` | 最近 N 天发表（默认 30 天） |
| `category_overview` | ⭐ 某分类的 **Markdown 总览表**（可直接贴进报告） |
| `export_bibtex` | 导出 BibTeX 条目 |
| `zotero_status` | Zotero 连接与推送进度 |
| `push_to_zotero` | ⭐ 推送新论文到 Zotero（默认演练模式） |

> ✅ 已实测：stdio 与 HTTP 两种传输都能与官方 client 完成握手并调用全部 10 个工具。
> 出错时返回**结构化错误 + 修复建议**（Agent 能读懂"该怎么改"，而不是收到崩溃）。

### 5.3 用法示例（问 Agent 就行）

```
"帮我找 2026 年有代码的 zero-shot ObjectNav 论文，给 5 篇"
"列出 Aerial VLN 这个分类的总览表"
"这几篇导出成 BibTeX"
"把新论文推到我的 Zotero"
```

### 5.4 暴露成 HTTP（可选，服务器部署用）

```bash
python3 -m zenav.interfaces.mcp_server --transport http
# 或：zenav mcp --transport http
```

**监听地址与端口来自配置**（命令行没有 `--port` 参数）：

```toml
# config/settings.toml
[mcp]
transport = "stdio"
http_host = "127.0.0.1"        # ⚠️ 默认只监听回环
http_port = 8765
auth_token_env = "ZENAV_MCP_TOKEN"   # http 传输的 Bearer token
```

> ⚠️ **对外暴露前必须做两件事**：把 `http_host` 改成 `0.0.0.0`（或反代地址）、
> 并设好 `ZENAV_MCP_TOKEN` —— 否则等于把论文库接口裸奔在公网。
> 只给自己用的话，保持 stdio 即可，**零网络暴露**。

---

## 6. 用法 D：命令行

适合调试、脚本、cron。**与 MCP 共用同一套逻辑**，行为必然一致。

```bash
# 环境：项目根目录
python3 -m zenav.interfaces.cli <子命令>       # 或 make <目标>
```

### 6.1 全部 11 个子命令

| 子命令 | 作用 | 例子 |
|--------|------|------|
| `info` | 论文库概况 | `zenav info` |
| `categories` | 列出分类 | `zenav categories` |
| `topics` | 列出子专题 | `zenav topics --limit 20` |
| `search` | ⭐ 检索 | `zenav search "zero-shot object navigation" --limit 5` |
| `show` | 单篇详情 | `zenav show 10.1016/j.neunet.2026.109323` |
| `overview` | ⭐ 分类总览表 | `zenav overview "Aerial VLN" --out vln.md` |
| `latest` | 最近发表 | `zenav latest --days 30 --limit 10` |
| `bibtex` | 导出 BibTeX | `zenav bibtex "VLN" --out vln.bib` |
| `config` | 看配置与密钥状态 | `zenav config`（密钥只显示掩码） |
| `zotero` | Zotero 推送 | `zenav zotero status` / `push` / `push --yes` |
| `mcp` | 启动 MCP 服务 | `zenav mcp`（stdio） |

### 6.2 常用检索参数

```bash
zenav search "keyword" \
  --category "Aerial VLN"      # 任务分类
  --topic "Real Robot"         # 子专题
  --venue "CoRL"               # 会议
  --year-from 2025 --year-to 2026
  --has-code                   # 只看有代码
  --open-access                # 只看开放获取
  --min-citations 20
  --sort date                  # date | citations | hotness | year | title
  --limit 20 --offset 0
  --abstract                   # 结果里带摘要片段
```

**分类总览表（`overview`）** —— 生成可直接贴进报告的 Markdown：

```bash
zenav overview "Aerial VLN"                    # 打印
zenav overview "Aerial VLN" --out vln.md       # 写入文件
zenav overview "VLN" --group-by venue --limit-per-group 3   # 按会议分组
```

```
--group-by   year（默认）| venue | topic
--limit-per-group  每组最多几篇
```

实测输出（`Aerial VLN`）：

```
# Aerial VLN
**共 92 篇** ｜ A 级会议 5 篇 ｜ 有开源代码 12 篇 ｜ 最新 2026-09-28
## 2026（64 篇）
| 年份 | 会议/期刊 | 标题 | 作者 | 链接 |
```

**导出 BibTeX**：

```bash
zenav bibtex --category "Aerial VLN" --out vln.bib
zenav bibtex --topic "Real Robot" --limit 50 --out robot.bib
```

**查单篇（`show`）** —— 接受的标识格式（实测）：

```bash
zenav show 10.1016/j.neunet.2026.109323     # ✅ DOI 原样
zenav show https://doi.org/10.1016/j...     # ✅ DOI 完整 URL（粘贴即可）
zenav show 2603.28691                       # ✅ arXiv ID（⚠️ 不要加 arXiv: 前缀）
zenav show "Interactive 3D scene graph"     # ✅ 标题片段（模糊匹配）
```

> ⚠️ `arXiv:2603.28691` 这种**带前缀**的写法**不识别**；
> 若某篇是 arXiv 论文，用它的 arXiv / DOI 链接里那串数字即可。

> `--json` 放在**子命令前**：`zenav --json search "vln"` → 输出结构化 JSON，便于脚本消费。

### 6.3 常用 make 目标

```bash
make                 # 看全部目标
make search Q="zero-shot" ARGS="--has-code"
make latest D=30 N=10
make overview C="Aerial VLN"        # 生成总览表
make mcp                            # 起 MCP
make zotero-status / zotero-push / zotero-push-yes
make test                           # 跑全部自测（183 个用例）
make serve                          # 本地预览网站
```

---

## 7. 自动化与运维（不需要你本机）

```
采集               GitHub Actions 云端，每周一 10:17 北京时间
                   增量：回看 21 天 + 跨源去重 + 失败快退
                   零第三方依赖（不 pip install，更快更稳）

部署               同一次工作流内完成（GitHub 默认 token 推送不触发其他工作流，
                   所以部署必须同流程依赖，这是刻意设计）

防停看护           本机每月 1 日 11:00 跑 keepalive.py
                   ⚠️ 为什么需要：GitHub 定时工作流 **60 天无活动会自动禁用**
```

**工作流跑了什么（10 步）**：

```
① Checkout  ② Setup Python  ③ Self-test（规则+契约+服务层）
④ Harvest   ⑤ Re-filter     ⑥ Backfill（分级/专题/链接）
⑦ Enrich（引用数·开放获取·代码仓库）  ⑧ Export（README + 站点数据）
⑨ Sanity check  ⑩ Commit changes（⚠️ push 前先 pull --rebase）
```

> ⚠️ 第 ⑩ 步的细节值得记住：采集要跑约 20 分钟，期间若有人推了 main，
> 直接 `git push` 会因非快进被拒 ⇒ **整轮采集白跑**。所以先 rebase 再推，
> 失败会重试并明确报错（已加回归测试守住这条）。

---

## 8. 配置：改行为、加数据源、密钥

### 8.1 三个文件的职责

| 文件 | 放什么 | 能否公开提交 |
|------|--------|--------------|
| `config/sources.toml` | **有哪些数据源、怎么跑**（唯一事实来源） | ✅ 可以 |
| `config/settings.toml` | 数据来源、Zotero、MCP 的行为 | ✅ 可以 |
| `config/secrets.env` | **密钥的真实值** | ❌ **禁止**（已 gitignore） |

> ⭐ **密钥铁律**：配置文件里**只出现"环境变量名"**，真实值从环境变量读。
> 所以配置文件可以放心公开，密钥永不进仓库。

### 8.2 改行为（不用改代码）

```toml
# config/sources.toml
[sources.openalex]
enabled = true
interval = 1.5          # 该源限速（比改代码强）
per_page = 200          # 源特有参数，会按函数签名自动传进去

[sources.semanticscholar]
enabled = false         # 关掉一个源：改这一行就行
```

```toml
# config/settings.toml
[catalog]
source = "docs/data/papers.json"        # 本地文件，或换成远程 URL

[zotero]
enabled = true
backend = "local"                       # local（不用云端）| web | auto
collection_root = "Zero-Shot Navigation"

[mcp]
transport = "stdio"                     # stdio | http
```

> ✅ 已验证：`enabled = false` 真的会排除该源；改 `rows`/`max_age_days`
> 真的会传进采集函数；**配置缺失不会禁用任何源**（默认启用，绝不静默关停）。

### 8.3 加一个新数据源

```
第 1 步  在 scripts/sources.py 写 harvest_xxx(date_from) → 返回归一化 list
         （签名由框架按需探测：接受 date_from / limit / options 都行）
第 2 步  在 SOURCES 注册表登记
第 3 步  config/sources.toml 加一节（可选，但推荐：限速与源特有参数）
第 4 步  python3 scripts/sources.py --selftest     # 契约自测
第 5 步  make test                                  # 全量回归
```

> 详细步骤与** 6 个坑**见 [`OPERATIONS.md`](OPERATIONS.md) 的《加一个新数据源》。

### 8.4 密钥（只在需要时）

```bash
cp config/secrets.env.example config/secrets.env
chmod 600 config/secrets.env
```

| 变量 | 什么时候需要 |
|------|--------------|
| `ZOTERO_API_KEY` / `ZOTERO_LIBRARY_ID` | ⚠️ **只有**用云端 Zotero（`backend = "web"`）才需要 |
| `SEMANTIC_SCHOLAR_API_KEY` | 只有启用 semanticscholar 源才需要 |
| `ZENAV_MCP_TOKEN` | 只有把 MCP 暴露到公网才需要 |

> ⭐ **默认配置下，这三个一个都不用配。**

---

## 9. 常见问题

**Q：需要装 Zotero 插件吗？**
A：**不需要，本项目也不提供插件** —— Zotero 10+ 官方自带本地 API（`127.0.0.1:23119`）
可直接写入。我们直接调它，少一层中间件。详见 [§4.1](#41-先回答那个关键问题需要-zotero-插件吗)。

**Q：我不想用 Zotero 云端，行吗？**
A：✅ 行，而且这是**默认**方式（`backend = "local"`）。
不用 zotero.org 账号、不用 API Key、不用装插件、完全离线、**数据不经过任何服务器**。

**Q：每次写入都弹授权框？**
A：首次弹窗时你选了 **Allow**（一次性 key）。改选 **Always Allow** 即可一劳永逸。

**Q：Zotero 连不上（连接失败）？**
A：① Zotero 桌面端是否**正在运行**；② 版本是否 **10+**（7–9 的本地 API 只读）；
③ 设置 → 高级 的开关是否勾选；④ 是否在**同一台机器**上（本地 API 只听 127.0.0.1）。
WSL 用户：需 WSL2 **mirrored** 网络模式才能直连 Windows 的 localhost。

**Q：我是 WSL，能连 Windows 上的 Zotero 吗？**
A：能 —— WSL2 用 **mirrored** 网络模式时，WSL 里的 `127.0.0.1:23119` 直连 Windows 的
Zotero。默认 NAT 模式则需要把 `local_api_base` 改成 Windows 宿主 IP。

**Q：为什么要每周跑、不是每天？**
A：本列表定位是**领域全景跟踪**，周长更新足够；而且 Actions 跑得越密越容易撞上
GitHub 的定时限制。**每日晨报**是另一个系统（跑在服务器上，见 §2 末尾说明）。

**Q：投稿中的论文会被算成"已发表"吗？**
A：不会。venue 含 `Submitted to` / `Withdrawn` / `Rejected` 的**不归属会议、不给分级**。
（这是修过的一个真 bug：曾把 10 篇 `Submitted to ICLR 2026` 算成 ICLR/A 级。）

**Q：找不到某篇该有的论文？**
A：可能被相关性过滤挡掉了（领域外的论文会被滤除）。
提 issue 说清标题即可 —— 过滤器**误杀成本远高于误收成本**，所以宁可放宽。

**Q：这一堆测试是干嘛的？**
A：183 个离线用例 + 6 项仓库检查，专门守那些**不报错**的错：
过度声称、静默失效、分层被破坏、密钥误提交、README 链接失效……
每项检查都做过**反向验证**（故意造错 → 确认会失败），否则不算数。

---

## 10. 功能清单与文档地图

### 10.1 全功能清单

| # | 功能 | 状态 | 入口 |
|---|------|------|------|
| 1 | 自动采集（4 源，周更，增量+去重） | ✅ | 云端自动 |
| 2 | 相关性过滤（只留本领域） | ✅ | 云端自动 |
| 3 | 会议分级（CCF-A / 机器人主会） | ✅ | 云端自动 |
| 4 | 分类（10）/ 子专题（37）自动打标 | ✅ | 云端自动 |
| 5 | 补引用数 / 开放获取 / 代码仓库 | ✅ | 云端自动 |
| 6 | 网页检索与筛选 | ✅ | 网站 |
| 7 | 分类总览表（Markdown） | ✅ | CLI / MCP |
| 8 | BibTeX 导出 | ✅ | CLI / MCP |
| 9 | **MCP 接口（10 工具）** | ✅ | Agent |
| 10 | **Zotero 自动入库（本地 API）** | ✅ | CLI / MCP |
| 11 | 配置化（改 toml 即改行为） | ✅ | `config/*.toml` |
| 12 | 密钥管理（不进代码/配置） | ✅ | `config/secrets.env` |
| 13 | 月度防停看护 | ✅ | 本机 cron |
| 14 | 社媒源（小红书/知乎/公众号） | ⏸ 暂缓 | — |
| 15 | Zotero 标注读取 / 双向同步 | ❌ 明确不做 | — |

### 10.2 文档地图（看哪个文档）

| 文档 | 讲什么 | 什么时候看 |
|------|--------|-----------|
| **`USAGE.md`（本文）** | **怎么用：全功能 + 用法 + 配置 + FAQ** | ⭐ 想用起来 |
| [`SERVICE.md`](SERVICE.md) | 服务层使用手册（MCP / CLI / Zotero 细节） | 接 Agent、调 Zotero |
| [`OPERATIONS.md`](OPERATIONS.md) | 运维：跑在哪、多久一次、坑与修复 | 出问题、想改流程 |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 架构设计与演进路线（为什么这么做） | 想扩展系统 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 怎么贡献（加论文/改分级/加源） | 想参与 |

> 📌 **单一事实来源**：行为以 `config/*.toml` 与代码为准，文档若与代码不符**以代码为准**，
> 并欢迎提 issue 指出文档过期处。

---

## 附录：上手最短路径

```
只想看论文      → 打开 https://ser666.github.io/awesome-zero-shot-navigation/

想接自己的 Zotero
  → ① 装 Zotero 10+   ② 设置→高级→勾选允许本机应用通信
  → ③ make zotero-status  ④ make zotero-push（演练）  ⑤ make zotero-push-yes
  ⚠️ 首次弹框选 **Always Allow**

想让 Agent 帮你查
  → 把 §5.1 那段 JSON 贴进 MCP 客户端配置 → 重启 → 直接问

想本地跑命令行
  → cd 项目目录；python3 -m zenav.interfaces.cli info

想改行为
  → 编辑 config/sources.toml 或 config/settings.toml（不用改代码）

出问题
  → make test（183 用例 + 6 项检查，先确认环境没坏）
  → 再查 OPERATIONS.md 的《已踩过的坑》
```
