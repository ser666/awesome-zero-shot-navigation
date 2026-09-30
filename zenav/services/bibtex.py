"""BibTeX 导出。

为什么需要它（虽然主路径是 Zotero API 直推）：

1. **兜底通道** —— API Key 没配 / 想手动导入 / 换到别的文献管理器（Mendeley、
   JabRef、Papers）时，BibTeX 是通用货币。
2. **可复制** —— Agent 可以直接把 BibTeX 文本贴进 LaTeX 论文里。
3. **可在没有凭据的环境用** —— 比如别人的机器上。

⚠️ 生成的是"能编译的 BibTeX"，不是"格式最完美的 BibTeX"：
真正的排版规范（缩写期刊名、姓首字母等）交给 LaTeX 宏包/文献管理器处理，
在这里过度加工反而会引入难查的转义 bug。
"""

from __future__ import annotations

import re

from zenav.domain.paper import Paper

# BibTeX 里有特殊含义的字符：必须转义，否则编译报错或静默丢字
_BIB_ESCAPE = {
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
}
_ESCAPE_RE = re.compile("|".join(re.escape(k) for k in _BIB_ESCAPE))
_NON_KEY_RE = re.compile(r"[^A-Za-z0-9]+")


def escape_bibtex(text: str) -> str:
    """转义 BibTeX 特殊字符。"""
    return _ESCAPE_RE.sub(lambda m: _BIB_ESCAPE[m.group(0)], text or "")


def entry_type(paper: Paper) -> str:
    """按发表形态选条目类型。

    ⚠️ 这里与 `ZoteroConfig.default_item_type` 是**不同**的取值域：
    BibTeX 只有 inproceedings/article/misc 这类；Zotero 有 conferencePaper 等。
    两者别互相套用。
    """
    kind = (paper.venue_kind or "").lower()
    if kind == "journal":
        return "article"
    if kind in ("conference", "proceedings"):
        return "inproceedings"
    return "misc"


def citation_key(paper: Paper, used: set[str] | None = None) -> str:
    """生成唯一 cite key：`姓 + 年 + 标题首实词`。

    为什么不用简单计数（paper1/paper2）：跨运行不稳定，
    重新导出一次就和论文里的 \\cite{} 对不上了。
    """
    if paper.authors:
        first = paper.authors[0]
        # "He, Yu" → "He"；"Yu He" → "He"
        surname = first.split(",")[0].strip() if "," in first else first.split()[-1]
    else:
        surname = "anon"
    surname = _NON_KEY_RE.sub("", surname) or "anon"

    year = str(paper.year or "nd")

    stop = {"a", "an", "the", "on", "of", "for", "and", "to", "in", "with",
            "towards", "toward", "via"}
    words = [w for w in _NON_KEY_RE.split(paper.title.lower()) if w]
    head = next((w for w in words if w not in stop and len(w) > 2), "nav")
    base = f"{surname}{year}{head.capitalize()}"

    if used is None:
        return base
    key, n = base, 1
    while key in used:
        n += 1
        key = f"{base}{chr(ord('a') + n - 1)}"
    used.add(key)
    return key


def to_bibtex_entry(paper: Paper, key: str | None = None) -> str:
    """单篇 → BibTeX 条目文本。"""
    etype = entry_type(paper)
    cite = key or citation_key(paper)
    fields: list[tuple[str, str]] = [("title", escape_bibtex(paper.title))]

    if paper.authors:
        # BibTeX 的 author 字段用 " and " 分隔
        fields.append(("author", " and ".join(escape_bibtex(a) for a in paper.authors)))
    if paper.year:
        fields.append(("year", str(paper.year)))
    if paper.date:
        fields.append(("date", paper.date))

    # 会议/期刊名放对字段，否则 BibTeX 样式会显示不出来
    if etype == "inproceedings":
        fields.append(("booktitle", escape_bibtex(paper.venue_full or paper.venue_short)))
    elif etype == "article":
        fields.append(("journal", escape_bibtex(paper.venue_full or paper.venue_short)))
    elif paper.venue_short:
        fields.append(("howpublished", escape_bibtex(paper.venue_short)))

    if paper.doi:
        fields.append(("doi", paper.doi))
    if paper.arxiv_id:
        fields.append(("eprint", paper.arxiv_id))
        fields.append(("archivePrefix", "arXiv"))
    if paper.url:
        fields.append(("url", paper.url))
    if paper.pdf_url and paper.pdf_url != paper.url:
        fields.append(("pdf", paper.pdf_url))
    if paper.citations:
        fields.append(("note", f"Cited by {paper.citations}"))
    if paper.code_url:
        fields.append(("comment", f"Code: {paper.code_url}"))

    width = max((len(k) for k, _ in fields), default=0)
    body = "\n".join(f"  {k:<{width}} = {{{v}}}," for k, v in fields)
    return f"@{etype}{{{cite},\n{body}\n}}"


def to_bibtex(papers: list[Paper], *, header: str = "") -> str:
    """多篇 → 一个完整的 .bib 文本。

    自动处理**重复 cite key**（同名同姓同年会撞车）——
    撞了就加后缀 a/b/c，保证生成的文件能被 BibTeX 直接编译。
    """
    used: set[str] = set()
    blocks: list[str] = []
    for p in papers:
        blocks.append(to_bibtex_entry(p, citation_key(p, used)))
    parts = []
    if header:
        parts.append(f"% {header}")
        parts.append("")
    parts.extend(blocks)
    return "\n\n".join(parts) + "\n"
