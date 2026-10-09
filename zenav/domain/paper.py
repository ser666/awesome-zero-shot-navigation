"""领域模型 —— 论文（Paper）。

职责边界（重要）：

    ✅ 该在这里：字段、派生属性、指代自己的序列化（to_dict/from_record）
    ❌ 不该在这里：IO（读文件/发 HTTP）、外部格式渲染（BibTeX/Zotero item）
       —— 那些属于 infra / services 层。领域模型要知道"什么是论文"，
          不需要知道"Zotero 的 JSON 长什么样"。

**短键 vs 长键**：`docs/data/papers.json` 为省体积用了短键（t/a/d/y/...）。
本模块是**唯一**负责这两种表示互转的地方 —— 别在别处再写一遍映射表，
否则加字段时必然漏改（历史上就吃过这个亏）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# ── 短键 → 长键（唯一事实来源，改这里就够） ────────────────────────────
SHORT_TO_LONG: dict[str, str] = {
    "t": "title",
    "a": "authors",
    "d": "date",
    "y": "year",
    "v": "venue_short",
    "vk": "venue_kind",
    "vt": "venue_tier",
    "vf": "venue_full",
    "c": "category",
    "tp": "topics",
    "u": "url",
    "p": "pdf_url",
    "co": "code_url",
    "pr": "project_url",
    "doi": "doi",
    "arx": "arxiv_id",
    "cite": "citations",
    "oa": "open_access",
    "nw": "is_new",
    "hot": "hotness",
    "s": "abstract",
    "tl": "tldr",
    "src": "sources",
}
LONG_TO_SHORT: dict[str, str] = {v: k for k, v in SHORT_TO_LONG.items()}

_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def normalize_title(title: str) -> str:
    """标题归一化（用于去重与幂等指纹）。

    做法：小写 → 所有非字母数字（保留中文）压成单空格 → 去首尾。
    为什么不用简单的 `.lower().strip()`：标点/连字符/多空格的差异
    会让同一篇论文产生两个指纹，导致 Zotero 里出现重复条目。
    """
    return _PUNCT_RE.sub(" ", (title or "").lower()).strip()


@dataclass(frozen=True)
class Paper:
    """一篇论文。

    `frozen=True` 是有意的：论文是"从数据源读到的事实"，
    运行期不该被就地改写。需要变体时用 `dataclasses.replace()` 生成新对象。
    """

    title: str
    authors: tuple[str, ...] = ()
    date: str = ""
    year: int = 0
    venue_short: str = ""
    venue_kind: str = ""
    venue_tier: str = ""
    venue_full: str = ""
    category: str = "Other"
    topics: tuple[str, ...] = ()
    url: str = ""
    pdf_url: str = ""
    code_url: str = ""
    project_url: str = ""
    doi: str = ""
    arxiv_id: str = ""
    citations: int = 0
    open_access: bool = False
    # ⭐ 「新论文」标记 —— 由导出侧按 NEW_DAYS 判据算好（见 scripts/export.py）。
    #    服务层不再重复实现一遍判据，避免两套定义漂移。
    is_new: bool = False
    hotness: int = 0
    abstract: str = ""
    tldr: str = ""
    sources: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # ── 构造 ────────────────────────────────────────────────────────
    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> Paper:
        """从 papers.json 的**短键记录**构造；也接受已经是长键的 dict。

        宽容处理未知字段：存进 `raw`，这样将来采集层加字段时，
        服务层不用同步改（向后兼容）。
        """
        if not isinstance(rec, dict):
            raise TypeError(f"论文记录必须是 dict，收到 {type(rec).__name__}")

        def take(key: str, default: Any) -> Any:
            # 先按长键找，再按短键找（两种输入都支持）
            if key in rec:
                return rec[key]
            short = LONG_TO_SHORT.get(key)
            if short and short in rec:
                return rec[short]
            return default

        authors = take("authors", []) or []
        topics = take("topics", []) or []
        sources = take("sources", []) or []

        return cls(
            title=str(take("title", "") or "").strip(),
            authors=tuple(str(a) for a in authors if a),
            date=str(take("date", "") or ""),
            year=int(take("year", 0) or 0),
            venue_short=str(take("venue_short", "") or ""),
            venue_kind=str(take("venue_kind", "") or ""),
            venue_tier=str(take("venue_tier", "") or ""),
            venue_full=str(take("venue_full", "") or ""),
            category=str(take("category", "Other") or "Other"),
            topics=tuple(str(t) for t in topics if t),
            url=str(take("url", "") or ""),
            pdf_url=str(take("pdf_url", "") or ""),
            code_url=str(take("code_url", "") or ""),
            project_url=str(take("project_url", "") or ""),
            doi=str(take("doi", "") or ""),
            arxiv_id=str(take("arxiv_id", "") or ""),
            citations=int(take("citations", 0) or 0),
            open_access=bool(take("open_access", False)),
            is_new=bool(take("is_new", False)),
            hotness=int(take("hotness", 0) or 0),
            abstract=str(take("abstract", "") or ""),
            tldr=str(take("tldr", "") or ""),
            sources=tuple(str(s) for s in sources if s),
            raw={k: v for k, v in rec.items()
                 if k not in SHORT_TO_LONG and k not in LONG_TO_SHORT},
        )

    # ── 派生属性 ────────────────────────────────────────────────────
    @property
    def is_preprint(self) -> bool:
        return self.venue_kind in ("preprint", "unknown", "")

    @property
    def is_published(self) -> bool:
        """是否已正式发表（有会议/期刊）。⚠️ 投稿中/撤稿不算。"""
        return self.venue_kind in ("conference", "proceedings", "journal")

    @property
    def best_link(self) -> str:
        """最该给人点的链接：PDF > 正式页 > DOI。"""
        return self.pdf_url or self.url or (
            f"https://doi.org/{self.doi}" if self.doi else ""
        )

    @property
    def best_identifier(self) -> str:
        """人类可读的标识：DOI > arXiv > 短标题。"""
        if self.doi:
            return f"DOI:{self.doi}"
        if self.arxiv_id:
            return f"arXiv:{self.arxiv_id}"
        return self.title[:60]

    def fingerprint(self) -> str:
        """幂等指纹 —— 跨运行稳定，用于"这篇推过没有"。

        优先级：DOI > arXiv > 标题指纹。
        实测数据分布：696 篇有 DOI、418 篇有 arXiv、54 篇两者都无
        ⇒ 第三种分支是**必需**的，不是防御性代码。
        """
        if self.doi:
            return f"doi:{self.doi.strip().lower()}"
        if self.arxiv_id:
            return f"arx:{self.arxiv_id.strip().lower()}"
        digest = hashlib.sha1(
            normalize_title(self.title).encode("utf-8")
        ).hexdigest()[:16]
        return f"ttl:{digest}"

    def authors_display(self, limit: int = 3) -> str:
        """作者展示：超出 limit 用 et al.（与 README 的风格一致）。"""
        if not self.authors:
            return "—"
        if len(self.authors) <= limit:
            return ", ".join(self.authors)
        return ", ".join(self.authors[:limit]) + " et al."

    def venue_display(self) -> str:
        """会议/期刊展示：`CoRL 2025 [A]`，没发表就标 Preprint。"""
        if not self.venue_short:
            return "Preprint" if self.is_preprint else "—"
        tier = f" [{self.venue_tier}]" if self.venue_tier else ""
        year = f" {self.year}" if self.year else ""
        return f"{self.venue_short}{year}{tier}"

    def mla_cite(self) -> str:
        """一行引用（用于总览表/摘要输出，不追求严格 MLA 合规）。"""
        return (
            f"{self.authors_display()} ({self.year or 'n.d.'}). "
            f"{self.title}. {self.venue_short or 'Preprint'}. "
            f"{self.best_link}"
        )

    def to_dict(self, *, short_keys: bool = False) -> dict[str, Any]:
        """序列化。`short_keys=True` 时输出与 papers.json 一致的短键格式。"""
        data: dict[str, Any] = {
            "title": self.title,
            "authors": list(self.authors),
            "date": self.date,
            "year": self.year,
            "venue_short": self.venue_short,
            "venue_kind": self.venue_kind,
            "venue_tier": self.venue_tier,
            "venue_full": self.venue_full,
            "category": self.category,
            "topics": list(self.topics),
            "url": self.url,
            "pdf_url": self.pdf_url,
            "code_url": self.code_url,
            "project_url": self.project_url,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "citations": self.citations,
            "open_access": self.open_access,
            "is_new": self.is_new,
            "hotness": self.hotness,
            "abstract": self.abstract,
            "tldr": self.tldr,
            "sources": list(self.sources),
        }
        if not short_keys:
            return data
        return {LONG_TO_SHORT[k]: v for k, v in data.items()}

    def to_brief(self) -> dict[str, Any]:
        """精简表示（Agent 上下文友好）—— 不含摘要正文。

        ⚠️ 为什么给 Agent 默认不带摘要：756 篇 × 1800 字会把上下文撑爆。
        需要摘要时走 `get_paper` 单篇取。
        """
        return {
            "title": self.title,
            "authors": self.authors_display(),
            "year": self.year,
            "date": self.date,
            "venue": self.venue_display(),
            "category": self.category,
            "topics": list(self.topics),
            "link": self.best_link,
            "pdf": self.pdf_url,
            "code": self.code_url,
            "project": self.project_url,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "citations": self.citations,
            "open_access": self.open_access,
            "is_new": self.is_new,
            "identifier": self.best_identifier,
        }

    def to_markdown_row(self) -> str:
        """总览表的一行（Markdown）。链接用短标签，便于在表格里阅读。"""
        links = []
        if self.best_link:
            links.append(f"[PDF]({self.best_link})" if self.pdf_url else f"[页]({self.best_link})")
        if self.code_url:
            links.append(f"[代码]({self.code_url})")
        if self.project_url:
            links.append(f"[项目]({self.project_url})")
        link_str = " · ".join(links) if links else "—"
        return (
            f"| {self.year or '—'} | {self.venue_display()} | {self.title} "
            f"| {self.authors_display()} | {link_str} |"
        )

    def to_search_text(self) -> str:
        """用于关键词检索的合并文本（一次预处理，多次匹配）。"""
        return " ".join((
            self.title, self.tldr, self.abstract,
            " ".join(self.authors), self.venue_short, self.venue_full,
            self.category, " ".join(self.topics), self.doi, self.arxiv_id,
        )).lower()
