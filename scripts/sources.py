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

# ── 检索关键词（每条都是 OpenAlex/Crossref 的查询串）
QUERIES = [
    "zero-shot navigation",
    "zero-shot object navigation",
    "zero-shot object goal navigation",
    "zero-shot vision-and-language navigation",
    "zero-shot embodied navigation",
    "training-free navigation",
    "open-vocabulary object navigation",
    "zero-shot semantic navigation",
    "vision-language navigation foundation model",
    "embodied navigation foundation model",
    "zero-shot aerial navigation",
    "LLM based navigation",
    "VLM navigation robot",
    "instruction following navigation",
    "goal navigation unseen object",
]


def _get_json(url: str, timeout: int = 45, retries: int = 4):
    """带退避重试的 GET。429（限速）单独处理，等待更久。"""
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
                wait = 8 * (i + 1)  # 限速：等更久
            elif e.code in (500, 502, 503, 504):
                wait = 4 * (i + 1)
            else:
                wait = 2 * (i + 1)
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
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
                     date_to: str | None = None) -> list:
    """按标题/摘要检索，按发表日期倒序"""
    params = {
        "per-page": min(per_page, 200),
        "sort": "publication_date:desc",
        "mailto": POLITE_EMAIL,
    }
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
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
