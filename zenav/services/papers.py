"""论文查询服务 —— 服务层的业务核心。

设计要点：

1. **与传输无关** —— 本模块不知道 MCP / HTTP / CLI 的存在。
   三个入口（MCP 工具、命令行、Python API）调的都是同一套方法，
   ⇒ 修一次 bug 三处都好，行为也绝不会不一致。
2. **无状态** —— 除 catalog 缓存外不持有状态，可安全并发调用。
3. **返回结构化数据** —— 返回 dataclass / dict，不返回"拼好的字符串"。
   格式化是 interfaces 层的事（Agent 要 JSON，人要看表格，需求不同）。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

from zenav.domain.paper import Paper, normalize_title
from zenav.infra.catalog import PaperCatalog
from zenav.log import get_logger

log = get_logger(__name__)

# 允许的排序方式（白名单，防止把任意字符串当 SQL/字段名用）
SORT_FIELDS = ("date", "citations", "hotness", "year", "title")


@dataclass
class SearchResult:
    """一次查询的结果 + 上下文（Agent 需要知道"这是第几页/共多少"）。"""

    papers: list[Paper]
    total_matched: int
    query: dict[str, Any] = field(default_factory=dict)
    catalog_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_matched": self.total_matched,
            "returned": len(self.papers),
            "catalog_total": self.catalog_total,
            "query": self.query,
            "papers": [p.to_brief() for p in self.papers],
        }


class PaperService:
    """论文的查询与整理。"""

    def __init__(self, catalog: PaperCatalog) -> None:
        self._catalog = catalog

    @property
    def catalog(self) -> PaperCatalog:
        """暴露目录（供 Zotero 等服务共享同一份数据，避免重复加载）。"""
        return self._catalog

    # ── 元信息 ──────────────────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        """数据集概况（Agent 的"探索入口"：先看有什么，再决定查什么）。"""
        data = self._catalog.load()
        tiers: dict[str, int] = {}
        kinds: dict[str, int] = {}
        years: dict[int, int] = {}
        for p in data.papers:
            if p.venue_tier:
                tiers[p.venue_tier] = tiers.get(p.venue_tier, 0) + 1
            kinds[p.venue_kind or "unknown"] = kinds.get(p.venue_kind or "unknown", 0) + 1
            if p.year:
                years[p.year] = years.get(p.year, 0) + 1
        return {
            "total": data.total,
            "generated_at": data.generated_at,
            "source": data.source_desc,
            "categories": data.categories,
            "topics": data.topics,
            "venues": data.venues,
            "tiers": dict(sorted(tiers.items())),
            "venue_kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
            "years": dict(sorted(years.items(), reverse=True)),
            "with_code": sum(1 for p in data.papers if p.code_url),
            "with_doi": sum(1 for p in data.papers if p.doi),
            "with_arxiv": sum(1 for p in data.papers if p.arxiv_id),
            "open_access": sum(1 for p in data.papers if p.open_access),
        }

    def categories(self) -> dict[str, int]:
        """全部分类 + 各自论文数。"""
        return dict(self._catalog.load().categories)

    def topics(self, limit: int = 0) -> dict[str, int]:
        """全部子专题标签 + 计数。"""
        got = self._catalog.load().topics
        items = list(got.items())
        return dict(items[:limit] if limit > 0 else items)

    # ── 查询 ────────────────────────────────────────────────────────
    def search(
        self,
        query: str = "",
        *,
        category: str = "",
        topic: str = "",
        venue: str = "",
        year_from: int = 0,
        year_to: int = 0,
        has_code: bool = False,
        open_access: bool = False,
        min_citations: int = 0,
        sort: str = "date",
        limit: int = 20,
        offset: int = 0,
    ) -> SearchResult:
        """多条件检索。所有条件为 AND；`query` 内多个词也是 AND。

        Args:
            query: 关键词（空格分隔，全部命中才算）。匹配标题/摘要/作者/会议。
            sort: date（默认，新→旧）| citations | hotness | year | title
            limit: 返回条数上限（0 = 不限，⚠️ Agent 场景慎用，会撑爆上下文）
            offset: 分页偏移
        """
        if sort not in SORT_FIELDS:
            sort = "date"
        data = self._catalog.load()

        # 预处理关键词（避免每条记录都 split 一次）
        terms = [t for t in (query or "").lower().split() if t]

        def match(p: Paper) -> bool:
            if category and p.category != category:
                # 宽容匹配：允许写 "ObjectNav" 命中 "Object-Goal Navigation (ObjectNav)"
                if category.lower() not in p.category.lower():
                    return False
            if topic and topic not in p.topics:
                # 同样宽容：大小写不敏感
                if not any(topic.lower() == t.lower() for t in p.topics):
                    return False
            if venue and venue.lower() not in p.venue_short.lower():
                return False
            if year_from and p.year < year_from:
                return False
            if year_to and p.year > year_to:
                return False
            if has_code and not p.code_url:
                return False
            if open_access and not p.open_access:
                return False
            if min_citations and p.citations < min_citations:
                return False
            if terms:
                haystack = p.to_search_text()
                if not all(t in haystack for t in terms):
                    return False
            return True

        hits = [p for p in data.papers if match(p)]
        hits = self._sort(hits, sort)

        total_matched = len(hits)
        if offset:
            hits = hits[offset:]
        if limit and limit > 0:
            hits = hits[:limit]

        return SearchResult(
            papers=hits,
            total_matched=total_matched,
            query={
                "query": query, "category": category, "topic": topic,
                "venue": venue, "year_from": year_from, "year_to": year_to,
                "has_code": has_code, "open_access": open_access,
                "min_citations": min_citations, "sort": sort,
                "limit": limit, "offset": offset,
            },
            catalog_total=data.total,
        )

    def get(self, identifier: str) -> Paper | None:
        """按标识取单篇。支持：DOI、arXiv ID、完整标题、标题片段。

        匹配优先级：精确 DOI → 精确 arXiv → 标题完全一致（归一化后）
        → 标题包含。找不到返回 None（调用方决定怎么报错）。
        """
        if not identifier or not identifier.strip():
            return None
        needle = identifier.strip()
        low = needle.lower()
        # 允许用户粘贴完整 DOI URL
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if low.startswith(prefix):
                low = low[len(prefix):]
                break

        data = self._catalog.load()
        norm_needle = normalize_title(needle)

        # 1) 精确标识符
        for p in data.papers:
            if p.doi and p.doi.lower() == low:
                return p
            if p.arxiv_id and p.arxiv_id.lower() == low:
                return p
        # 2) 标题完全一致
        for p in data.papers:
            if normalize_title(p.title) == norm_needle:
                return p
        # 3) 标题包含（取最贴近的：标题最短的那个）
        candidates = [p for p in data.papers
                      if norm_needle and norm_needle in normalize_title(p.title)]
        if candidates:
            return min(candidates, key=lambda p: len(p.title))
        return None

    def latest(
        self,
        days: int = 30,
        *,
        limit: int = 20,
        category: str = "",
    ) -> SearchResult:
        """最近 N 天发表的论文（按发表日，与网站口径一致）。

        ⚠️ 口径说明：本项目一律以**发表/上线日期**为准（Boss 明确要求），
        不用"首次采集时间"——否则每周采集会把一堆老论文算成"新"。
        """
        cutoff = (_dt.date.today() - _dt.timedelta(days=max(1, days))).isoformat()
        data = self._catalog.load()
        hits = [
            p for p in data.papers
            if p.date and p.date[:10] >= cutoff
            and (not category or category.lower() in p.category.lower())
        ]
        hits = self._sort(hits, "date")
        total = len(hits)
        if limit > 0:
            hits = hits[:limit]
        return SearchResult(
            papers=hits,
            total_matched=total,
            query={"days": days, "limit": limit, "category": category,
                   "cutoff": cutoff},
            catalog_total=data.total,
        )

    # ── 整理：分类总览 ──────────────────────────────────────────────
    def category_overview(
        self,
        category: str,
        *,
        group_by: str = "year",
        limit_per_group: int = 0,
    ) -> dict[str, Any]:
        """⭐ 一个分类的"总览"（Boss 明确要的东西）。

        返回结构化数据；渲染成 Markdown 由 `render_overview_markdown` 负责，
        这样 MCP / 网站 / 命令行可以各自决定怎么展示。
        """
        data = self._catalog.load()
        members = [p for p in data.papers
                   if category.lower() in p.category.lower()]
        if not members:
            return {"category": category, "found": False, "total": 0, "groups": []}
        members = self._sort(members, "date")

        if group_by not in ("year", "venue", "topic"):
            group_by = "year"

        buckets: dict[str, list[Paper]] = {}
        for p in members:
            if group_by == "year":
                key = str(p.year or "未知年份")
            elif group_by == "venue":
                key = p.venue_display()
            else:
                key = p.topics[0] if p.topics else "未标注"
            buckets.setdefault(key, []).append(p)

        if group_by == "year":
            keys = sorted(buckets, key=lambda k: (not k.isdigit(), k), reverse=True)
        else:
            keys = sorted(buckets, key=lambda k: -len(buckets[k]))

        groups = []
        for k in keys:
            items = buckets[k]
            if limit_per_group > 0:
                items = items[:limit_per_group]
            groups.append({
                "group": k,
                "count": len(buckets[k]),
                "papers": [p.to_brief() for p in items],
            })

        tier_a = sum(1 for p in members if p.venue_tier == "A")
        return {
            "category": category,
            "found": True,
            "total": len(members),
            "tier_a_count": tier_a,
            "with_code": sum(1 for p in members if p.code_url),
            "newest_date": max((p.date for p in members if p.date), default=""),
            "group_by": group_by,
            "groups": groups,
        }

    def all_overviews(self, *, limit_per_group: int = 3) -> list[dict[str, Any]]:
        """所有分类的概览（网站/摘要页用）。"""
        return [
            self.category_overview(c, limit_per_group=limit_per_group)
            for c in self.categories()
        ]

    # ── 内部 ────────────────────────────────────────────────────────
    @staticmethod
    def _sort(papers: list[Paper], sort: str) -> list[Paper]:
        if sort == "citations":
            return sorted(papers, key=lambda p: (-p.citations, -p.year))
        if sort == "hotness":
            return sorted(papers, key=lambda p: (-p.hotness, -p.year))
        if sort == "year":
            return sorted(papers, key=lambda p: (-p.year, p.date))
        if sort == "title":
            return sorted(papers, key=lambda p: p.title.lower())
        # date：新→旧；日期缺失的排最后（而不是排最前，否则脏数据会占满首页）
        return sorted(papers, key=lambda p: (p.date or "0000-00-00"), reverse=True)


def render_overview_markdown(overview: dict[str, Any],
                             max_rows: int = 0) -> str:
    """把 `category_overview()` 的结果渲染成 Markdown 总览表。

    与数据分开的原因：同一份数据要给三种消费者 ——
    Agent（要 JSON）、人（要表格）、网站（要 HTML）。
    把渲染抽出来，就不必为了"给网站用"再去数据层加参数。
    """
    if not overview.get("found"):
        return f"# {overview.get('category', '')}\n\n（该分类下暂无论文）"

    lines = [
        f"# {overview['category']}",
        "",
        f"**共 {overview['total']} 篇**"
        + (f" ｜ A 级会议 {overview['tier_a_count']} 篇" if overview.get("tier_a_count") else "")
        + (f" ｜ 有开源代码 {overview['with_code']} 篇" if overview.get("with_code") else "")
        + (f" ｜ 最新 {overview['newest_date']}" if overview.get("newest_date") else ""),
        "",
    ]
    shown = 0
    for g in overview.get("groups", []):
        lines.append(f"## {g['group']}（{g['count']} 篇）")
        lines.append("")
        lines.append("| 年份 | 会议/期刊 | 标题 | 作者 | 链接 |")
        lines.append("|------|-----------|------|------|------|")
        for p in g["papers"]:
            if max_rows and shown >= max_rows:
                break
            lines.append(_row(p))
            shown += 1
        if max_rows and shown >= max_rows:
            lines.append(f"\n> （已截断，共 {overview['total']} 篇）")
            break
        lines.append("")
    return "\n".join(lines)


def _row(p: dict[str, Any]) -> str:
    """把 Paper.to_brief() 的 dict 渲染成一行表格。"""
    links = []
    if p.get("pdf"):
        links.append(f"[PDF]({p['pdf']})")
    elif p.get("link"):
        links.append(f"[页]({p['link']})")
    if p.get("code"):
        links.append(f"[代码]({p['code']})")
    if p.get("project"):
        links.append(f"[项目]({p['project']})")
    link_str = " · ".join(links) if links else "—"
    title = str(p.get("title", "")).replace("|", "\\|")
    return (f"| {p.get('year') or '—'} | {p.get('venue') or '—'} | {title} "
            f"| {p.get('authors') or '—'} | {link_str} |")
