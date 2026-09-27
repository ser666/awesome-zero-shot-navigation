#!/usr/bin/env python3
"""数据源探测 —— 在【运行环境所在网络】实测哪些学术源可用。

为什么要这个脚本：
  中国大陆本机访问不了 arXiv / DBLP；但 GitHub Actions 跑在海外。
  所以"能不能用某个源"必须在 **Actions 环境里实测**，不能靠本机推断。

用法：
    python3 scripts/probe_sources.py            # 探测全部
    python3 scripts/probe_sources.py arxiv      # 只测某个
"""

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
HDR = {"User-Agent": UA, "Accept": "application/json, text/xml, */*"}


def _get(url: str, timeout: int = 25, hdr: dict | None = None) -> tuple[int, str, float]:
    t0 = time.time()
    req = urllib.request.Request(url, headers=hdr or HDR)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(200000).decode("utf-8", "ignore")
        return r.status, body, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(4000).decode("utf-8", "ignore"), time.time() - t0
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}", time.time() - t0


def tcp(host: str, port: int, timeout: int = 8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


# ═══════════════════════════════════════════════════════════════
# 各源探测函数：返回 (是否可用, 说明, 样本信息)
# ═══════════════════════════════════════════════════════════════

def probe_arxiv():
    """arXiv 官方 API —— 最新论文的主力源（含 comment 字段，常含 project/code 链接）"""
    url = ("http://export.arxiv.org/api/query?"
           + urllib.parse.urlencode({
               "search_query": 'all:"zero-shot navigation"',
               "start": 0, "max_results": 3,
               "sortBy": "submittedDate", "sortOrder": "descending"}))
    st, body, dt = _get(url)
    if st != 200:
        return False, f"HTTP {st}: {body[:120]}", None
    import re
    entries = re.findall(r"<entry>(.*?)</entry>", body, re.S)
    if entries:
        m = re.search(r"<title>(.*?)</title>", entries[0], re.S)
        title = m.group(1).strip() if m else "?"
    else:
        title = "?"
    # 检查 comment / journal_ref 字段是否存在（这两字段对"补链接/补会议"很关键）
    has_comment = "<arxiv:comment>" in body
    has_journal = "<arxiv:journal_ref>" in body
    return True, (f"{dt:.1f}s ｜ {len(entries)} 条 ｜ comment字段={has_comment} "
                  f"journal_ref={has_journal}"), title[:80]


def probe_semanticscholar():
    """Semantic Scholar —— 引用数 / openAccessPdf（无 Key 配额低）"""
    url = ("https://api.semanticscholar.org/graph/v1/paper/search?"
           + urllib.parse.urlencode({
               "query": "zero-shot navigation", "limit": 3,
               "fields": "title,year,citationCount,externalIds,openAccessPdf"}))
    st, body, dt = _get(url)
    if st != 200:
        return False, f"HTTP {st}: {body[:120]}", None
    try:
        d = json.loads(body)
        n = len(d.get("data", []))
        return True, f"{dt:.1f}s ｜ {n} 条", (d["data"][0]["title"][:70] if n else "?")
    except Exception as e:  # noqa: BLE001
        return False, f"解析失败 {e}", None


def probe_dblp():
    """DBLP —— 权威会议/期刊名（补 venue 最准）"""
    url = ("https://dblp.org/search/publ/api?"
           + urllib.parse.urlencode({"q": "zero-shot navigation", "format": "json",
                                     "h": 3}))
    st, body, dt = _get(url)
    if st != 200:
        return False, f"HTTP {st}: {body[:120]}", None
    if "anubis" in body.lower() or "<html" in body[:200].lower():
        return False, "被 Anubis/HTML 挑战页拦截", None
    try:
        d = json.loads(body)
        hits = d["result"]["hits"].get("hit", [])
        v = hits[0]["info"].get("venue") if hits else "?"
        return True, f"{dt:.1f}s ｜ {len(hits)} 条", f"venue={v}"
    except Exception as e:  # noqa: BLE001
        return False, f"解析失败 {e}", None


def probe_openreview():
    """OpenReview —— 会议投稿（ICLR/NeurIPS/CoRL 等，可拿官方 decision）"""
    url = ("https://api2.openreview.net/notes/search?"
           + urllib.parse.urlencode({"term": "zero-shot navigation", "limit": 3}))
    st, body, dt = _get(url)
    if st != 200:
        return False, f"HTTP {st}: {body[:120]}", None
    try:
        d = json.loads(body)
        n = len(d.get("notes", []))
        ttl = d["notes"][0]["content"].get("title", {}).get("value", "?") if n else "?"
        return True, f"{dt:.1f}s ｜ {n} 条", str(ttl)[:70]
    except Exception as e:  # noqa: BLE001
        return False, f"解析失败 {e}", None


def probe_huggingface():
    """Hugging Face Daily Papers —— 社区热度（upvotes），可做"热门"排序"""
    url = "https://huggingface.co/api/daily_papers?limit=3"
    st, body, dt = _get(url)
    if st != 200:
        return False, f"HTTP {st}: {body[:120]}", None
    try:
        d = json.loads(body)
        n = len(d)
        t = d[0].get("paper", {}).get("title", "?") if n else "?"
        return True, f"{dt:.1f}s ｜ {n} 条", str(t)[:70]
    except Exception as e:  # noqa: BLE001
        return False, f"解析失败 {e}", None


def probe_paperswithcode():
    """Papers with Code —— 代码仓库链接（注意：站点 2025 年已关停，故只需确认）"""
    st, body, dt = _get("https://paperswithcode.com/api/v1/papers/?items_per_page=2")
    if st in (200, 301, 302):
        return True, f"{dt:.1f}s ｜ HTTP {st}", body[:100]
    return False, f"HTTP {st}（可能已关停）", None


def probe_unpaywall():
    """Unpaywall —— 开放获取 PDF 地址（需 email 参数，免费）"""
    url = ("https://api.unpaywall.org/v2/10.1109/LRA.2024.3357317"
           "?email=1035534180@qq.com")
    st, body, dt = _get(url)
    if st != 200:
        return False, f"HTTP {st}: {body[:120]}", None
    try:
        d = json.loads(body)
        return True, f"{dt:.1f}s", f"is_oa={d.get('is_oa')}"
    except Exception as e:  # noqa: BLE001
        return False, f"解析失败 {e}", None


def probe_crossref():
    """Crossref（已在用，作为基线对比）"""
    url = ("https://api.crossref.org/works?"
           + urllib.parse.urlencode({"query.bibliographic": "zero-shot navigation",
                                     "rows": 2, "mailto": "1035534180@qq.com"}))
    st, body, dt = _get(url)
    if st != 200:
        return False, f"HTTP {st}", None
    try:
        d = json.loads(body)
        n = len(d["message"]["items"])
        return True, f"{dt:.1f}s ｜ {n} 条", "基线源"
    except Exception as e:  # noqa: BLE001
        return False, f"解析失败 {e}", None


def probe_openalex():
    """OpenAlex（已在用，基线）"""
    url = ("https://api.openalex.org/works?"
           + urllib.parse.urlencode({"search": "zero-shot navigation",
                                     "per-page": 2, "mailto": "1035534180@qq.com"}))
    st, body, dt = _get(url)
    if st != 200:
        return False, f"HTTP {st}", None
    try:
        d = json.loads(body)
        return True, f"{dt:.1f}s ｜ {len(d.get('results', []))} 条", "基线源"
    except Exception as e:  # noqa: BLE001
        return False, f"解析失败 {e}", None


def probe_semantic_scholar_alt():
    """Semantic Scholar 批量端点（补 citations）"""
    st, body, dt = _get("https://api.semanticscholar.org/graph/v1/paper/"
                        "ARXIV:2409.05815?fields=title,citationCount,externalIds")
    if st != 200:
        return False, f"HTTP {st}: {body[:100]}", None
    try:
        d = json.loads(body)
        return True, f"{dt:.1f}s", f"cites={d.get('citationCount')}"
    except Exception as e:  # noqa: BLE001
        return False, f"解析失败 {e}", None


def probe_github():
    """GitHub API（找代码仓库 / 竞品调研）"""
    st, body, dt = _get("https://api.github.com/search/repositories?"
                        "q=zero-shot+navigation&per_page=2")
    if st != 200:
        return False, f"HTTP {st}", None
    try:
        d = json.loads(body)
        return True, f"{dt:.1f}s ｜ {d.get('total_count')} 结果", "可用于找代码"
    except Exception as e:  # noqa: BLE001
        return False, f"解析失败 {e}", None


PROBES = [
    ("arxiv", probe_arxiv),
    ("semanticscholar", probe_semanticscholar),
    ("semanticscholar-alt", probe_semantic_scholar_alt),
    ("dblp", probe_dblp),
    ("openreview", probe_openreview),
    ("huggingface", probe_huggingface),
    ("paperswithcode", probe_paperswithcode),
    ("unpaywall", probe_unpaywall),
    ("crossref", probe_crossref),
    ("openalex", probe_openalex),
    ("github", probe_github),
]


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None

    print("=" * 84)
    print("环境信息")
    print("=" * 84)
    import platform
    print(f"  Python: {platform.python_version()} ｜ {platform.system()}")
    print(f"  CI 环境: {'是（GitHub Actions）' if __import__('os').environ.get('CI') else '否（本机）'}")
    print(f"  GITHUB_ACTIONS={__import__('os').environ.get('GITHUB_ACTIONS', '—')}")
    print()
    print("  TCP 连通性:")
    for host, port in [("export.arxiv.org", 80), ("arxiv.org", 443),
                       ("api.semanticscholar.org", 443), ("dblp.org", 443),
                       ("api.openreview.net", 443), ("huggingface.co", 443),
                       ("api.crossref.org", 443), ("api.openalex.org", 443),
                       ("github.com", 443)]:
        print(f"    {host}:{port} → {'✅' if tcp(host, port) else '❌'}")

    print()
    print("=" * 84)
    print("源可用性（真实请求）")
    print("=" * 84)
    results = {}
    for name, fn in PROBES:
        if only and name != only:
            continue
        try:
            ok, note, sample = fn()
        except Exception as e:  # noqa: BLE001
            ok, note, sample = False, f"异常 {e}", None
        results[name] = ok
        mark = "✅ 可用" if ok else "❌ 不可用"
        print(f"  {mark:9} {name:22} {note}")
        if sample:
            print(f"            └─ {sample}")

    print()
    print("=" * 84)
    print(f"汇总：{sum(1 for v in results.values() if v)}/{len(results)} 个源可用")
    print("=" * 84)


if __name__ == "__main__":
    main()
