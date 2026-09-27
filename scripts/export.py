"""导出 — 生成 README.md（awesome list）+ docs/data/papers.json（网站数据）

用法：
    python3 scripts/export.py
"""

from __future__ import annotations

import json
import pathlib
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

# 分类展示顺序
CAT_ORDER = [
    "Object-Goal Navigation (ObjectNav)",
    "Vision-and-Language Navigation (VLN)",
    "Aerial VLN",
    "Semantic / Open-Vocabulary Navigation",
    "Social Navigation",
    "Exploration",
    "Other",
]

CAT_ANCHOR = {
    "Object-Goal Navigation (ObjectNav)": "objectnav",
    "Vision-and-Language Navigation (VLN)": "vln",
    "Aerial VLN": "aerial-vln",
    "Semantic / Open-Vocabulary Navigation": "semantic-nav",
    "Social Navigation": "social-nav",
    "Exploration": "exploration",
    "Other": "other",
}


def load():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM papers ORDER BY published_date DESC, citations DESC")]
    con.close()
    for r in rows:
        try:
            r["authors"] = json.loads(r["authors"] or "[]")
        except Exception:  # noqa: BLE001
            r["authors"] = []
        try:
            r["sources"] = json.loads(r["sources"] or "[]")
        except Exception:  # noqa: BLE001
            r["sources"] = []
    return rows


def fmt_authors(authors, limit=3) -> str:
    if not authors:
        return ""
    if len(authors) <= limit:
        return ", ".join(authors)
    return ", ".join(authors[:limit]) + " et al."


def fmt_venue(r) -> str:
    v = (r.get("venue") or "").strip()
    if v:
        # 去掉过长的会议全名
        v = v.replace("Proceedings of the ", "").replace("Proceedings of ", "")
        if len(v) > 62:
            v = v[:59] + "…"
        return v
    y = r.get("year")
    return f"arXiv {y}" if y else "arXiv"


def paper_line(r) -> str:
    title = r["title"].strip().replace("\n", " ")
    link = r.get("url") or (f"https://doi.org/{r['doi']}" if r.get("doi") else "")
    if not link and r.get("pdf_url"):
        link = r["pdf_url"]
    if link:
        t = f"[{title}]({link})"
    else:
        t = title
    parts = [f"- {t}"]
    meta = []
    a = fmt_authors(r.get("authors"))
    if a:
        meta.append(a)
    meta.append(fmt_venue(r))
    if r.get("published_date"):
        meta.append(f"`{r['published_date']}`")
    if r.get("citations"):
        meta.append(f"⭐ {r['citations']}")
    code = []
    if r.get("pdf_url"):
        code.append(f"[PDF]({r['pdf_url']})")
    if code:
        meta.append(" · ".join(code))
    parts.append("  \n  " + " · ".join(meta))
    return "".join(parts)


def build_readme(rows: list) -> str:
    total = len(rows)
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [r for r in rows if (r.get("published_date") or "") >= week_ago]

    cats = defaultdict(list)
    for r in rows:
        cats[r.get("category") or "Other"].append(r)

    lines = []
    lines.append("# Awesome Zero-Shot Navigation\n")
    lines.append(
        "> A curated list of **zero-shot / training-free navigation** research — "
        "**automatically updated every day**.\n")
    lines.append(
        "> Covering *Object-Goal Navigation · Vision-and-Language Navigation (VLN) · "
        "Aerial VLN · Semantic/Open-Vocabulary Navigation · Social Navigation · "
        "Exploration*.\n")
    lines.append(
        f"> 🔎 **Browse the papers in a searchable web UI → "
        f"[**{SITE.replace('https://', '')}**]({SITE})**\n")

    # 徽章
    lines.append(
        f"![Papers](https://img.shields.io/badge/papers-{total}-blue) "
        f"![Last update](https://img.shields.io/badge/updated-{now.strftime('%Y--%m--%d')}-green) "
        "![License](https://img.shields.io/badge/license-MIT-lightgrey)\n")

    lines.append("---\n")

    # 统计
    lines.append("## 📊 Stats\n")
    lines.append(f"- **Total papers**: {total}")
    lines.append(f"- **New in the last 7 days**: {len(recent)}")
    lines.append(f"- **Categories**: {len([c for c in cats if c])}")
    src = defaultdict(int)
    for r in rows:
        for s in (r.get("sources") or []):
            src[s] += 1
    if src:
        lines.append("- **Sources**: " + ", ".join(f"{k} ({v})"
                                                  for k, v in sorted(src.items())))
    lines.append(f"- **Last updated**: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # 目录
    lines.append("## 🗂 Contents\n")
    for c in CAT_ORDER:
        if cats.get(c):
            n = len(cats[c])
            lines.append(f"- [{c}](#{CAT_ANCHOR[c]}) — {n} papers")
    lines.append("- [Related awesome lists](#-related-awesome-lists)")
    lines.append("- [About / Contributing](#-about)\n")

    lines.append("---\n")

    # 各分类
    for c in CAT_ORDER:
        items = cats.get(c)
        if not items:
            continue
        lines.append(f'<a id="{CAT_ANCHOR[c]}"></a>\n')
        lines.append(f"## {c} ({len(items)})\n")
        for r in items[:120]:
            lines.append(paper_line(r))
        if len(items) > 120:
            lines.append(f"\n*…and {len(items)-120} more (see the "
                         f"[web browser]({SITE}) for the full list).*")
        lines.append("")

    lines.append("---\n")

    # 相关列表
    lines.append("## 🔗 Related Awesome Lists\n")
    lines.append("This list is intentionally **narrow and deep** "
                 "(zero-shot navigation only). For broader coverage see:\n")
    for name, desc in [
        ("jonyzhang2023/awesome-embodied-vla-va-vln",
         "Embodied AI, VLA, VA and VLN — the largest curated list"),
        ("UCSB-AI/awesome-vision-language-navigation",
         "Vision-and-Language Navigation (ACL 2022 survey companion)"),
        ("zchoi/Awesome-Embodied-Robotics-and-Agent",
         "Embodied robotics + LLM agents"),
        ("ChanganVR/awesome-embodied-vision", "Embodied vision reading list"),
        ("Franky-X/Awesome-Embodied-Navigation",
         "Embodied navigation: concept, paradigm and SOTA"),
        ("daqingliu/awesome-vln", "Vision-Language Navigation papers"),
    ]:
        lines.append(f"- [{name}](https://github.com/{name}) — {desc}")
    lines.append("")

    lines.append("---\n")

    # About
    lines.append('<a id="about"></a>\n')
    lines.append("## 📖 About\n")
    lines.append(
        "**What counts as \"zero-shot navigation\" here?** Papers where an embodied "
        "agent navigates to a goal **without task-specific training** — including "
        "zero-shot, training-free, open-vocabulary, and foundation-model / VLA-driven "
        "approaches that generalize to unseen goals or environments.\n")
    lines.append("**How it works**\n")
    lines.append("```")
    lines.append("Every day (GitHub Actions, free for public repos):")
    lines.append("  1. Harvest  → OpenAlex + Crossref (15 queries, title/abstract search)")
    lines.append("  2. Filter   → rule-based relevance check (see scripts/relevance.py)")
    lines.append("  3. Dedup    → DOI / arXiv ID / fuzzy title matching")
    lines.append("  4. Export   → this README + docs/data/papers.json")
    lines.append("  5. Deploy   → GitHub Pages")
    lines.append("```\n")
    lines.append("**Contributing** — Found a missing paper or a mis-classification?\n")
    lines.append("1. Open an [issue](../../issues) with the paper link, **or**")
    lines.append("2. Add it to `scripts/` — see [CONTRIBUTING.md](CONTRIBUTING.md) "
                 "(coming soon), **or**")
    lines.append("3. Just edit `data/papers.db` metadata... (don't — open an issue instead 😄)\n")
    lines.append("**Citation**\n")
    lines.append("```bibtex")
    lines.append("@misc{awesome_zero_shot_navigation,")
    lines.append(f"  title  = {{Awesome Zero-Shot Navigation}},")
    lines.append(f"  author = {{Yin, Zhongqi}},")
    lines.append(f"  year   = {{{now.year}}},")
    lines.append(f"  url    = {{https://github.com/{REPO}}}")
    lines.append("}")
    lines.append("```\n")
    lines.append("**License** — MIT (list); each paper belongs to its authors.\n")
    lines.append("---\n")
    lines.append(f"*Auto-generated. Last update: {now.strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append("")
    return "\n".join(lines)


def build_site_json(rows: list) -> dict:
    """网站数据：精简字段，去掉超长摘要"""
    out = []
    for r in rows:
        ab = (r.get("abstract") or "").strip()
        out.append({
            "t": r["title"],
            "a": r.get("authors") or [],
            "y": r.get("year"),
            "d": r.get("published_date"),
            "v": r.get("venue"),
            "ab": ab[:900] + ("…" if len(ab) > 900 else ""),
            "u": r.get("url"),
            "doi": r.get("doi"),
            "c": r.get("citations") or 0,
            "cat": r.get("category") or "Other",
            "src": r.get("sources") or [],
            "oa": bool(r.get("open_access")),
            "pdf": r.get("pdf_url"),
        })
    cats = defaultdict(int)
    for r in out:
        cats[r["cat"]] += 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": REPO,
        "site": SITE,
        "total": len(out),
        "categories": dict(cats),
        "papers": out,
    }


def main():
    rows = load()
    print(f"📚 读取 {len(rows)} 篇")
    README.write_text(build_readme(rows), encoding="utf-8")
    print(f"✅ README.md ({README.stat().st_size/1024:.1f} KB)")

    SITE_DATA.parent.mkdir(parents=True, exist_ok=True)
    data = build_site_json(rows)
    SITE_DATA.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                         encoding="utf-8")
    print(f"✅ docs/data/papers.json ({SITE_DATA.stat().st_size/1024:.1f} KB)")
    return data


if __name__ == "__main__":
    main()
