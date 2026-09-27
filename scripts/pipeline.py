"""管线 — 采集 → 相关性过滤 → 去重合并 → 入库

用法：
    python3 scripts/pipeline.py                 # 增量更新（默认）
    python3 scripts/pipeline.py --full          # 全量（从 2021 年起）
    python3 scripts/pipeline.py --days 30       # 只抓最近 30 天
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from relevance import check as relevance_check  # noqa: E402
from sources import QUERIES, SOURCES, now_iso  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "papers.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    title_norm    TEXT,
    authors       TEXT,          -- JSON 数组
    year          INTEGER,
    published_date TEXT,         -- YYYY-MM-DD（发表/上线时间）
    venue         TEXT,
    abstract      TEXT,
    doi           TEXT UNIQUE,
    arxiv_id      TEXT,
    citations     INTEGER DEFAULT 0,
    url           TEXT,
    pdf_url       TEXT,
    open_access   INTEGER DEFAULT 0,
    category      TEXT,          -- 任务分类
    sources       TEXT,          -- JSON 数组，来源
    relevance     TEXT,          -- 命中规则
    first_seen    TEXT,          -- 首次采集时间
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_pubdate ON papers(published_date DESC);
CREATE INDEX IF NOT EXISTS idx_cat ON papers(category);
CREATE INDEX IF NOT EXISTS idx_doi ON papers(doi);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    queries    INTEGER,
    fetched    INTEGER,
    relevant   INTEGER,
    inserted   INTEGER,
    updated    INTEGER,
    note       TEXT
);
"""

# ── 任务分类（按标题/摘要关键词）
CATEGORY_RULES = [
    ("Aerial VLN", ["aerial", "uav", "drone", "aircraft", "flight"]),
    ("Social Navigation", ["social navigation", "social nav", "crowd",
                           "pedestrian", "human-aware", "human-robot interaction"]),
    ("Vision-and-Language Navigation (VLN)",
     ["vision-and-language navigation", "vision language navigation", "vln",
      "r2r", "rxr", "instruction following", "instruction-following",
      "language instruction", "linguistic"]),
    ("Object-Goal Navigation (ObjectNav)",
     ["object navigation", "objectnav", "object goal", "object-goal",
      "ovon", "goal navigation", "goal-oriented navigation", "target object"]),
    ("Semantic / Open-Vocabulary Navigation",
     ["semantic navigation", "open-vocabulary", "open vocabulary", "semantic map",
      "scene graph"]),
    ("Exploration", ["exploration", "frontier", "active mapping"]),
]


def classify(title: str, abstract: str) -> str:
    t = f"{title} {abstract}".lower()
    for cat, keys in CATEGORY_RULES:
        if any(k in t for k in keys):
            return cat
    return "Other"


def norm_title(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ══════════════════════════════════════════════════════════════
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def existing_index(con):
    """已有论文的索引，用于去重"""
    by_doi, by_title = {}, {}
    for r in con.execute("SELECT id, doi, title_norm, year FROM papers"):
        if r["doi"]:
            by_doi[r["doi"].lower()] = r["id"]
        if r["title_norm"]:
            by_title[r["title_norm"]] = r["id"]
    return by_doi, by_title


def merge_into(existing: dict, new: dict):
    """用新数据补全已有记录（保留更好的字段）"""
    for f in ["abstract", "venue", "url", "pdf_url", "published_date",
              "arxiv_id", "doi"]:
        if not existing.get(f) and new.get(f):
            existing[f] = new[f]
    if (new.get("citations") or 0) > (existing.get("citations") or 0):
        existing["citations"] = new["citations"]
    if new.get("open_access"):
        existing["open_access"] = 1
    srcs = set(existing.get("_sources") or [])
    srcs.add(new.get("source", ""))
    existing["_sources"] = sorted(s for s in srcs if s)


# ══════════════════════════════════════════════════════════════
def run(date_from: str | None = None, date_to: str | None = None,
        verbose: bool = True) -> dict:
    import json

    started = now_iso()
    con = connect()
    by_doi, by_title = existing_index(con)
    if verbose:
        print(f"📚 库中已有 {len(by_title)} 篇")

    fetched = 0
    relevant = 0
    # 内存里的候选（本次抓到的），按 DOI/标题归一化聚合
    cand: dict = {}

    for q in QUERIES:
        for src_name in ["openalex", "crossref"]:
            if src_name == "crossref" and not date_from:
                pass  # Crossref 无日期过滤时也抓（补正式出版信息）
            if verbose:
                print(f"  🔎 [{src_name}] {q}")
            try:
                rows = SOURCES[src_name](q, date_from=date_from, date_to=date_to)
            except Exception as e:  # noqa: BLE001
                print(f"     ⚠️ {str(e)[:80]}")
                continue
            fetched += len(rows)
            kept = 0
            for p in rows:
                ok, why = relevance_check(p.get("title", ""), p.get("abstract", ""))
                if not ok:
                    continue
                kept += 1
                relevant += 1
                p["relevance"] = why
                p["title_norm"] = norm_title(p["title"])
                key = (p["doi"] or "").lower() or p["title_norm"]
                if key in cand:
                    merge_into(cand[key], p)
                else:
                    cand[key] = {**p, "_sources": [p["source"]]}
            if verbose:
                print(f"      → {len(rows)} 抓取 / {kept} 相关")
            # 限速：OpenAlex 对高频请求会 429，给它更大的间隔
            time.sleep(1.5 if src_name == "openalex" else 0.8)

    if verbose:
        print(f"\n📊 抓取 {fetched} 条，相关 {relevant} 条，去重后候选 {len(cand)} 条")

    inserted = updated = 0
    for p in cand.values():
        doi = (p.get("doi") or "").lower() or None
        tid = None
        if doi and doi in by_doi:
            tid = by_doi[doi]
        elif p["title_norm"] in by_title:
            tid = by_title[p["title_norm"]]
        else:
            # 模糊匹配（同标题不同写法）
            for tn, pid in by_title.items():
                if abs(len(tn) - len(p["title_norm"])) < 25 and \
                        similar(tn, p["title_norm"]) >= 0.88:
                    tid = pid
                    break

        cat = classify(p["title"], p.get("abstract") or "")
        srcs = json.dumps(p.get("_sources") or [p.get("source")], ensure_ascii=False)

        if tid:
            row = dict(con.execute("SELECT * FROM papers WHERE id=?", (tid,)).fetchone())
            merge_into(row, p)
            con.execute("""UPDATE papers SET abstract=?, venue=?, url=?, pdf_url=?,
                           published_date=?, arxiv_id=?, citations=?, open_access=?,
                           sources=?, relevance=?, category=?, updated_at=?
                           WHERE id=?""",
                        (row.get("abstract"), row.get("venue"), row.get("url"),
                         row.get("pdf_url"), row.get("published_date"),
                         row.get("arxiv_id"), row.get("citations") or 0,
                         1 if row.get("open_access") else 0, srcs,
                         p["relevance"], cat, now_iso(), tid))
            updated += 1
        else:
            con.execute("""INSERT INTO papers
                (title, title_norm, authors, year, published_date, venue, abstract,
                 doi, arxiv_id, citations, url, pdf_url, open_access, category,
                 sources, relevance, first_seen, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (p["title"], p["title_norm"], json.dumps(p.get("authors") or [],
                                                                 ensure_ascii=False),
                         p.get("year"), p.get("published_date"), p.get("venue"),
                         p.get("abstract"), p.get("doi"), p.get("arxiv_id"),
                         p.get("citations") or 0, p.get("url"), p.get("pdf_url"),
                         1 if p.get("open_access") else 0, cat, srcs,
                         p["relevance"], now_iso(), now_iso()))
            new_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            if p.get("doi"):
                by_doi[p["doi"].lower()] = new_id
            by_title[p["title_norm"]] = new_id
            inserted += 1

    con.execute("""INSERT INTO runs (started_at, finished_at, queries, fetched,
                   relevant, inserted, updated) VALUES (?,?,?,?,?,?,?)""",
                (started, now_iso(), len(QUERIES) * 2, fetched, relevant,
                 inserted, updated))
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    con.close()

    result = {"fetched": fetched, "relevant": relevant, "inserted": inserted,
              "updated": updated, "total": total}
    if verbose:
        print(f"\n✅ 新增 {inserted} 篇 / 更新 {updated} 篇 / 库中共 {total} 篇")
    return result


def refilter(verbose: bool = True) -> dict:
    """用当前 relevance 规则重新审核库中所有论文，删除不再相关的。

    用途：规则收紧后清洗历史数据（否则旧误收会一直留着）。
    """
    from relevance import check as _check

    con = connect()
    rows = [dict(r) for r in con.execute("SELECT id, title, abstract FROM papers")]
    drop = []
    for r in rows:
        ok, why = _check(r["title"], r["abstract"] or "")
        if not ok:
            drop.append((r["id"], why, r["title"]))

    if verbose:
        print(f"🔄 重新审核 {len(rows)} 篇 → 应删除 {len(drop)} 篇")
        from collections import Counter
        for why, n in Counter(d[1] for d in drop).most_common():
            print(f"   {n:>4}  {why}")

    if drop:
        con.executemany("DELETE FROM papers WHERE id=?", [(d[0],) for d in drop])
        con.commit()
    total = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    con.close()
    if verbose:
        print(f"✅ 清洗完成：删除 {len(drop)} 篇，剩余 {total} 篇")
    return {"removed": len(drop), "remaining": total}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="全量（2021 年起）")
    ap.add_argument("--days", type=int, default=None, help="只抓最近 N 天")
    ap.add_argument("--year", type=str, default=None,
                    help="只抓某一年，如 2022（补历史用）")
    ap.add_argument("--refilter", action="store_true",
                    help="只用当前规则清洗已有数据（不采集）")
    args = ap.parse_args()

    if args.refilter:
        refilter()
        return

    if args.year:
        date_from = f"{args.year}-01-01"
        date_to = f"{args.year}-12-31"
        print(f"🚀 采集 {args.year} 年（{date_from} → {date_to}）")
        t0 = time.time()
        res = run(date_from=date_from, date_to=date_to)
        print(f"⏱ 耗时 {time.time()-t0:.1f}s")
        return res

    if args.full:
        date_from = "2021-01-01"
    elif args.days:
        date_from = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    else:
        # 增量：回看 14 天（容错，避免漏抓）
        date_from = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")

    print(f"🚀 开始采集（date_from={date_from}）")
    t0 = time.time()
    res = run(date_from=date_from)
    print(f"⏱ 耗时 {time.time()-t0:.1f}s")
    return res


if __name__ == "__main__":
    main()
