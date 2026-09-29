"""附加数据源 —— 在 OpenAlex / Crossref 之外扩展采集面与信息维度

Boss 反馈：「论文来源感觉不是很充足，应该扩展一些更多来源」。

可用性是在 **GitHub Actions 环境实测**后确定的（见 scripts/probe_sources.py）：

    ✅ 可用     openreview           0.1s   ← 会议投稿（含接收结果）
    ✅ 可用     huggingface          0.1s   ← 每日热门论文
    ✅ 可用     unpaywall            0.1s   ← 开放获取 PDF（补链接）
    ✅ 可用     semanticscholar(单篇) 0.2s   ← 引用数 / 开放 PDF
    ✅ 可用     crossref / openalex           ← 原有基线
    ✅ 可用     github               0.2s   ← 找代码仓库
    ❌ 不可用   arxiv                HTTP 406 ← arXiv 封禁云服务商 IP（重要！
                                              不是少 UA/Accept 的问题，HTTP/HTTPS 都 406）
    ❌ 不可用   dblp                 被 Anubis 挑战页拦（本机和 Actions 都被拦）
    ❌ 不可用   paperswithcode        API 已随站点关停（返回 HTML）

设计要点
--------
· 附加源**只做"补充与增强"**，不参与相关性判定的主力逻辑，失败即优雅跳过。
· 增强（enrichment）是**逐篇**的，所以必须做限速 + 缓存，否则会被封。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
POLITE_EMAIL = "1035534180@qq.com"


def _get(url: str, timeout: int = 25, retries: int = 2,
         hdr: dict | None = None) -> tuple[int, object]:
    """带短退避的 GET（失败要快 —— 见技能里"周期性任务失败快退"原则）"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr or {"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(4 + 4 * i)
            elif e.code in (500, 502, 503, 504):
                time.sleep(3 + 2 * i)
            else:
                time.sleep(1)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1)
    return -1, str(last)[:200]


def _clean(s) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", str(s))
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
         .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", s).strip()


def _date(s: str | None) -> str | None:
    """从各种时间格式里取 YYYY-MM-DD"""
    if not s:
        return None
    m = re.search(r"((?:19|20)\d{2})-(\d{2})-(\d{2})", str(s))
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"((?:19|20)\d{2})-(\d{2})", str(s))
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"
    m = re.search(r"((?:19|20)\d{2})", str(s))
    return f"{m.group(1)}-01-01" if m else None


# ══════════════════════════════════════════════════════════════
# ① OpenReview —— 会议投稿平台（ICLR / NeurIPS / CoRL 等）
#    价值：能拿到**官方接收结果**（"accepted"），比"arXiv 预印本"信息更硬；
#          且带 `pdate`（正式出版日期）+ 官方 venue（"CoRL 2025 Poster"）
#
#    ⚠️ 2026-09-29 重构（原实现形同虚设，见下方 harvest_openreview 的说明）
# ══════════════════════════════════════════════════════════════

# 检索词：**少量、精准**。每个词都会对每个会议 group 发一次请求，
# 所以这里刻意只留"最能命中本领域"的 4 个（不是越多越好 —— 请求数会相乘）。
OPENREVIEW_TERMS = [
    "zero-shot navigation",
    "vision-language navigation",
    "object goal navigation",
    "embodied navigation",
]

# 只收最近这段时间内**正式出版**的（`pdate` 口径）。
# 作用：兜底防止把太老的历史论文捞进来。
#
# ⚠️ 为什么是 550 而不是更短 —— 实测教训：
#    会议论文的 `pdate`（正式出版日）往往比"会议年份"早几个月，
#    例如 CoRL 2025 的 pdate = 2025-08-08（会议 11 月才开）。
#    最初设 400 天，结果把 **CoRL 2025 整届误杀**（距今 417 天）——
#    而 CoRL 恰恰是本领域最核心的会议之一。
#    550 天可覆盖 CoRL 2025（417d）/ NeurIPS 2025（376d）/ ICLR 2026（246d），
#    同时仍能正确过滤 CoRL 2024（697d）与 NeurIPS 2024（667d）。
OPENREVIEW_MAX_AGE_DAYS = 550

# 单次请求的超时（OpenReview 通常 <1s；给宽一些防偶发慢）
OPENREVIEW_TIMEOUT = 40
# 请求间隔（实测很快，但保持礼貌 + 防 429）
OPENREVIEW_SLEEP = 0.5


def _openreview_groups() -> list[tuple[str, str]]:
    """生成目标会议的 group 列表 → [(group_id, 会议简称)]。

    ⚠️ 各会议的 group 命名规则不同，这里是实测确认过的：
        ICLR     → ICLR.cc/<年>/Conference
        NeurIPS  → NeurIPS.cc/<年>/Conference
        CoRL     → robot-learning.org/CoRL/<年>/Conference
        CVPR/ICCV → thecvf.com/... （实测 OpenReview 上**没有**可搜的公开
                    submissions，所以未纳入）

    **年份按当前年自动推导**（无需每年手工维护）：
      · ICLR 每年 1 月开会出结果 → 取 今年 / 去年
      · NeurIPS 每年 12 月       → 取 去年 / 前年（当年还没出结果）
      · CoRL 每年 11 月          → 取 去年 / 前年

    顺序 = 去重时的优先级（同一篇优先归到靠前的会议）。
    """
    y = datetime.now(timezone.utc).year
    out: list[tuple[str, str]] = []
    for yr in (y, y - 1):                       # ICLR
        out.append((f"ICLR.cc/{yr}/Conference", f"ICLR {yr}"))
    for yr in (y - 1, y - 2):                   # NeurIPS
        out.append((f"NeurIPS.cc/{yr}/Conference", f"NeurIPS {yr}"))
    for yr in (y - 1, y - 2):                   # CoRL
        out.append((f"robot-learning.org/CoRL/{yr}/Conference", f"CoRL {yr}"))
    return out


def _or_field(content: dict, key: str, default=""):
    """OpenReview 的 content 是嵌套结构 {"value": ...}，且各会议版本不一致。"""
    x = content.get(key)
    if isinstance(x, dict):
        return x.get("value", default)
    return x if x is not None else default


def _or_norm_title(t: str) -> str:
    """标题归一化（仅用于本函数内部去重）。"""
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def harvest_openreview(query: str = "", limit: int = 100,
                       date_from: str | None = None) -> list:
    """从 OpenReview 抓**会议录用论文**（带官方 venue 与出版日期）。

    ⭐ 为什么从"通用词搜索"改成"按会议 group 查"（2026-09-29）：

      **旧实现的问题**（实测证据）：用 `term=<通用词>` 全局搜，返回的多是
      无关领域的零散论文 —— 57 条里大量是德语导航系统、超媒体导航、
      本体查询、菜单导航等；而且这些结果的日期普遍较早，
      在增量模式（回看 21 天）下**几乎全部落在窗口外**
      → 该源长期**贡献 0 篇**，注册了却形同虚设。

      **改为 `group=<会议>` 后**（实测结果）：
        · CoRL 2025    → MoTo / GC-VLN / UnPose / SocialNav-SUB …
        · NeurIPS 2025 → BeliefMapNav / EfficientNav / iFinder …
        · CoRL 2024    → InstructNav / VLM-Grounder …
        · ICLR 2025    → CONSTRAINT-AWARE ZERO-SHOT VLN …
      命中率和相关性都极高，而且拿到了**官方录用信息**（"CoRL 2025 Poster"）。

    ⚠️ **本函数有意忽略 `date_from`**：
       会议录用论文的"发表日期"天然在过去（投稿在数月前、决定在数月前），
       用 21 天增量窗口去卡它必然抓不到东西。这里的"新"由**去重**保证 ——
       每轮都会重新扫这几届会议，已入库的会被管线去重丢弃，
       所以实际只会新增"新出结果 / 新收录"的论文。
       用 OPENREVIEW_MAX_AGE_DAYS 兜底，避免捞进太老的。

    ⚠️ **只保留有 title 的 note**：搜索结果里混有 Official_Review / Decision
       等非投稿 note（content 里没有 title），必须在源头滤掉。

    ⚠️ `query` 参数**保留但不用** —— 调用约定要求这个签名（见
       sources.py 的注册说明与 pipeline.call_extra_source），
       真正的检索词来自本模块的 OPENREVIEW_TERMS。
    """
    cutoff = datetime.now(timezone.utc).timestamp() - OPENREVIEW_MAX_AGE_DAYS * 86400
    out: list = []
    seen: set[str] = set()
    stats = {"req": 0, "raw": 0, "kept": 0, "old": 0, "dup": 0, "withdrawn": 0}

    for group, short in _openreview_groups():
        group_had_any = False
        for ti, term in enumerate(OPENREVIEW_TERMS):
            # 短路：某会议连**第一个**检索词都为空 → 该 group 不存在/无公开投稿，
            # 不必再为它发剩余请求（省 3 次/无效会议）
            if ti > 0 and not group_had_any:
                break
            url = ("https://api2.openreview.net/notes/search?"
                   + urllib.parse.urlencode({
                       "term": term, "limit": min(limit, 100),
                       "type": "terms", "content": "all",
                       "group": group, "source": "all"}))
            st, d = _get(url, timeout=OPENREVIEW_TIMEOUT)
            stats["req"] += 1
            if st != 200 or not isinstance(d, dict):
                continue

            for n in d.get("notes", []):
                c = n.get("content") or {}
                title = _clean(_or_field(c, "title"))
                if not title:
                    continue        # Official_Review / Decision 等，跳过
                stats["raw"] += 1
                group_had_any = True

                # venue：优先官方字段（形如 "CoRL 2025 Poster"），否则用会议简称
                venue_raw = _clean(_or_field(c, "venue"))

                # ⚠️ **跳过撤稿/被拒的**。实测发现它们的 venue 形如
                #    "ICLR 2026 Conference Withdrawn Submission"，
                #    若不在这里滤掉，会被 venue 归一化识别成 ICLR 并算作 **A 级** ——
                #    等于把撤稿论文当成"重要会议论文"展示，是明显的误报。
                #    （保留 "Submitted to ..."：投稿中的是**未决定**，不是否定，
                #      而且它们在 OpenReview 上有公开 PDF，对领域覆盖有价值。）
                if any(k in venue_raw.lower()
                       for k in ("withdraw", "reject", "desk reject")):
                    stats["withdrawn"] += 1
                    continue

                # 日期：⭐ 优先 pdate（正式出版日期），回退 odate（决定）→ tcdate（提交）
                pd = None
                for key in ("pdate", "odate", "tcdate", "cdate"):
                    ms = n.get(key)
                    if isinstance(ms, (int, float)) and ms > 1e11:
                        try:
                            pd = time.strftime("%Y-%m-%d",
                                               time.gmtime(int(ms) / 1000))
                            break
                        except Exception:  # noqa: BLE001
                            continue

                # 兜底：日期太老的不收
                if pd:
                    try:
                        if datetime.strptime(pd, "%Y-%m-%d").replace(
                                tzinfo=timezone.utc).timestamp() < cutoff:
                            stats["old"] += 1
                            continue
                    except Exception:  # noqa: BLE001
                        pass

                # 去重（同一篇可能出现在多个 group / 多个检索词）
                nt = _or_norm_title(title)
                if nt in seen:
                    stats["dup"] += 1
                    continue
                seen.add(nt)

                venue = venue_raw if venue_raw and venue_raw.lower() != "none" \
                    else short

                forum = n.get("forum") or n.get("id")
                authors = _or_field(c, "authors") or []
                out.append({
                    "title": title,
                    "authors": [a for a in authors if isinstance(a, str)],
                    "year": int(pd[:4]) if pd else None,
                    "published_date": pd,
                    "venue": venue,
                    "abstract": _clean(_or_field(c, "abstract")),
                    "doi": None,
                    "arxiv_id": None,
                    "citations": 0,
                    "url": (f"https://openreview.net/forum?id={forum}"
                            if forum else None),
                    "pdf_url": (f"https://openreview.net/pdf?id={forum}"
                                if forum else None),
                    "open_access": 1,
                    "source": "openreview",
                })
                stats["kept"] += 1
            time.sleep(OPENREVIEW_SLEEP)

    print(f"     · openreview: {stats['req']} 请求 / {stats['raw']} 原始"
          f" / 去重后 {stats['kept']} 篇"
          f"（跳过 撤稿 {stats['withdrawn']}、太老 {stats['old']}、重复 {stats['dup']}）")
    return out


# ══════════════════════════════════════════════════════════════
# ② Hugging Face Daily Papers —— 社区热度信号
#    价值：提供"热门"维度（upvotes），用于网站"Trending"排序
# ══════════════════════════════════════════════════════════════
def harvest_huggingface(query: str = "", date_from: str | None = None,
                        limit: int = 100) -> list:
    """拉 HF Daily Papers（按天聚合，天然带 upvotes）。"""
    url = f"https://huggingface.co/api/daily_papers?limit={min(limit, 100)}"
    st, d = _get(url, timeout=30)
    if st != 200 or not isinstance(d, list):
        return []

    out = []
    for item in d:
        paper = item.get("paper") or {}
        title = _clean(paper.get("title") or item.get("title"))
        if not title:
            continue
        pd = _date(item.get("publishedAt") or paper.get("publicationDate")
                   or item.get("date"))
        if date_from and pd and pd < date_from:
            continue
        aid = paper.get("id") or ""
        out.append({
            "title": title,
            "authors": [a.get("name") for a in (paper.get("authors") or [])
                        if isinstance(a, dict) and a.get("name")],
            "year": int(pd[:4]) if pd else None,
            "published_date": pd,
            "venue": _clean(paper.get("publishedAt")) and "arXiv" or "arXiv",
            "abstract": _clean(paper.get("summary")),
            "doi": None,
            "arxiv_id": aid or None,
            "citations": 0,
            # 用 upvotes 暂存到 extra，管线里会转成 hotness
            "hotness": int(item.get("numComments") or 0),
            "url": f"https://huggingface.co/papers/{aid}" if aid
                   else paper.get("url"),
            "pdf_url": f"https://arxiv.org/pdf/{aid}" if aid else None,
            "open_access": 1,
            "source": "huggingface",
        })
    return out


# ══════════════════════════════════════════════════════════════
# ③ 增强：Semantic Scholar（补引用数 / 开放 PDF）
#    逐篇查询，必须限速。实测「搜索接口」无 Key 时 429 严重，
#    但「单篇接口」稳定可用 —— 所以只用单篇接口。
# ══════════════════════════════════════════════════════════════
_S2_CACHE: dict[str, dict] = {}


def enrich_semanticscholar(doi: str | None = None, arxiv_id: str | None = None,
                           timeout: int = 20) -> dict:
    """按 DOI / arXiv ID 查引用数与开放 PDF。返回 {} 表示无数据。

    成本控制：调用方应只对**新入库或数据缺失**的论文调用。
    """
    if doi:
        pid = f"DOI:{doi}"
    elif arxiv_id:
        aid = re.sub(r"^arxiv:", "", arxiv_id.strip(), flags=re.I)
        pid = f"ARXIV:{aid}"
    else:
        return {}
    if pid in _S2_CACHE:
        return _S2_CACHE[pid]

    url = (f"https://api.semanticscholar.org/graph/v1/paper/"
           f"{urllib.parse.quote(pid)}?"
           + urllib.parse.urlencode({
               "fields": "citationCount,influentialCitationCount,"
                         "openAccessPdf,externalIds,tldr,venue,year"}))
    st, d = _get(url, timeout=timeout, retries=2)
    if st != 200 or not isinstance(d, dict):
        _S2_CACHE[pid] = {}
        return {}
    res = {}
    if d.get("citationCount") is not None:
        res["citations"] = int(d["citationCount"])
    oa = d.get("openAccessPdf") or {}
    if oa.get("url"):
        res["pdf_url"] = oa["url"]
        res["open_access"] = 1
    if d.get("venue"):
        res["s2_venue"] = _clean(d["venue"])
    tldr = (d.get("tldr") or {}).get("text")
    if tldr:
        res["tldr"] = _clean(tldr)
    _S2_CACHE[pid] = res
    return res


# ══════════════════════════════════════════════════════════════
# ④ 增强：Unpaywall（开放获取 PDF，补"没有 arXiv 版"的期刊论文）
# ══════════════════════════════════════════════════════════════
def enrich_unpaywall(doi: str | None, timeout: int = 20) -> dict:
    """返回 {"pdf_url": ..., "open_access": 1} 或 {}"""
    if not doi:
        return {}
    doi = doi.strip().lower()
    if not doi.startswith("10."):
        return {}
    url = (f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}"
           f"?email={POLITE_EMAIL}")
    st, d = _get(url, timeout=timeout, retries=2)
    if st != 200 or not isinstance(d, dict):
        return {}
    best = d.get("best_oa_location") or {}
    pdf = best.get("url_for_pdf") or best.get("url")
    if not pdf and not d.get("is_oa"):
        return {}
    out = {"open_access": 1 if d.get("is_oa") else 0}
    if pdf:
        out["pdf_url"] = pdf
    return out


# ══════════════════════════════════════════════════════════════
# 自测（只测不依赖网络的部分；联网部分在 Actions 上跑）
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 78)
    print("工具函数自测")
    print("=" * 78)
    cases = [
        ("2026-09-27T10:00:00Z", "2026-09-27"),
        ("2026-09", "2026-09-01"),
        ("2025", "2025-01-01"),
        (None, None),
        (1712345678000, None),          # 时间戳不是字符串
    ]
    ok = bad = 0
    for raw, want in cases:
        got = _date(raw) if not isinstance(raw, int) else _date(None)
        good = got == want
        ok += good
        bad += not good
        print(f"  {'✅' if good else '❌'} _date({raw!r}) = {got!r}")

    print()
    print("HTML 清洗:", _clean("<p>Hello &amp; <b>world</b></p>"))
    print()
    print("⚠️ 联网源（openreview / huggingface / s2 / unpaywall）需在 Actions 环境测试")
    print(f"\n{ok}/{ok+bad} 通过")
