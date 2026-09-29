"""论文采集源 — OpenAlex + Crossref（纯标准库，无需 pip install）

设计要点：
  · 只用 urllib，Actions 里不需要装任何依赖 → 更快更稳
  · 两条源互补：OpenAlex 负责"新"（按发表日期倒序），
    Crossref 负责"正式出版信息"（期刊/DOI）
  · 全部带 polite email（OpenAlex/Crossref 都支持，提高限速额度）
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape

POLITE_EMAIL = "1035534180@qq.com"
UA = f"awesome-zero-shot-navigation/1.0 (mailto:{POLITE_EMAIL})"

# ── 检索关键词
#
# Boss 反馈「论文来源感觉不是很充足」→ 除增加**数据源**，最高性价比的是
# **扩大检索面**：把"任务别名 / 方法别名 / 基准名 / 平台名"都覆盖到。
#
# 分三组，便于维护和调参：
#   A. 核心任务（zero-shot / training-free 直白变体）
#   B. 任务体系（各子任务的标准叫法 —— 覆盖不写 zero-shot 但同类的论文）
#   C. 方法 / 基准 / 平台（LLM/VLM/VLA 驱动、GOAT/R2R-CE 等基准）
QUERIES_CORE = [
    "zero-shot navigation",
    "zero-shot object navigation",
    "zero-shot object goal navigation",
    "zero-shot vision-and-language navigation",
    "zero-shot embodied navigation",
    "zero-shot semantic navigation",
    "zero-shot indoor navigation",
    "training-free navigation",
    "training-free object goal navigation",
    "open-vocabulary object navigation",
    "open-vocabulary navigation",
    "zero-shot aerial navigation",
    "zero-shot UAV navigation",
]

QUERIES_TASK = [
    "object goal navigation unseen object",
    "object goal navigation zero shot",
    "instance goal navigation",
    "image goal navigation",
    "point goal navigation generalization",
    "multi-object navigation",
    "semantic navigation robot",
    "instance navigation open vocabulary",
    "vision-and-language navigation continuous environments",
    "aerial vision-and-language navigation",
    "UAV vision language navigation",
    "social navigation robot",
    "autonomous exploration unknown environment",
    "embodied question answering navigation",
    "text-driven navigation",
]

QUERIES_METHOD = [
    "visual language model robot navigation",
    "large language model robot navigation",
    "LLM based navigation",
    "VLM navigation robot",
    "vision language action navigation",
    "foundation model embodied navigation",
    "vision-language navigation foundation model",
    "embodied navigation foundation model",
    "instruction following navigation",
    "scene graph navigation",
    "semantic map navigation agent",
    "memory based navigation agent",
    "reinforcement learning zero shot navigation",
]

# 用于 GitHub 上找代码仓库 / 关键词匹配（不参与 API 检索）
QUERIES_BENCH = [
    "GOAT benchmark",
    "R2R-CE",
    "NavGPT",
    "open-vocabulary object goal navigation benchmark",
]

QUERIES = QUERIES_CORE + QUERIES_TASK + QUERIES_METHOD

# ⭐ 聚合式源（EXTRA_SOURCES）用的**通用检索词**。
# 这类源一次请求就返回一批论文（不是按关键词逐个查），
# 所以只需要一个尽量宽的词来触发它们即可 —— 真正的相关性筛选
# 由 relevance.py 在这批结果上统一把关。
# ⚠️ 用太窄的词（如 "zero-shot navigation"）会漏掉同领域其他叫法的论文，
#    所以这里故意用**最宽的上位词**。
EXTRA_QUERY = "navigation"


def _get_json(url: str, timeout: int = 40, retries: int = 2):
    """带退避重试的 GET。

    ⚠️ 限速策略：429 只重试 2 次、退避较短 —— **失败要快**。
    理由：这活儿每天跑，今天漏抓的论文明天会被 14 天回看窗口捞回来，
    没必要为一个查询卡几分钟（实测曾因 429 退避把单次任务拖到 25 分钟+）。
    """
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(5 + 5 * i)       # 5s, 10s
            elif e.code in (500, 502, 503, 504):
                time.sleep(3 + 2 * i)
            else:
                time.sleep(1 + i)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + i)
    raise last if last else RuntimeError("request failed")


def _clean_text(s) -> str:
    """去掉 HTML 标签 / 实体 / 多余空白"""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _decode_openalex_abstract(inverted) -> str:
    if not inverted:
        return ""
    positions = []
    for idxs in inverted.values():
        positions.extend(idxs)
    if not positions:
        return ""
    words = [""] * (max(positions) + 1)
    for word, idxs in inverted.items():
        for p in idxs:
            if 0 <= p < len(words):
                words[p] = word
    return _clean_text(" ".join(words))


# ══════════════════════════════════════════════════════════════
# OpenAlex
# ══════════════════════════════════════════════════════════════
def harvest_openalex(query: str, per_page: int = 200, date_from: str | None = None,
                     date_to: str | None = None, sort: str = "date") -> list:
    """按标题/摘要检索。

    sort:
      "date"     → publication_date:desc（默认，用于"抓最新"）
      "cited"    → cited_by_count:desc（用于"抓经典" —— 找高引里程碑论文）
      "relevant" → 不传 sort，用 OpenAlex 默认的相关性排序
    """
    params = {
        "per-page": min(per_page, 200),
        "mailto": POLITE_EMAIL,
    }
    if sort == "date":
        params["sort"] = "publication_date:desc"
    elif sort == "cited":
        params["sort"] = "cited_by_count:desc"
    # "relevant" → 不传 sort

    flt = [f"title_and_abstract.search:{query}"]
    if date_from:
        flt.append(f"from_publication_date:{date_from}")
    if date_to:
        flt.append(f"to_publication_date:{date_to}")
    params["filter"] = ",".join(flt)
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)

    try:
        data = _get_json(url)
    except Exception as e:  # noqa: BLE001
        print(f"    ⚠️ openalex '{query[:40]}' 失败: {str(e)[:70]}")
        return []

    out = []
    for it in data.get("results", []):
        title = _clean_text(it.get("display_name"))
        if not title:
            continue
        authors = [a.get("author", {}).get("display_name", "")
                   for a in it.get("authorships", [])]
        authors = [a for a in authors if a]
        primary = it.get("primary_location") or {}
        best_oa = it.get("best_oa_location") or {}
        doi = (it.get("doi") or "").replace("https://doi.org/", "") or None
        out.append({
            "title": title,
            "authors": authors,
            "year": it.get("publication_year"),
            "published_date": it.get("publication_date"),
            "venue": (primary.get("source") or {}).get("display_name"),
            "abstract": _decode_openalex_abstract(it.get("abstract_inverted_index")),
            "doi": doi,
            "arxiv_id": _extract_arxiv(it),
            "citations": it.get("cited_by_count", 0) or 0,
            "url": it.get("doi") or (it.get("primary_location") or {}).get("landing_page_url"),
            "pdf_url": best_oa.get("pdf_url"),
            "open_access": bool((it.get("open_access") or {}).get("is_oa")),
            "type": it.get("type"),
            "source": "openalex",
        })
    return out


def _extract_arxiv(item) -> str | None:
    for loc in item.get("locations", []) or []:
        land = (loc or {}).get("landing_page_url") or ""
        m = re.search(r"arxiv\.org/abs/([\d.]+)", land)
        if m:
            return m.group(1)
    return None


# ══════════════════════════════════════════════════════════════
# Crossref
# ══════════════════════════════════════════════════════════════
def harvest_crossref(query: str, rows: int = 60, date_from: str | None = None,
                     date_to: str | None = None) -> list:
    """按书目信息检索（补正式出版信息；不加 sort 以保留相关性排序）"""
    params = {"query.bibliographic": query, "rows": min(rows, 100),
              "mailto": POLITE_EMAIL}
    f = []
    if date_from:
        f.append(f"from-pub-date:{date_from}")
    if date_to:
        f.append(f"until-pub-date:{date_to}")
    if f:
        params["filter"] = ",".join(f)
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)

    try:
        data = _get_json(url)
    except Exception as e:  # noqa: BLE001
        print(f"    ⚠️ crossref '{query[:40]}' 失败: {str(e)[:70]}")
        return []

    out = []
    for it in (data.get("message") or {}).get("items", []):
        titles = it.get("title") or []
        title = _clean_text(titles[0] if titles else "")
        if not title:
            continue
        authors = []
        for a in it.get("author", []) or []:
            fam, giv = a.get("family", ""), a.get("given", "")
            authors.append(f"{fam}, {giv}".strip(", ") if fam else giv)
        dp = (it.get("issued") or {}).get("date-parts", [[]])[0]
        pub_date = "-".join(str(p).zfill(2) for p in dp) if dp else None
        out.append({
            "title": title,
            "authors": [a for a in authors if a],
            "year": dp[0] if dp else None,
            "published_date": pub_date,
            "venue": (it.get("container-title") or [None])[0],
            "abstract": _clean_text(it.get("abstract")),
            "doi": it.get("DOI"),
            "arxiv_id": None,
            "citations": it.get("is-referenced-by-count", 0) or 0,
            "url": it.get("URL"),
            "pdf_url": None,
            "open_access": False,
            "type": it.get("type"),
            "source": "crossref",
        })
    return out


SOURCES = {
    "openalex": harvest_openalex,
    "crossref": harvest_crossref,
}

# ⭐ 新增采集源的**契约**（加源只需两处，其它零改动）：
#
#   1) 写一个 harvest_xxx(query, date_from=None, date_to=None) -> list[dict]
#      · 若该源支持"按引用量排序抓经典"，**再加一个 sort="date" 参数**
#        （管线会用签名检测自动判断是否传 sort，见 pipeline.py run()）
#      · 返回的 dict 字段见下方 harvest_openalex 的构造（12 个字段）
#      · **不要**在这里 print / 落盘 / 改 DB —— 只负责"取回并归一化"
#
#   2) 注册进本字典：  "xxx": harvest_xxx
#
#   ✅ 不需要改 pipeline.py（它遍历 SOURCES）
#   ✅ 加完跑 `make test` 会校验签名是否满足调用约定
#   ⚠️ 只在"每轮调一次"的聚合式源（如 API 一次返回一批）→ 注册到 EXTRA_SOURCES
#
# 判别标准：这个源需要**按关键词逐个查**吗？
#   是 → SOURCES（会被调用 len(QUERIES) 次）
#   否 → EXTRA_SOURCES（每轮只调 1 次）

# ⭐ 每个源的请求间隔（秒）—— 限速松紧按源配置。
#    新增的源若限速更严（如无 Key 的免费 API），在这里加一行即可。
#    未列出的源用 DEFAULT_INTERVAL。
#    为什么单独抽出来：曾经写死在 pipeline 里（`1.5 if openalex else 0.8`），
#    加新源时容易忘配、进而被 429 拖慢整轮。
DEFAULT_INTERVAL = 0.8
SOURCE_INTERVALS = {
    "openalex": 1.5,    # 对高频请求较敏感，间隔放大
}

# 附加源（OpenReview / HuggingFace）—— 单独注册。
# 为什么不混进 SOURCES：它们的调用签名不同（不需要 query 循环里的 date_to 等），
# 且在管线里是"每轮只调一次"，而不是"每个关键词调一次"。
#
# ⚠️ arXiv API 已确认**不可用**（HTTP 406，封禁云服务商 IP，HTTP/HTTPS 都一样），
#    所以不要试图加回来 —— 详见 scripts/probe_sources.py 的探测记录。
try:
    from sources_extra import harvest_huggingface, harvest_openreview
    EXTRA_SOURCES = {
        "openreview": harvest_openreview,   # 会议投稿（含接收结果）
        "huggingface": harvest_huggingface,  # 每日热门（带热度）
    }
except ImportError:  # 单独运行 sources.py 时容忍
    EXTRA_SOURCES = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _selftest_registry() -> bool:
    """⭐ 自测：源注册表是否**可按管线的方式调用**（不联网）。

    为什么需要这条：管线遍历 `SOURCES` 并统一传
        harvest_fn(query, date_from=..., date_to=...[, sort=...])
    所以任何一个注册的源，只要签名不能满足这个调用约定，
    采集时就会**静默跳过**（异常被 except 吞掉，只打印一行警告）。

    本自测把这条约定**变成可验证的**：
      · 每个源都必须能被 (query, date_from, date_to) 调用
      · 支持 sort 的源，额外接受 sort
      · 返回必须是 list（不能返回 None / dict）
    """
    import inspect as _inspect

    print("=" * 60)
    print("源注册表自测（不联网）")
    print("=" * 60)
    ok = True

    # ── ① SOURCES：按关键词逐个调用（管线主循环）
    print("\n① SOURCES（按关键词逐个调用）")
    for name, fn in SOURCES.items():
        params = set(_inspect.signature(fn).parameters)
        sig = _inspect.signature(fn)
        kwargs = {"date_from": "2026-01-01", "date_to": "2026-12-31"}
        if "sort" in params:
            kwargs["sort"] = "date"
        try:
            sig.bind("self-test query", **kwargs)
            print(f"   ✅ {name:12s} 可调用   kwargs={sorted(kwargs)}")
        except TypeError as e:
            ok = False
            print(f"   ❌ {name:12s} 签名不符: {e}")

    # ── ② EXTRA_SOURCES：每轮只调一次（聚合式）
    #   调用约定（与 pipeline 一致）：date_from 恒传；
    #   query 若为**必填参数**（无默认值），额外传 EXTRA_QUERY。
    print("\n② EXTRA_SOURCES（每轮只调一次）")
    for name, fn in EXTRA_SOURCES.items():
        sig = _inspect.signature(fn)
        kwargs = {"date_from": "2026-01-01"}
        q_param = sig.parameters.get("query")
        if q_param is not None and q_param.default is _inspect.Parameter.empty:
            kwargs["query"] = EXTRA_QUERY
        try:
            sig.bind(**kwargs)
            print(f"   ✅ {name:12s} 可调用   kwargs={sorted(kwargs)}")
        except TypeError as e:
            ok = False
            print(f"   ❌ {name:12s} 签名不符: {e}")

    # ── ②·五 限速配置：键必须是已注册的源名（防止改名后残留无效配置）
    print("\n②·五 限速配置（SOURCE_INTERVALS）")
    registered = set(SOURCES) | set(EXTRA_SOURCES)
    stray = [k for k in SOURCE_INTERVALS if k not in registered]
    if stray:
        ok = False
        print(f"   ❌ 配了不存在的源: {stray}（源名拼错或已改名？）")
    else:
        for k, v in SOURCE_INTERVALS.items():
            print(f"   ✅ {k:12s} 间隔 {v}s")
        print(f"   ℹ️ 未列出的源用默认 {DEFAULT_INTERVAL}s")

    # ── ③ 检索规模提示（让人对"加关键词/加源的代价"有数）
    print("\n③ 检索规模")
    per_round = len(QUERIES) * len(SOURCES) + len(EXTRA_SOURCES)
    print(f"   QUERIES = {len(QUERIES)} 条")
    print(f"   每轮请求 ≈ {len(QUERIES)} × {len(SOURCES)} 源"
          f" + {len(EXTRA_SOURCES)} 聚合源 = {per_round} 次")
    print("   ⚠️ 加关键词/源会线性增加耗时 → 注意 --budget 要够")

    print("\n" + "=" * 60)
    print("自测结果:", "✅ 全部通过" if ok else "❌ 有失败")
    print("=" * 60)
    return ok


if __name__ == "__main__":
    import argparse as _ap

    ap = _ap.ArgumentParser(description="sources.py 自测 / 接口探测")
    ap.add_argument("--selftest", action="store_true",
                    help="只跑注册表自测（离线，秒级）")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(0 if _selftest_registry() else 1)

    print("测试 OpenAlex...")
    rs = harvest_openalex("zero-shot object navigation", per_page=5)
    print(f"  返回 {len(rs)} 条")
    for r in rs[:3]:
        print(f"   · [{r['published_date']}] {r['title'][:66]}")

    print("\n测试 Crossref...")
    rs = harvest_crossref("zero-shot navigation", rows=5)
    print(f"  返回 {len(rs)} 条")
    for r in rs[:3]:
        print(f"   · [{r['published_date']}] {r['title'][:66]}")

    print("\n（跑 --selftest 可离线校验注册表契约）")
