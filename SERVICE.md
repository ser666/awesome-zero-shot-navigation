# 服务层使用手册（zenav）

> 本文档讲**怎么用**。设计动机与取舍见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。
> 运维口径见 [`OPERATIONS.md`](OPERATIONS.md)。

---

## 这是什么

本项目分两层，职责严格分开：

```
采集层  scripts/    零第三方依赖，跑在 GitHub Actions
       产出 → data/papers.db + docs/data/papers.json
                     ↓ 只通过"数据文件"耦合
服务层  zenav/      给 Agent / 命令行 / Zotero 用
```

服务层提供三样东西：

| # | 能力 | 用在哪 |
|---|------|--------|
| **1** | **配置化 + 密钥管理** | 数据源、Zotero、MCP 全部从 `config/*.toml` 读，密钥只走环境变量 |
| **2** | **MCP 接口** | 让 Claude Code / Hermes 等 Agent 直接检索、取详情、导 BibTeX |
| **3** | **Zotero 单向推送** | 把新论文自动推送到你的 Zotero 库，按分类建目录。<br>⭐ **默认走 Zotero 本地 API —— 不用云端、不用账号、不用 API Key、不用插件** |

---

## 快速开始（30 秒）

```bash
# 1) 看论文库概况
make info

# 2) 检索
make search Q="zero-shot object navigation"
make search Q="VLN" ARGS="--has-code --limit 5"

# 3) 某个分类的总览表
make overview C="Aerial VLN"

# 4) 看配置与密钥状态（只显示掩码，不显示值）
make config
```

等价的直接调用（不依赖 make）：

```bash
python3 -m zenav.interfaces.cli info
python3 -m zenav.interfaces.cli search "zero-shot" --limit 5
python3 -m zenav.interfaces.cli overview "VLN" --out vln.md
python3 -m zenav.interfaces.cli latest --days 30
python3 -m zenav.interfaces.cli bibtex "vln" --limit 50 --out refs.bib
python3 -m zenav.interfaces.cli show 10.1109/lra.2026.3723310   # DOI / arXiv / 标题都行
```

> 💡 所有命令都支持 `--json`（给脚本用）和 `--log-level DEBUG`（排查用）。

---

## 一、接入 Agent（MCP）

### 1.1 前提

```bash
uv venv .venv && uv pip install -r requirements.txt
# 或最小安装：uv pip install "mcp>=1.9"
```

> ⚠️ **只有 MCP 需要这个依赖**。CLI、Zotero 推送、Python import 全部零依赖可用。

### 1.2 客户端配置

把下面这段加到 MCP 客户端的配置里（**路径改成你的实际路径**）：

```json
{
  "mcpServers": {
    "zero-shot-nav": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "zenav.interfaces.mcp_server"],
      "cwd": "/absolute/path/to/awesome-zero-shot-navigation"
    }
  }
}
```

- **Claude Code**：写进项目的 `.mcp.json`
- **Hermes**：见其文档中的 MCP server 配置项
- 其他客户端：任何支持 stdio 传输的都能用

### 1.3 自检

不想配客户端也能先验证：

```bash
make mcp        # 前台启动，Ctrl-C 退出；能起来就说明没问题
```

### 1.4 暴露了哪些工具（10 个）

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `get_dataset_info` | 库的概况（探索入口） | — |
| `list_categories` | 全部分类 + 论文数 | — |
| `list_topics` | 子专题标签 + 论文数 | `limit` |
| `search_papers` | 多条件检索 | `query` `category` `topic` `venue` `year_from/to` `has_code` `open_access` `min_citations` `sort` `limit` `offset` |
| `get_paper` | 单篇完整信息（含摘要） | `identifier`（DOI / arXiv / 标题） |
| `latest_papers` | 最近发表的论文 | `days` `limit` `category` |
| ⭐ `category_overview` | 分类总览表（Markdown） | `category` `group_by` `limit_per_group` `format` |
| `export_bibtex` | 导出 BibTeX 文本 | `query` `category` `topic` `limit` `dry_run` |
| `zotero_status` | Zotero 连接与进度 | — |
| ⭐ `push_to_zotero` | 推送到 Zotero（默认演练） | `category` `since_days` `limit` `dry_run` |

**设计约定**：
- 所有工具返回 JSON，失败时返回 `{"ok": false, "error": ..., "hint": ...}`
  —— `hint` 里是**怎么修**，Agent 可以直接转述给你
- `search_papers` 默认**不带摘要**（756 篇全带会撑爆 Agent 上下文）；
  要摘要用 `get_paper` 取单篇
- 返回条数上限 100（`limit` 会被钳制）

### 1.5 HTTP 方式（服务器部署时）

```bash
python3 -m zenav.interfaces.mcp_server --transport http --host 127.0.0.1 --port 8765
```

⚠️ **安全**：
- 默认只监听回环地址。改成 `0.0.0.0` 会被配置校验**拒绝**（除非你显式改配置并知道风险）
- 对外暴露前必须：① 设置 `ZENAV_MCP_TOKEN`，② 前面挂 HTTPS 反代
- 认证目前靠反向代理（nginx basic auth / bearer）。**不建议**直接把端口开到公网

---

## 二、Zotero 单向推送

### 2.1 设计边界（重要）

```
✅ 做：你的服务/数据 → 你的本地 Zotero 库（单向），按分类自动建目录
❌ 不做：从 Zotero 反向读；双向同步；冲突合并
```

### 2.2 ⭐ 两种后端：默认**不用云端**

| | ⭐ **local**（默认） | web |
|---|---|---|
| 走哪 | Zotero **本地** API `127.0.0.1:23119` | `api.zotero.org` |
| zotero.org 账号 | ❌ 不需要 | ✅ 需要 |
| 网站 API Key | ❌ 不需要 | ✅ 需要 |
| 装插件 | ❌ 不需要 | ❌ 不需要 |
| 网络 | ⭐ **完全离线** | 必须联网 |
| 限速 | 无 | 有 |
| 延迟 | ⭐ 8–60 ms | 500–1500 ms |
| 数据经过 Zotero 服务器 | ⭐ **不经过** | 会经过 |

```
backend = "local"   ← 默认（不用云端）
backend = "web"     ← 用官方云同步
backend = "auto"    ← 先探测本地，不可用才回落 web
```

> ⭐ **为什么默认 local 而不是 auto**：auto 在本地不可用时会**静默回落到云端
> 并要求账号**，容易让人困惑（"为什么突然让我去申请 Key？"）。默认 local 时，
> 连不上就直接告诉你怎么把本地 Zotero 配好。

### 2.3 ⭐ 用本地后端（推荐，约 3 分钟）

**前提**：Zotero **10 或更新** 的桌面端。

> ⚠️ **版本是硬门槛**：本地 API 的**写入**能力是 Zotero 10 才有的。
> Zotero 7–9 的本地 API **只读** —— 读能用，写必失败。

```
① 安装/升级 Zotero 到 10+（zotero.org/download，免费）
② 打开 Zotero → 设置 → 高级 →
   勾选 "Allow other applications on this computer to communicate with Zotero"
   （不勾选，所有请求返回 403）
③ Zotero 保持运行，然后：
     make zotero-status      # 应显示「本地 API / 不用云端 / 连接正常」
     make zotero-push        # 演练：只报告，不写入
     make zotero-push-yes    # 首次真实写入 → ⭐ Zotero 弹确认框
```

**首次写入时 Zotero 会弹一个确认框**，三个选项：

| 选项 | 结果 |
|------|------|
| **Always Allow** | ⭐ **选这个** —— 签发持久 key，以后不再弹 |
| Allow | 只签发**一次性** key ⇒ **每次写入都会再弹一次** |
| Deny | 拒绝（403） |

> 💡 选 "Allow" 后"每次都弹窗"是**预期行为**（不是故障）：一次性 key 被消费后，
> 下一次写入会返回 401，客户端会自动重新授权 —— 于是又弹一次。

**授权后的 key 存在哪**：`data/zotero_local_keys.json`
（**已 gitignore**；权限 0600；按 Zotero 实例 ID 分区）。

> ⚠️ 这个 key 等同于"改写你文献库的权力"——不要复制给别人、不要提交。

### 2.4 用云端后端（可选，约 2 分钟）

只有在 `backend = "web"`（或 auto 且本地不可用）时才需要：

1. 打开 <https://www.zotero.org/settings/keys/new>
2. 勾选 **Allow library access** 和 ⭐ **Allow write access**
3. 复制 **API Key**；同页顶部找 `Your userID ... is 1234567`（**数字**，非用户名）
4. 写入 `config/secrets.env` 的 `ZOTERO_API_KEY` / `ZOTERO_LIBRARY_ID`

```bash
cp config/secrets.env.example config/secrets.env
chmod 600 config/secrets.env
```

> ⚠️ `config/secrets.env` 已在 `.gitignore` 中 —— 永远不要 `git add -f`。
> ⚠️ web 后端意味着**数据会经过 Zotero 的服务器**。

### 2.5 使用

```bash
make zotero-status       # 看后端、连得通不通、推了多少、还剩多少
make zotero-push         # ⭐ 演练：只报告将要做什么，**不写入任何东西**
make zotero-push-yes     # 真正写入
```

带筛选：

```bash
python3 -m zenav.interfaces.cli zotero push --category "Aerial VLN" --since-days 30
python3 -m zenav.interfaces.cli zotero push --limit 200
```

**幂等行为**：

```
· 已推过的论文不会重复推（按 DOI → arXiv → 标题指纹 去重）
· 进度记在 data/zotero_state.json（原子写；损坏会自动备份重建）
· 单篇失败不影响其它篇，且**失败的不会被记为已推**（下次自动重试）
· 单次运行默认最多推 max_batch（默认 50）条，防止误操作一次性灌爆
```

**演练（dry run）的严格语义**：

```
演练 = 只读。不写条目、不建目录、不弹授权框、不落状态文件。
       它会**报告**"将会新建哪些目录"，但一个字节都不写。
```

> ⚠️ 这条是从一个真 bug 修出来的：早期实现在演练时也建目录，
> 结果演练会**弹授权框**，还真的在你库里建了目录。

Zotero 端会形成这样的结构：

```
📁 Zero-Shot Navigation
   ├── 📁 Object-Goal Navigation (ObjectNav)
   ├── 📁 Vision-and-Language Navigation (VLN)
   ├── 📁 Aerial VLN
   └── 📁 ...
```

> 条目类型按论文形态自动判断：会议→`conferencePaper`、期刊→`journalArticle`、
> 预印本→`preprint`。
> ⚠️ 没有"已发表证据"的条目（venue_kind 为空/unknown）一律归为 `preprint`
> 而不是会议论文 —— 避免在文献库里造成过度声称。

### 2.6 自动化（可选）

```bash
# crontab：每周一 11:00 推送（在采集工作流之后）
0 11 * * 1 cd /path/to/repo && python3 -m zenav.interfaces.cli zotero push --yes >> /tmp/zotero_push.log 2>&1
```

> 本地后端推送时，**Zotero 必须正在运行**且在同一台机器上。

### 2.7 ⚠️ 本地后端的两个环境注意点

```
① 推送必须跑在**装 Zotero 的那台机器**上
   本地 API 只监听 127.0.0.1，服务器上的服务无法直接写你笔记本的 Zotero。
   ⇒ 所以数据从云端拉（GitHub Pages），写入在本地发生：
        [catalog] source = "https://.../papers.json"   ← 读远程
        [zotero]  backend = "local"                     ← 写本地
     这也是默认配置的形态，**不需要任何服务器**。

② WSL / 虚拟机场景
   WSL2 用 **mirrored** 网络模式时（`~/.wslconfig` 里 networkingMode=mirrored），
   WSL 里的 `127.0.0.1:23119` 可以直连 Windows 上运行的 Zotero ✅
   若用默认 NAT 模式，则需要用 Windows 宿主 IP（改 local_api_base）。
   ⚠️ 另有一个坑：连不通的本地端口在 WSL 下是**超时**而非"拒绝"，
      所以探测失败会等几秒（已把超时设为 8s，避免误以为卡死）。
```

---

## 三、配置说明

### 3.1 文件分工

| 文件 | 管什么 |
|------|--------|
| `config/sources.toml` | **采集层**：有哪些数据源、开关、限速、密钥环境变量名 |
| `config/settings.toml` | **服务层**：数据从哪读、Zotero 行为、MCP 传输 |
| `config/secrets.env` | 密钥**值**（gitignored）｜模板见 `secrets.env.example` |

### 3.2 密钥的三级来源（优先级从高到低）

```
1. 进程环境变量        export ZOTERO_API_KEY=...     ← 生产环境首选
2. config/secrets.env  本机开发便利（gitignored）
3. （无）              → 报错，并给出配置指引
```

**代码里只出现环境变量名**，绝不出现值。例：

```toml
[sources.semanticscholar]
enabled = false
api_key_env = "SEMANTIC_SCHOLAR_API_KEY"   # ← 只有名字，没有值
```

### 3.3 加一个新的数据源

1. 在 `scripts/sources.py` 写 `harvest_xxx()`，注册进 `SOURCES` 或 `EXTRA_SOURCES`
2. 在 `config/sources.toml` 加一节 `[sources.xxx]`
3. `make test` —— 契约自测会校验签名

> 详细约定见 `scripts/sources.py` 顶部注释 + `OPERATIONS.md`。
> **不需要改管线代码**（它会遍历注册表）。

### 3.4 切换数据来源（本地 / 远程）

```toml
[catalog]
# 本地文件（开发）
source = "docs/data/papers.json"
# 远程 URL（服务器部署 —— 永远是最新的，不用 git pull）
# source = "https://ser666.github.io/awesome-zero-shot-navigation/data/papers.json"
# SQLite（要原始字段时）
# source = "data/papers.db"
```

---

## 四、在服务器上部署（可选）

服务层的设计让"部署"很轻 —— **没有数据库、没有常驻写入**：

```bash
git clone https://github.com/ser666/awesome-zero-shot-navigation.git
cd awesome-zero-shot-navigation

# ① 用远程数据源（不用 clone 数据文件，永远最新）
#    编辑 config/settings.toml：
#    [catalog]
#    source = "https://ser666.github.io/awesome-zero-shot-navigation/data/papers.json"

# ② 配密钥（用环境变量，别落地文件）
export ZOTERO_API_KEY=...
export ZOTERO_LIBRARY_ID=...

# ③ 装 MCP 依赖并起 HTTP 服务
uv venv .venv && uv pip install -r requirements.txt
.venv/bin/python -m zenav.interfaces.mcp_server --transport http
```

---

## 五、开发：代码结构与扩展点

### 5.1 分层

```
zenav/
├── errors.py            异常体系（带 hint，便于 Agent 转述）
├── log.py               日志（⚠️ 走 stderr —— stdio 下 stdout 是协议通道）
├── config/              配置加载
│   ├── models.py        dataclass + 校验（frozen）
│   ├── loader.py        TOML → 默认值合并 → ${ENV} 展开 → 构造
│   └── secrets.py       密钥解析 + 掩码
├── domain/              ★ 纯领域模型（无 IO）
│   └── paper.py         Paper + 短键/长键唯一映射
├── infra/               ★ 所有 IO
│   ├── catalog.py       数据访问（本地 JSON / 远程 JSON / SQLite）
│   └── http.py          HTTP 客户端（urllib + 重试 + 泄密防护）
├── services/            ★ 业务逻辑（与传输无关）
│   ├── papers.py        检索 / 详情 / 分类总览
│   ├── bibtex.py        BibTeX 渲染
│   └── zotero.py        单向推送（Client / State / Service 三层）
└── interfaces/          薄适配层
    ├── mcp_server.py    MCP 工具（10 个）
    └── cli.py           命令行
```

### 5.2 三条纪律

1. **业务逻辑只写在 `services/`** —— MCP 工具和 CLI **都不许**有逻辑副本，
   否则两边行为会漂移（修了一边忘另一边）
2. **IO 只在 `infra/`** —— 领域模型和 services 不做网络/文件操作，
   这样它们能被极快地单元测试
3. **采集层不得引入第三方依赖** —— 见 `requirements.txt` 的说明

### 5.3 常见扩展

| 想做什么 | 改哪里 |
|---------|--------|
| 加一个 MCP 工具 | `services/` 加方法 → `interfaces/mcp_server.py` 加一个 `@mcp.tool()` 薄包装 |
| 换数据来源 | `infra/catalog.py` 加一个 `PaperCatalog` 子类 + `create_catalog` 注册 |
| 换 HTTP 库 | 只改 `infra/http.py`（上层只见 `HttpClient` 接口） |
| 支持 Zotero 双向 | 实现 `services/zotero.py` 里的 `ZoteroWriter` 协议并扩展 service |
| 加配置项 | `config/models.py` 加字段 + `loader.py` 解析 + 在 `*.toml` 写默认值 |

### 5.4 测试

```bash
make test            # 全量（采集层规则 + 服务层）
make service-test    # 只有服务层（150 个用例，<1 秒）
```

服务层测试**完全离线**（用 `tests/_support.py` 里的替身），
不碰网络、不需要凭据 —— 所以能在任何机器、任何 CI 上跑。

---

## 六、常见问题

**Q：`make mcp` 起不来 / 客户端连不上？**
A：① 确认装了 mcp SDK（`uv pip install "mcp>=1.9"`）；
   ② 确认客户端配置里的 `cwd` 是仓库根目录（否则找不到 `config/`）；
   ③ 看 stderr 的日志 —— 不要用 `print` 调试，**stdio 下 stdout 被协议占用**。

**Q：Zotero 报 403？**
A：两种情况，看后端：
   ① **本地后端**：Zotero 没开本地 API（设置 → 高级 → 勾选
      "Allow other applications … communicate with Zotero"），
      或者授权对话框里点了 Deny。也可能是 Zotero 版本 < 10（写入需 10+）。
   ② **web 后端**：API Key 没勾 **write access**，或 Key 与 userID 不是同一账号。

**Q：不想用 Zotero 云端，可以吗？**
A：✅ 可以，而且这是**默认**方式（`backend = "local"`）。
   Zotero 10+ 的**本地 API 支持写入**：不用 zotero.org 账号、不用网站 API Key、
   不用插件、完全离线、数据不经过任何服务器。
   只需装 Zotero 10+、开启设置里的开关、保持 Zotero 运行。
   详见 §2.3。

**Q：用本地后端时，**每次写入都弹授权框**？**
A：你在第一次弹窗时选了 **Allow**（只签发一次性 key）。
   一次性 key 用完即失效 ⇒ 下次写入 401 ⇒ 客户端重授权 ⇒ 又弹一次
   —— 这是官方设计的**预期行为**。
   选 **Always Allow** 即可一劳永逸。

**Q：本地后端连不上（连接失败）？**
A：按顺序排查：① Zotero 桌面端是否**正在运行**（本地 API 由客户端提供）；
   ② 版本是否 **10+**；③ 设置 → 高级 的开关是否勾选；
   ④ 是否跑在**同一台机器**上（本地 API 只听 127.0.0.1）。
   WSL 用户注意：需 WSL2 **mirrored** 网络模式才能直连 Windows 的 localhost。

**Q：本地 Zotero 的 key 在哪、安全吗？**
A：`data/zotero_local_keys.json`，权限 0600，已在 `.gitignore` 中。
   它按 Zotero 实例 ID 分区（换了数据目录就要重新授权），
   等同于"改写你文献库的权力"——不要外传、不要提交。

**Q：Zotero 报 412 / 428？**
A：Zotero 重启过或换了数据目录（实例 ID 变了）。客户端会**自动刷新并重试一次**；
   若持续失败，确认 Zotero 数据目录没被改动。

**Q：Zotero 报 412 / 条目已存在？**
A：说明之前推过但状态文件丢了。本项目靠 `data/zotero_state.json` 幂等；
   该文件丢失只会导致"重复推一次"，不会损坏已有条目。删掉重复的手动清理即可。

**Q：搜索结果为空，但我知道库里有这篇？**
A：先 `make categories` 确认分类名（不要凭记忆猜）；
   `query` 里的多个词是 **AND** 关系，词多了会过滤掉；
   标题检索可以试更独特的片段。

**Q：数据是旧的？**
A：看 `make info` 里的"生成时间"。本地文件需要 `make update`；
   服务器/远程模式则自动跟 GitHub Pages 上的最新数据。

**Q：`date` 排序出现了未来的日期（如 2027-01）？**
A：那是期刊的预发表日期（数据源就这么给的）。本项目一律以
   **发表/上线日期**为准（Boss 明确要求），不做特殊处理。

---

## 七、变更记录

| 日期 | 变化 |
|------|------|
| **2026-09-30** | 首次建立。落地需求 1（配置化+密钥）、4（MCP 接口 + CLI）、5（Zotero 单向推送）。含 150 个离线单元测试 |
| **2026-09-30（下午）** | ⭐ **Zotero 改为默认不用云端**（Boss 要求）：新增 **本地 API 后端**（Zotero 10+ 的 `127.0.0.1:23119`，离线/无账号/无 Key/无插件），默认 `backend = "local"`；web 后端保留为可选项。新增 21 个本地 API 测试（含假 Zotero 服务，覆盖 401 重授权、412 实例刷新）。修掉一个真 bug：**演练（dry-run）会建目录并弹授权框**。测试总数 150 → 183 |
| **2026-09-30（晚）** | ⚠️ 修掉一个**只有新环境才暴露**的真 bug：`requirements.txt` 原写无上界的 `mcp>=1.9`，新装会拿到 **mcp 2.x**（`FastMCP` 改名 `MCPServer`）⇒ MCP 启动失败 `No module named 'mcp.server.fastmcp'`；开发机恰好装着 1.x 所以长期未发现。现钉 `mcp>=1.9,<2`，并让 `_import_fastmcp()` 在 2.x 下抛出**带可执行命令的 ConfigError**。发现方式：一次"模拟陌生人从零部署"的验证。新增 4 个依赖守卫测试（183 → 187） |
