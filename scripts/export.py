"""导出 — 生成 README.md（awesome list）+ docs/data/papers.json（网站数据）

README 条目格式（参考高 star 列表的做法，Boss 指定）
----------------------------------------------------
    - [2025] [**CVPR 2025**] Title of the paper
      [![PDF](badge)] [![project](badge)] [![code](badge)] [![DOI](badge)]
      Authors · ⭐ citations

    要点：
      · 条目前加 **年份**（[2025]）
      · 重要会议/期刊名 **加粗**（CCF-A 或机器人领域主要会议期刊）
      · 链接用 **shields.io 标签**（比纯文字链接好看，参考
        visitworld123/Awesome-Robot-Use-Agent）
      · 除 PDF 外，有 project 页 / 代码仓库也一并列出

组织方式：**分类 → 年份倒序**（参考 jonyzhang2023/awesome-embodied-vla-va-vln）
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "papers.db"
README = ROOT / "README.md"
SITE_DATA = ROOT / "docs" / "data" / "papers.json"

REPO = "ser666/awesome-zero-shot-navigation"
SITE = "https://ser666.github.io/awesome-zero-shot-navigation/"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from topics import CATEGORY_ORDER  # noqa: E402

# 分类锚点
CAT_ANCHOR = {
    "Object-Goal Navigation (ObjectNav)": "objectnav",
    "Vision-and-Language Navigation (VLN)": "vln",
    "LLM / VLM Navigation Agents": "llm-agents",
    "Aerial VLN": "aerial-vln",
    "Semantic & Open-Vocabulary Navigation": "semantic-nav",
    "Image & Point-Goal Navigation": "image-goal",
    "Multi-Object Navigation": "multi-object",
    "Social Navigation": "social-nav",
    "Exploration": "exploration",
    "Other": "other",
}

# shields.io 徽章样式（Boss 要求的"标签式链接"）
BADGE = "https://img.shields.io/badge/"


def load():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(papers)")}
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM papers ORDER BY published_date DESC, citations DESC")]
    con.close()
    for r in rows:
        for k, default in (("authors", "[]"), ("sources", "[]"), ("topics", "[]")):
            if k not in cols:
                r[k] = default
        for k, default in (("authors", []), ("sources", []), ("topics", [])):
            try:
                r[k] = json.loads(r.get(k) or json.dumps(default))
            except Exception:  # noqa: BLE001
                r[k] = default
    return rows


def fmt_authors(authors, limit=3) -> str:
    if not authors:
        return ""
    a = [x for x in authors if x]
    if len(a) <= limit:
        return ", ".join(a)
    return ", ".join(a[:limit]) + " et al."


def badge(label: str, color: str, url: str, logo: str = "") -> str:
    """生成一个 shields.io 徽章链接（HTML 形式，GitHub 上渲染最稳）"""
    src = f"{BADGE}{label.replace(' ', '%20')}-{color}"
    if logo:
        src += f"?logo={logo}"
    return f'<a href="{url}"><img src="{src}" alt="{label}"></a>'


def entry_badges(r: dict) -> str:
    """条目右侧的标签式链接"""
    out = []

    # PDF（优先 arXiv / 开放获取 PDF）
    pdf = (r.get("pdf_url") or "").strip()
    if pdf:
        label = "PDF"
        aid = (r.get("arxiv_id") or "").strip()
        if aid and "arxiv" in pdf.lower():
            label = f"arXiv {aid}"
        out.append(badge(label, "b31b1b", pdf))

    # 项目主页
    if (r.get("project_url") or "").strip():
        out.append(badge("project", "2f6fb2", r["project_url"]))

    # 代码仓库（带 star 数徽章更醒目）
    code = (r.get("code_url") or "").strip()
    if code:
        out.append(badge("code", "0f9d58", code, logo="github"))
        m = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", code)
        if m:
            slug = f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
            out.append(f'<img src="https://img.shields.io/github/stars/{slug}'
                       f'?label=%E2%AD%90" alt="stars">')

    # DOI
    if (r.get("doi") or "").strip():
        out.append(badge("DOI", "6b6659",
                         f"https://doi.org/{r['doi']}"))

    return " ".join(out)


def entry_title(r: dict) -> str:
    """标题链接（优先 DOI / 落地页，没有则 PDF）"""
    t = re.sub(r"\s+", " ", (r.get("title") or "").strip())
    link = r.get("url") or (f"https://doi.org/{r['doi']}" if r.get("doi") else "")
    link = link or r.get("pdf_url")
    # 标题里的 [ ] 会破坏 markdown 链接语法，转义
    t_safe = t.replace("[", "\\[").replace("]", "\\]")
    return f"[{t_safe}]({link})" if link else t_safe


def venue_html(r: dict) -> str:
    """会议/期刊：重要会议加粗"""
    short = (r.get("venue_short") or "").strip()
    kind = (r.get("venue_kind") or "").strip()
    vy = r.get("venue_year") or r.get("year")
    if not short:
        return ""
    # 预印本/其它：不加年份（年份已在条目开头），也不加粗
    if kind in ("preprint", "proceedings", "other"):
        text = short
    elif vy:
        text = f"{short} {vy}"
    else:
        text = short
    if r.get("venue_tier") == "A":
        return f"[**{text}**]"
    return f"[{text}]"


def paper_line(r: dict) -> str:
    """单条论文（带年份前缀 + 会议加粗 + 标签式链接）"""
    year = r.get("venue_year") or r.get("year") or 0
    ytag = f"[{year}] " if year else ""

    parts = [f"- {ytag}"]
    v = venue_html(r)
    if v:
        parts.append(v + " ")
    parts.append(entry_title(r))

    line1 = "".join(parts)

    badges = entry_badges(r)
    meta_bits = []
    a = fmt_authors(r.get("authors"))
    if a:
        meta_bits.append(a)
    if r.get("citations"):
        meta_bits.append(f"⭐ {r['citations']}")
    if r.get("open_access"):
        meta_bits.append("🔓 OA")

    line2 = badges
    if meta_bits:
        line2 += ("  " if badges else "") + "· " + " · ".join(meta_bits)

    if line2.strip():
        return f"{line1}  \n  {line2}"
    return line1


def build_readme(rows: list) -> str:
    total = len(rows)
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [r for r in rows if (r.get("published_date") or "") >= week_ago]

    cats: dict[str, list] = defaultdict(list)
    for r in rows:
        cats[r.get("category") or "Other"].append(r)

    n_tier_a = sum(1 for r in rows if r.get("venue_tier") == "A")
    n_pdf = sum(1 for r in rows if r.get("pdf_url"))
    n_code = sum(1 for r in rows if r.get("code_url"))
    n_proj = sum(1 for r in rows if r.get("project_url"))

    L: list[str] = []
    L.append("# Awesome Zero-Shot Navigation\n")
    L.append(
        "> A curated list of **zero-shot / training-free navigation** research — "
        "**automatically updated every week**.\n")
    L.append(
        "> Covering *Object-Goal Navigation · Vision-and-Language Navigation (VLN) · "
        "Aerial VLN · Semantic & Open-Vocabulary Navigation · Image-Goal · "
        "Social Navigation · Exploration*.\n")
    L.append(
        f"> 🔎 **Browse & search the papers in a web UI → "
        f"[**{SITE.replace('https://', '').rstrip('/')}**]({SITE})**\n")
    L.append(
        "> 🤖 **Use it from an AI agent (MCP) / CLI / Zotero → "
        "see [**SERVICE.md**](SERVICE.md)**\n")

    # ── 徽章行
    L.append(
        f'<a href="{SITE}"><img src="{BADGE}papers-{total}-0984e3'
        f'?style=for-the-badge&logo=googlescholar&logoColor=white" alt="Papers"></a> '
        f'<img src="{BADGE}Top%20venues-{n_tier_a}-b0522c'
        f'?style=for-the-badge" alt="Top venues"> '
        f'<img src="{BADGE}with%20code-{n_code}-0f9d58'
        f'?style=for-the-badge&logo=github" alt="with code"> '
        f'<a href="https://github.com/{REPO}/actions"><img '
        f'src="{BADGE}auto--updated%20weekly-2f6fb2'
        f'?style=for-the-badge&logo=githubactions&logoColor=white" '
        f'alt="Auto-updated weekly"></a> '
        f'<img src="{BADGE}license-MIT-lightgrey?style=for-the-badge" alt="MIT">\n')

    L.append("---\n")

    # ── 统计
    L.append("## 📊 Stats\n")
    L.append(f"- **Total papers**: **{total}**")
    L.append(f"- **New in the last 7 days**: **{len(recent)}**")
    L.append(f"- **Published at top venues** "
             f"(CCF-A / major robotics): **{n_tier_a}**")
    L.append(f"- **With PDF**: {n_pdf} ｜ **with code**: {n_code} ｜ "
             f"**with project page**: {n_proj}")
    L.append(f"- **Categories**: {len([c for c in cats if c and cats[c]])}")
    src: dict[str, int] = defaultdict(int)
    for r in rows:
        for s in (r.get("sources") or []):
            src[s] += 1
    if src:
        L.append("- **Sources**: "
                 + ", ".join(f"{k} ({v})" for k, v in sorted(src.items())))
    L.append(f"- **Last updated**: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    L.append("")

    # ── 图例（让读者看懂符号）
    L.append("## 🏷 How to read an entry\n")
    L.append("```")
    L.append("- [2025] [**CVPR 2025**] UniGoal: Towards Universal Zero-shot "
             "Goal-oriented Navigation")
    L.append("  ↑year   ↑venue (bold = top venue)   ↑title (links to paper)")
    L.append("  PDF · project · code · DOI badges, then authors and citation count")
    L.append("```")
    L.append("- **`[2025]`** — publication year")
    L.append("- **`[**CVPR 2025**]`** — published at a **top venue** "
             "(CCF-A or a major robotics conference/journal); "
             "non-bold = other venue or preprint")
    L.append("- Badges: `arXiv`/`PDF` · `project` · `code` (+ ⭐ stars) · `DOI`")
    L.append("")

    # ── 目录
    L.append("## 🗂 Contents\n")
    for c in CATEGORY_ORDER:
        if cats.get(c):
            L.append(f"- [{c}](#{CAT_ANCHOR.get(c, 'other')}) — "
                     f"{len(cats[c])} papers")
    L.append("- [Related awesome lists](#-related-awesome-lists)")
    L.append("- [How it works / Contributing](#-about)\n")
    L.append("---\n")

    # ── 各分类（内部按年份倒序分组）
    for c in CATEGORY_ORDER:
        items = cats.get(c)
        if not items:
            continue
        L.append(f'<a id="{CAT_ANCHOR.get(c, "other")}"></a>\n')
        L.append(f"## {c} ({len(items)})\n")
        by_year: dict[int, list] = defaultdict(list)
        for r in items:
            by_year[r.get("venue_year") or r.get("year") or 0].append(r)
        for y in sorted(by_year, reverse=True):
            L.append(f"### {y if y else 'Undated'}\n")
            for r in by_year[y][:150]:
                L.append(paper_line(r))
            if len(by_year[y]) > 150:
                L.append(f"\n*…and {len(by_year[y]) - 150} more — see the "
                         f"[web browser]({SITE}).*")
            L.append("")
        L.append("")
    L.append("---\n")

    # ── 相关列表
    L.append("## 🔗 Related Awesome Lists\n")
    L.append("This list is intentionally **narrow and deep** (zero-shot navigation "
             "only). For broader coverage see:\n")
    for name, desc in [
        ("jonyzhang2023/awesome-embodied-vla-va-vln",
         "Embodied AI, VLA, VA and VLN — the largest curated list"),
        ("UCSB-AI/awesome-vision-language-navigation",
         "Vision-and-Language Navigation (ACL 2022 survey companion)"),
        ("visitworld123/Awesome-Robot-Use-Agent",
         "Robot-use agents, with badge-style entries"),
        ("Franky-X/Awesome-Embodied-Navigation",
         "Embodied navigation: concept, paradigm and SOTA"),
        ("zchoi/Awesome-Embodied-Robotics-and-Agent",
         "Embodied robotics + LLM agents"),
        ("daqingliu/awesome-vln", "Vision-Language Navigation papers"),
        ("luohongkun.top/Embodied-AI-Daily",
         "Daily arXiv digest with fine-grained topic tags"),
    ]:
        if name.startswith("luohongkun"):
            L.append(f"- [{name}](https://{name}) — {desc}")
        else:
            L.append(f"- [{name}](https://github.com/{name}) — {desc}")
    L.append("")
    L.append("---\n")

    # ── About
    L.append('<a id="about"></a>\n')
    L.append("## 📖 About\n")
    L.append("**What counts as \"zero-shot navigation\" here?** Papers where an "
             "embodied agent navigates to a goal **without task-specific "
             "training** — including zero-shot, training-free, open-vocabulary, "
             "and foundation-model / VLA-driven approaches that generalize to "
             "unseen goals or environments.\n")
    L.append("**Data sources** (all free, no API key needed)\n")
    L.append("| Source | What it adds |")
    L.append("|--------|--------------|")
    L.append("| [OpenAlex](https://openalex.org) | primary harvest (title/abstract "
             "search) |")
    L.append("| [Crossref](https://crossref.org) | publication metadata, DOI |")
    L.append("| [OpenReview](https://openreview.net) | conference submissions & "
             "acceptance |")
    L.append("| [Hugging Face Papers](https://huggingface.co/papers) | trending "
             "signal |")
    L.append("| [Semantic Scholar](https://semanticscholar.org) | citation "
             "counts, TLDRs |")
    L.append("| [Unpaywall](https://unpaywall.org) | open-access PDFs |")
    L.append("| [GitHub API](https://github.com) | code repositories |")
    L.append("")
    L.append("> ℹ️ **arXiv's own API is not used** — it returns HTTP 406 for "
             "requests from cloud/datacenter IPs (GitHub Actions included), "
             "regardless of headers or HTTP/HTTPS. Verified with "
             "`scripts/probe_sources.py`.\n")
    L.append("**How it works**\n")
    L.append("```")
    L.append("Every week (GitHub Actions — free, runs in the cloud, no server needed):")
    L.append("  1. Harvest  → OpenAlex + Crossref (35 queries) + OpenReview + "
             "Hugging Face")
    L.append("  2. Filter   → rule-based relevance check (self-tested)")
    L.append("  3. Dedup    → DOI / arXiv ID / fuzzy title matching")
    L.append("  4. Enrich   → citations (S2) · OA PDFs (Unpaywall) · code repos "
             "(GitHub) · venue tiers")
    L.append("  5. Export   → this README + docs/data/papers.json")
    L.append("  6. Deploy   → GitHub Pages")
    L.append("```\n")
    L.append("See **[OPERATIONS.md](OPERATIONS.md)** for where it runs, how often, "
             "how the incremental update avoids gaps, and how the 60-day "
             "inactivity trap is handled.\n")
    L.append("**Contributing** — Found a missing paper, a wrong venue, or a "
             "mis-classification?\n")
    L.append("Open an [issue](../../issues) with the paper link and we'll fold it "
             "in. The whole pipeline lives in [`scripts/`](scripts) and is plain "
             "Python (standard library only — no dependencies to install).\n")
    L.append("**License** — MIT (list); each paper belongs to its authors.\n")
    L.append("---\n")
    L.append(f"*Auto-generated. Last update: {now.strftime('%Y-%m-%d %H:%M UTC')}*")
    L.append("")
    return "\n".join(L)


def build_site_json(rows: list) -> dict:
    """网站数据：字段精简但保留浏览所需的全部维度"""
    papers = []
    for r in rows:
        ab = re.sub(r"\s+", " ", (r.get("abstract") or "").strip())
        tldr = re.sub(r"\s+", " ", (r.get("tldr") or "").strip())
        papers.append({
            "t": r["title"],                                  # title
            "a": [x for x in (r.get("authors") or []) if x][:12],   # authors
            "d": r.get("published_date") or "",               # date
            "y": r.get("venue_year") or r.get("year") or 0,   # year
            "v": r.get("venue_short") or "",                  # venue short
            "vk": r.get("venue_kind") or "",                  # venue kind
            "vt": r.get("venue_tier") or "",                  # venue tier (A/B)
            "vf": r.get("venue") or "",                       # venue full name
            "c": r.get("category") or "Other",
            "tp": r.get("topics") or [],                      # sub-topic tags
            "u": r.get("url") or "",
            "p": r.get("pdf_url") or "",
            "co": r.get("code_url") or "",
            "pr": r.get("project_url") or "",
            "doi": r.get("doi") or "",
            "arx": r.get("arxiv_id") or "",
            "cite": r.get("citations") or 0,
            "oa": 1 if r.get("open_access") else 0,
            "hot": r.get("hotness") or 0,
            "s": ab[:1800],          # abstract（保留全文，前端按需展示）
            "tl": tldr[:400],
            "src": r.get("sources") or [],
        })

    # 主题标签统计（网站 chips 用）
    tag_count: dict[str, int] = defaultdict(int)
    for p in papers:
        for t in p["tp"]:
            tag_count[t] += 1
    cat_count: dict[str, int] = defaultdict(int)
    for p in papers:
        cat_count[p["c"]] += 1
    venue_count: dict[str, int] = defaultdict(int)
    for p in papers:
        if p["v"] and p["vk"] not in ("preprint", "proceedings", "other"):
            venue_count[p["v"]] += 1

    return {
        "total": len(papers),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "site": SITE,
        "repo": f"https://github.com/{REPO}",
        "categories": dict(sorted(cat_count.items(),
                                  key=lambda x: -x[1])),
        "topics": dict(sorted(tag_count.items(), key=lambda x: -x[1])),
        "venues": dict(sorted(venue_count.items(), key=lambda x: -x[1])),
        "papers": papers,
    }


def main():
    rows = load()
    print(f"📚 读取 {len(rows)} 篇")

    README.write_text(build_readme(rows), encoding="utf-8")
    n_readme = README.stat().st_size
    print(f"✅ README.md ({n_readme/1024:.1f} KB)")

    data = build_site_json(rows)
    SITE_DATA.parent.mkdir(parents=True, exist_ok=True)
    SITE_DATA.write_text(json.dumps(data, ensure_ascii=False),
                         encoding="utf-8")
    print(f"✅ docs/data/papers.json ({SITE_DATA.stat().st_size/1024:.1f} KB)")
    print(f"   分类 {len(data['categories'])} ｜ 子专题 {len(data['topics'])} ｜ "
          f"会议 {len(data['venues'])}")


if __name__ == "__main__":
    main()
