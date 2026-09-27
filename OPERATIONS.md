# 运维手册（Operations）

> 这份文档回答四个问题：**跑在哪？谁来跑？跑多勤？增量还是全量？**
> 以及出问题时怎么办。

---

## 一、跑在哪？谁触发？

**答：跑在 GitHub 的服务器上（GitHub Actions），不需要任何人的电脑开着，也不由小莫触发。**

```
GitHub Actions（云端，每周一自动）
   ↓ 采集 → 过滤 → 增强 → 导出 → 提交 → 部署
GitHub Pages（免费托管）
   ↓
https://ser666.github.io/awesome-zero-shot-navigation/
```

| 组件 | 在哪运行 | 触发方式 | 需要本机开机吗 |
|------|---------|---------|--------------|
| 采集 + 导出 + 部署 | **GitHub Actions**（云端） | ⏰ cron `17 2 * * 1` + 手动 + 代码推送 | ❌ 不需要 |
| 网站托管 | **GitHub Pages** | 由上面自动部署 | ❌ 不需要 |
| **防停看护** | ⚠️ **小莫的本机 cron** | 每月 1 日 11:00 | ✅ 需要 |

> 🎯 **关键结论：内容的日常更新完全自持，不依赖小莫、也不依赖任何一台电脑。**
> 唯一在本机的是"防停看护"（见第四节）——
> 因为 GitHub 那条"60 天无活动就静默禁用定时任务"的规则是**鸡生蛋**问题：
> 一旦任务被禁用，仓库里自己的检查也跑不起来了，**必须有个仓库外的看护者**。

**时间**：每周一 02:17 UTC = **北京时间周一 10:17**
（刻意避开整点 —— GitHub 文档明确说整点最拥堵，任务可能延迟甚至被丢弃）

---

## 二、跑多勤？（每日 → 每周）

**答：已按 Boss 要求改成**每周一次**。

| | 每周（当前） | 每日（改法） |
|---|---|---|
| cron | `17 2 * * 1` | `17 2 * * *` |
| 回看窗口 | 21 天 | 14 天 |
| Actions 时长 | ~10-20 分钟/周 | ~10-20 分钟/天 |
| 被数据源限速的概率 | 低 | 较高 |
| 新鲜度 | 滞后 ≤7 天 | 滞后 ≤1 天 |

**改法**：编辑 `.github/workflows/weekly-update.yml` 里的 `cron`，一行即可，其余不用动。

> 💡 **我的建议**：**保持每周**。理由：
> ① 这个列表的价值在"**窄而深 + 全自动**"，不在"抢首发"；
> ② 每日跑会显著提高被 OpenAlex / Semantic Scholar 限速（429）的概率，
>    而限速会让单轮任务变得不稳定；
> ③ 每周一次已经足够让"新论文"（`NEW` 标记窗口 14 天）保持有效。

---

## 三、增量还是全量？

**答：日常是【增量】；另有几个可手动触发的模式。**

| 命令 | 做什么 | 何时用 |
|------|-------|-------|
| `--days 21`（默认） | ⭐ **增量**：只抓最近 21 天的，与库内去重合并 | 每周自动跑的就是它 |
| `--full` | 从 2019 年起全量抓（慢，且仍偏重近期） | 首次建库 / 想扩充历史 |
| `--year 2023` | 只抓某一年 | 补特定年份 |
| `--landmarks` | 按引用量排序抓"经典"（⚠️ OpenAlex 对该排序常返回 503，不保证成功） | 想补高引里程碑 |
| `--refilter` | 用**当前规则**重新审核库内所有论文，删掉不再相关的 | 改了相关性规则后 |
| `--backfill` | 重算派生字段（会议分级 / 子专题 / 链接），**不联网** | 改了分级表或标签后 |
| `--enrich` | 逐篇补引用数 / 开放 PDF / 代码仓库（**限流**） | 补链接和引用 |

### 增量是怎么保证"不漏"的

三个机制叠加：

1. **回看窗口**：每周跑却回看 21 天 → 即使某一周失败，下一周还能捞回来
2. **跨源去重**：DOI / arXiv ID / 标题模糊匹配（相似度 ≥0.88）→ 同一篇论文多源抓到只算一篇
3. **失败快退**：429 只重试 2 次、退避很短 → **宁可这次少抓，也不拖死整轮**
   （对周期性任务，**"下一轮"本身就是天然的重试**）

---

## 四、防停看护（60 天规则）

### 问题

GitHub 官方文档（原话）：

> *"In a public repository, scheduled workflows are automatically disabled when no
> repository activity has occurred in 60 days."*

**而且不报错** —— 你以为在跑，其实早就停了。

### 三重保险

| 层 | 机制 | 位置 |
|----|------|------|
| ① | 提交用真实身份（commit 算"仓库活动"） | workflow 里 `git config user.email` |
| ② | 工作流内置健康检查（数据 > 45 天未更新就报错） | `weekly-update.yml` 的 `health-check` |
| ③ | ⭐ **仓库外的月度看护**（核心） | 本机 cron → `scripts/keepalive.py` |

**为什么必须有第 ③ 层**：一旦定时任务被禁用，`health-check` 也跑不起来了。
**必须有一个"仓库外"的看护者**去检查并重新启用：

```python
PUT /repos/{owner}/{repo}/actions/workflows/{file}/enable
```

### 看护脚本做什么

```bash
python3 scripts/keepalive.py
```

1. 查定时任务状态 → 若是 `disabled_inactivity` 就**自动重新启用**
2. 用 token 做一次真实提交（制造"账号活动"）
3. 数据超过阈值就手动触发一次运行（`workflow_dispatch`）补跑
4. 检查失败会明确报错（不静默）

> ⚠️ **这一层跑在本机**：如果本机长期不开机，看护会漏（但它是月度的，容错窗口很大）。

---

## 五、数据源与限速

### 实测可用性（GitHub Actions 环境）

| 源 | 状态 | 用途 | 备注 |
|----|------|------|------|
| OpenAlex | ✅ | 主采集（标题/摘要检索） | 偶发 429/503 |
| Crossref | ✅ | 出版信息 / DOI | 稳 |
| **OpenReview** | ✅ | 会议投稿与接收结果 | 稳 |
| **Hugging Face Papers** | ✅ | 每日热门（热度信号） | 稳 |
| Semantic Scholar | ⚠️ | 引用数 / TLDR | **搜索接口**无 Key 时 429 严重；**单篇接口**稳 |
| Unpaywall | ✅ | 开放获取 PDF | 稳，需真实 DOI |
| GitHub API | ✅ | 找代码仓库 | 认证后 30 次/分钟 |
| ❌ arXiv API | ❌ | —— | **HTTP 406，封禁云服务商 IP**，HTTP/HTTPS 都一样 |
| ❌ DBLP | ❌ | —— | 被 Anubis 挑战页拦（本机和云端都拦） |
| ❌ Papers with Code | ❌ | —— | API 随站点关停 |

> 🔍 **怎么自己复核**：`python3 scripts/probe_sources.py`
> （**建议在 Actions 上跑** —— 本机在中国大陆，很多源连不上，测了也不代表云端）
> 也可以在 GitHub 网页上手动触发 `Probe Sources` 工作流。

### 限速的三道闸

```
① 单源间隔     OpenAlex 1.5s / Crossref 0.8s / S2 0.6s / GitHub 2.2s
② 时间预算     采集 600s、增强 900s —— 超了就不再发新请求，直接收尾
③ 调用次数上限  GitHub 搜索 250 次/轮（最慢的一步单独限流）
```

**超预算怎么办？** 不用管 —— 下一轮接着做。
列表是**长期维护**的资产，不追求一轮做完。

---

## 六、出问题怎么办

| 症状 | 先看这里 | 处理 |
|------|---------|------|
| **网站数据不更新** | Actions 里 `deploy-pages` 作业是否成功 | ⚠️ 这是最容易踩的坑：**用默认 token 的推送不会触发其他工作流**，所以 Pages 部署必须**写在同一个工作流里**（`deploy-pages` 作业） |
| 定时任务莫名停了 | `python3 scripts/keepalive.py` | 脚本会自动重新启用 |
| 数据好久没变 | Actions 是否 `disabled_inactivity` | 同上 |
| 单轮任务跑很久 | 是否被 429 拖住 | 已加"失败快退 + 时间预算"；若仍慢，调小 `--budget` |
| 收录了无关论文 | `python3 scripts/audit.py` | 改 `scripts/relevance.py` **并加测试用例**，再跑 `--refilter`；⚠️ **收紧前必须先跑影响评估**（见下） |
| 会议名/子专题不对 | —— | 改 `scripts/venues.py` / `scripts/topics.py`，跑 `--backfill`（秒级，不联网） |
| **代码链接挂错仓库** | 抽查 `data/papers.db` | 改 `scripts/links.py`（**精度优先于召回**：宁可没有，也不能挂错） |
| 网站打不开 | Pages 设置 | 确认 Settings → Pages → Source = **GitHub Actions** |

### ⚠️ 一个要接受的现实：每周都会进来几篇杂音

规则 B2（标题含导航词 + 摘要含具身词 + 摘要导航密集）是**刻意放宽**的 ——
因为"漏掉真论文"的代价远大于"多收一篇无关的"。副作用是：**每次采集都会混进
几篇"借用了 navigation 一词"的论文**，典型是导航系统工程类
（制导/惯导/组合导航标定/UWB-INS 融合）。

实测两轮累计清掉 10 篇，例如：

```
· Monte Carlo Diagnostics of a Launch Vehicle Guidance Navigation and Control Simulator
· ...GRU-SAC Measurement Covariance Adaptation for Robust UWB/INS Indoor Navigation
· PedestrianDiffusion: ... 6D State Estimation for Inertial Navigation
· A New Algorithm for Navigation Trajectory Prediction of Land Vehicles
```

**这是设计取舍，不是 bug。** 处理方式：

1. 每季度（或想清理时）跑一次 `python3 scripts/audit.py` 抽查
2. 跑 `python3 scripts/impact.py` 看会删哪些 → **逐条核对摘要**（别只看数字）
3. 确认无误后把新词加进 `relevance.py` 的 `TITLE_ONLY_NEGATIVE_TERMS`
   （⚠️ **只加"高度具体"的组合词**，且**只看标题**）
4. **给每个新增词补一条回归测试**，同时补"必须通过"的兄弟案例
5. `python3 scripts/pipeline.py --refilter` 清洗 + `scripts/export.py` 重新导出

### 本地排查流程

```bash
make test        # 规则自测（全部应通过）
make update      # 手动跑一轮更新
make serve       # 本地起网站 → http://127.0.0.1:8210
make audit       # 查数据质量
```

---

## 七、数据与凭证

| 项 | 位置 | 说明 |
|----|------|------|
| 数据集 | `data/papers.db`（SQLite，**提交进 git**） | 有完整历史，可复现 |
| 网站数据 | `docs/data/papers.json` | 由 `export.py` 生成 |
| 列表 | `README.md` | 由 `export.py` 生成 |
| GitHub token（本地） | `~/.hermes/secrets/github_pat`（权限 600） | **不进 git**，用于本机增强 |
| GitHub token（CI） | Actions 内置 `GITHUB_TOKEN`；可选配 `secrets.GH_PAT` | 用于 CI 里搜代码仓库 |

> ⚠️ **绝不把 token 写进任何会被提交的文件。**
> CI 里的 token 只写进 `$HOME/.hermes/secrets/`（**不在仓库目录内**）。

---

## 八、成本

| 项 | 费用 |
|----|------|
| GitHub Actions（公开仓库） | **免费** |
| GitHub Pages（公开仓库） | **免费** |
| 所有数据源 API | **免费**（无 Key） |
| 服务器 | **不需要** |
| 域名 | **不需要** |
| **合计** | **0 元** |

> ⚠️ 注意 Pages 条款：**不得**用作商业网站 / SaaS 托管。
> 学术列表 / 个人 IP ✅；付费产品 ❌。

---

## 九、每月/每季建议的例行检查

- [ ] **每月**：确认 `keepalive.py` 看护正常（本机 cron 会汇报）
- [ ] **每季**：跑一次 `python3 scripts/probe_sources.py`（在 Actions 上）
      —— 数据源会变（arXiv 就是突然开始封云 IP 的），要定期复核
- [ ] **每季**：抽查 10 条代码链接是否指向正确仓库
- [ ] **偶尔**：看 star 增长 / issue，作为"内容是否被认可"的信号
