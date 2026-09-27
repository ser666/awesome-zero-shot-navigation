#!/usr/bin/env python3
"""影响评估 —— 收紧相关性规则前，先看清"会删掉谁"

⭐ 为什么必须有这个脚本：
   过滤器的**误杀成本远高于误收成本** ——
   列表里多一条无关条目只是小瑕疵，漏掉一篇重要论文是真实损失（而且永不发现）。
   所以任何规则收紧都必须先跑本脚本、**人工看一遍要删的每一条**，绝不能盲删。

用法：
    python3 scripts/impact.py            # 列出会被清掉的论文
    python3 scripts/impact.py --brief    # 只看数量 + 分类统计
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from relevance import check  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "papers.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", action="store_true", help="只看统计")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id, title, abstract, relevance, category, citations "
        "FROM papers ORDER BY citations DESC")]
    con.close()

    drop = []
    for r in rows:
        ok, why = check(r["title"], r["abstract"] or "")
        if not ok:
            drop.append((r, why))

    print(f"库中 {len(rows)} 篇 → 用当前规则重判，会被清掉 {len(drop)} 篇"
          f"（{len(drop)*100//max(len(rows),1)}%）")
    if not drop:
        return
    print()
    print("按新判定原因:")
    for why, n in Counter(w for _, w in drop).most_common():
        print(f"  {n:>4}  {why}")
    print()
    print("按原分类:")
    for cat, n in Counter(r["category"] for r, _ in drop).most_common():
        print(f"  {n:>4}  {cat}")

    if args.brief:
        return

    print()
    print("=" * 88)
    print("⚠️ 请逐条人工确认下面每一条都**确实不该留**（误杀比误收更糟）")
    print("=" * 88)
    for r, why in drop:
        print(f"\n  [{why}]")
        print(f"  {r['title'][:100]}")
        print(f"    原分类={r['category']} ｜ 引用={r['citations']} "
              f"｜ 旧理由={r['relevance']}")


if __name__ == "__main__":
    main()
