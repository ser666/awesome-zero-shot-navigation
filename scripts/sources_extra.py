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
# ① OpenReview —— 会议投稿平台（ICLR / NeurIPS / CoRL / CVPR 等）
#    价值：能拿到**官方接收结果**（"accepted"），比"arXiv 预印本"信息更硬
# ══════════════════════════════════════════════════════════════
def harvest_openreview(query: str, limit: int = 60,
                       date_from: str | None = None) -> list:
    """搜索 OpenReview 的 submissions。

    ⚠️ OpenReview 的 content 字段是嵌套结构 {"value": ...}，
       且不同会议版本不一致，取值要防御式。
    """
    url = ("https://api2.openreview.net/notes/search?"
           + urllib.parse.urlencode({"term": query, "limit": min(limit, 100),
                                     "type": "terms",
                                     "content": "all",
                                     "group": "all",
                                     "source": "all"}))
    st, d = _get(url, timeout=40)
    if st != 200 or not isinstance(d, dict):
        return []

    out = []
    for n in d.get("notes", []):
        c = n.get("content") or {}

        def v(key, default=""):
            x = c.get(key)
            if isinstance(x, dict):
                return x.get("value", default)
            return x if x is not None else default

        title = _clean(v("title"))
        if not title:
            continue

        # 日期：OpenReview 用 cdate / tcdate（毫秒时间戳）
        ts = n.get("cdate") or n.get("tcdate") or n.get("mdate")
        pd = None
        if ts:
            try:
                pd = time.strftime("%Y-%m-%d", time.gmtime(int(ts) / 1000))
            except Exception:  # noqa: BLE001
                pd = None
        if date_from and pd and pd < date_from:
            continue

        abstract = _clean(v("abstract"))
        venue_raw = _clean(v("venue") or v("venueid") or "")
        # 从 venue 里判断是否已被接收
        vlow = venue_raw.lower()
        if any(k in vlow for k in ("accept", "oral", "spotlight", "poster",
                                   "withdraw", "reject")):
            # OpenReview 的 venue 形如 "ICLR 2026 poster" / "NeurIPS 2025 oral"
            venue = venue_raw
        else:
            venue = "OpenReview"

        forum = n.get("forum") or n.get("id")
        out.append({
            "title": title,
            "authors": [a for a in (v("authors") or []) if isinstance(a, str)],
            "year": int(pd[:4]) if pd else None,
            "published_date": pd,
            "venue": venue,
            "abstract": abstract,
            "doi": None,
            "arxiv_id": None,
            "citations": 0,
            "url": f"https://openreview.net/forum?id={forum}" if forum else None,
            "pdf_url": f"https://openreview.net/pdf?id={forum}" if forum else None,
            "open_access": 1,
            "source": "openreview",
        })
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
