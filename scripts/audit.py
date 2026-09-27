#!/usr/bin/env python3
"""质量审计 — 检查采集进来的论文是否真的属于「零样本导航」

用途：跑完 pipeline 后抽查，发现误收就回去改 relevance.py
"""
import json
import pathlib
import random
import sqlite3
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from relevance import check  # noqa: E402

DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "papers.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("SELECT * FROM papers")]
con.close()

print("=" * 78)
print(f"📊 总量: {len(rows)} 篇")
print("=" * 78)

# ① 分类分布
cats = Counter(r["category"] for r in rows)
print("\n① 分类分布")
for c, n in cats.most_common():
    print(f"   {n:>5}  {c}")

# ② 命中规则分布
rules = Counter((r["relevance"] or "?").split(":")[0] for r in rows)
print("\n② 命中规则分布（A=导航+零样本, B=导航+具身+LLM, C=兜底）")
for c, n in rules.most_common():
    print(f"   {n:>5}  {c}")

# ③ ⚠️ 抽查标题里**没有**导航词的（最可能是误收）
def norm(s):
    return (s or "").lower()

no_nav = [r for r in rows if "navig" not in norm(r["title"])]
print(f"\n③ ⚠️ 标题无 'navig' 的: {len(no_nav)} 篇（最需人工核对）")
random.seed(42)
for r in random.sample(no_nav, min(12, len(no_nav))):
    print(f"   · [{r['relevance']}] {r['title'][:88]}")

# ④ 标题里连"zero-shot/training-free/open-vocab"都没有的（靠规则B进来的）
zs_words = ["zero-shot", "zero shot", "zeroshot", "training-free", "training free",
            "open-vocabulary", "open vocabulary", "without training", "no training",
            "without fine-tuning", "untrained"]
def has_zs(r):
    t = norm(r["title"]) + " " + norm(r["abstract"])
    return any(w in t for w in zs_words)

no_zs = [r for r in rows if not has_zs(r)]
print(f"\n④ ⚠️ 标题+摘要都没有零样本词的: {len(no_zs)} 篇（靠规则 B/C 进入）")
for r in random.sample(no_zs, min(12, len(no_zs))):
    print(f"   · [{r['relevance']}] {r['title'][:88]}")

# ⑤ 明显可疑的关键词（误收信号）—— 用词边界，避免 "gui"命中"guided"
SUSPECT = [r"\bgui\b", r"\bmenu\b", r"\bhyperlink", r"surg\w+",
           r"\btranslation\b", r"\bwireless\b", r"\brouting\b",
           r"\bprotein\b", r"sentiment", r"recommendation",
           r"\bdatabase\b", r"instance segmentation", r"object tracking",
           r"gesture generation", r"light field", r"cosmolog"]
import re as _re
susp = []
for r in rows:
    t = norm(r["title"])
    hits = [p for p in SUSPECT if _re.search(p, t)]
    if hits:
        susp.append((hits, r))
print(f"\n⑤ 🚨 标题含可疑词: {len(susp)} 篇")
for hits, r in susp[:15]:
    print(f"   · {hits} → {r['title'][:80]}")

# ⑥ 复核：对库里全部论文重跑一次 relevance，看有没有不一致
bad = []
for r in rows:
    ok, why = check(r["title"], r["abstract"] or "")
    if not ok:
        bad.append((why, r))
print(f"\n⑥ 用当前规则复核：{len(bad)} 篇现在判定为**不相关**（规则变过或摘要缺失）")
for why, r in bad[:10]:
    print(f"   · [{why}] {r['title'][:80]}")

# ⑦ 年份分布
years = Counter(r["year"] for r in rows if r["year"])
print("\n⑦ 年份分布（近 8 年）")
for y in sorted([y for y in years if y >= 2019], reverse=True):
    print(f"   {y}: {years[y]}")
