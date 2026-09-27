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
from sources import EXTRA_SOURCES, QUERIES, SOURCES, now_iso  # noqa: E402
from topics import primary_category, topic_tags  # noqa: E402
from venues import normalize as venue_normalize  # noqa: E402
import links as linkutil  # noqa: E402

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
    venue         TEXT,          -- 原始 venue 全名（数据源给的）
    abstract      TEXT,
    doi           TEXT UNIQUE,
    arxiv_id      TEXT,
    citations     INTEGER DEFAULT 0,
    url           TEXT,
    pdf_url       TEXT,
    open_access   INTEGER DEFAULT 0,
    category      TEXT,          -- 主分类
    sources       TEXT,          -- JSON 数组，来源
    relevance     TEXT,          -- 命中规则
    first_seen    TEXT,          -- 首次采集时间
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_pubdate ON papers(published_date DESC);
CREATE INDEX IF NOT EXISTS idx_cat ON papers(category);
CREATE INDEX IF NOT EXISTS idx_doi ON papers(doi);
-- ⚠️ idx_venue 不在这里建：venue_short 是后加的列，
--    对已存在的表要先 ALTER 才能建索引 → 放在 migrate() 之后建。

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

# ── 增量迁移：给已存在的库补字段
#
# 为什么不用"删库重建"：库里已有近 700 篇论文（含人工审计过的结果），
# 重建会丢掉 first_seen 等历史。所以用 ALTER TABLE 平滑升级。
MIGRATIONS: list[tuple[str, str]] = [
    # (列名, 定义)
    ("venue_short", "TEXT"),      # 归一化后的简称，如 ICRA / TPAMI
    ("venue_tier", "TEXT"),       # "A" = 重要会议/期刊（README 里加粗）
    ("venue_kind", "TEXT"),       # conference / journal / preprint / proceedings
    ("venue_year", "INTEGER"),    # 会议届次年份
    ("code_url", "TEXT"),         # 代码仓库
    ("project_url", "TEXT"),      # 项目主页 / demo
    ("topics", "TEXT"),           # JSON 数组：细粒度子专题标签
    ("hotness", "INTEGER DEFAULT 0"),  # 社区热度（HF upvotes 之类）
    ("tldr", "TEXT"),             # Semantic Scholar 的一句话摘要（可选）
]


def migrate(con) -> list[str]:
    """补齐新增列。返回实际新增的列名列表。"""
    have = {r[1] for r in con.execute("PRAGMA table_info(papers)")}
    added = []
    for col, decl in MIGRATIONS:
        if col not in have:
            con.execute(f"ALTER TABLE papers ADD COLUMN {col} {decl}")
            added.append(col)
    # 依赖新列的索引必须在这里建（老库的列还没加完时不能建）
    con.execute("CREATE INDEX IF NOT EXISTS idx_venue ON papers(venue_short)")
    con.commit()
    return added


def classify(title: str, abstract: str) -> str:
    """主分类（委托给 topics 模块，规则集中在一处便于维护）"""
    return primary_category(title, abstract)


def derive_fields(p: dict) -> dict:
    """计算所有**派生字段**（每次写入都重算 → 规则升级后自动回填旧数据）。

    派生自：
      · venue          → venue_short / venue_tier / venue_kind / venue_year
      · title+abstract → category（主分类）、topics（子专题标签）
      · abstract 里的 URL → code_url / project_url（若源数据没给）
    """
    import json

    v_short, v_tier, v_kind, v_year = venue_normalize(
        p.get("venue"), fallback_year=p.get("year"))

    # 链接：源数据没提供时，从摘要里捞（作者常把项目页写进摘要）
    code = p.get("code_url") or ""
    proj = p.get("project_url") or ""
    if not code or not proj:
        found = linkutil.from_text(p.get("abstract") or "", p.get("tldr") or "")
        code = code or found.get("code", "")
        proj = proj or found.get("project", "")

    return {
        "category": primary_category(p.get("title") or "",
                                     p.get("abstract") or ""),
        "venue_short": v_short,
        "venue_tier": v_tier,
        "venue_kind": v_kind,
        "venue_year": v_year,
        "code_url": _clean_url(code, is_code=True),
        "project_url": _clean_url(proj),
        "topics": json.dumps(
            topic_tags(p.get("title") or "", p.get("abstract") or ""),
            ensure_ascii=False),
    }


def _clean_url(u: str | None, is_code: bool = False) -> str | None:
    """URL 卫生：去空白/转义换行；code 链接归一化到仓库根。

    为什么需要：数据源给的标题/摘要里常带 `\\n` 转义，会把 URL 尾巴弄脏
    （点进去 404）；从摘要捞的 GitHub 链接常带 /tree/main 或 /compare/…
    子路径。实测出现过这些脏数据，所以在**每次写入时统一清洗**。
    """
    if not u:
        return None
    u = str(u).replace("\\n", "").replace("\\r", "").replace("\\t", "")
    u = re.sub(r"\s+", "", u).strip()
    if is_code and "github.com" in u.lower():
        u = linkutil.normalize_github(u)
    return u or None


def enrich(limit: int | None = None, only_missing: bool = True,
           budget_s: int = 420, verbose: bool = True,
           github_max: int = 250) -> dict:
    """增强环节 —— 给论文补「引用数 / 开放 PDF / 代码仓库」。

    为什么要独立成一个阶段（而不是塞进采集循环）：
      ① 增强是**逐篇**请求，数量级远大于采集（700 篇 vs 35 个查询），
         必须限速 + 有预算上限，否则一定超时；
      ② 增强失败**不该影响采集**（是锦上添花）；
      ③ 可按需**只补缺失的**（`only_missing`），日常增量只跑几十篇。

    ⚠️ 两个独立预算（都要有）：
      · budget_s   —— 总时间上限（超时留到下次，反正每周都跑）
      · github_max —— GitHub 搜索次数上限（认证后 30 次/分钟，
                      且它是本环节最慢的一步，单独限流防止挤掉其他源）
    """
    import sources_extra as sx

    t0 = time.time()
    con = connect()

    if only_missing:
        # 按"缺什么"选目标：缺 PDF 或 缺代码链接的都要补。
        # ⚠️ 不能写成"citations=0 且 无代码 且 无项目页"——
        #    那样会把"已有引用数但没代码链接"的论文全跳过（实测漏掉 600 篇）。
        where = ("WHERE (pdf_url IS NULL OR pdf_url = '' "
                 "OR code_url IS NULL OR code_url = '')")
    else:
        where = ""
    sql = f"""SELECT id, title, doi, arxiv_id, abstract, pdf_url, citations,
                     code_url, project_url, open_access, venue, year
              FROM papers {where}
              ORDER BY (code_url IS NULL OR code_url = '') DESC,
                       published_date DESC"""
    rows = [dict(r) for r in con.execute(sql)]
    if limit:
        rows = rows[:limit]
    if verbose:
        print(f"🔧 待增强 {len(rows)} 篇"
              f"（预算 {budget_s}s ｜ GitHub 搜索上限 {github_max} 次）")

    stats: dict[str, float] = {"s2": 0, "unpaywall": 0, "github": 0,
                               "pdf_added": 0, "code_added": 0,
                               "proj_added": 0, "cites_updated": 0, "done": 0}
    pat = None
    try:
        tok_file = pathlib.Path.home() / ".hermes" / "secrets" / "github_pat"
        if tok_file.exists():
            pat = tok_file.read_text().strip()
    except Exception:  # noqa: BLE001
        pat = None

    for r in rows:
        if time.time() - t0 > budget_s:
            if verbose:
                print(f"⏹ 达到预算上限，剩 {len(rows) - stats['done']} 篇留待下次")
            break

        upd: dict = {}

        # ① Semantic Scholar：引用数 / 开放 PDF（单篇接口稳定）
        try:
            s2 = sx.enrich_semanticscholar(doi=r.get("doi"),
                                           arxiv_id=r.get("arxiv_id"))
            if s2:
                stats["s2"] += 1
                if s2.get("citations") and s2["citations"] > (r.get("citations") or 0):
                    upd["citations"] = s2["citations"]
                    stats["cites_updated"] += 1
                if s2.get("pdf_url") and not r.get("pdf_url"):
                    upd["pdf_url"] = s2["pdf_url"]
                    upd["open_access"] = 1
                    stats["pdf_added"] += 1
                if s2.get("tldr"):
                    upd["tldr"] = s2["tldr"]
            time.sleep(0.6)          # S2 无 Key 配额低，必须限速
        except Exception:  # noqa: BLE001
            pass

        # ② Unpaywall：开放获取 PDF（补"没有 arXiv 版"的期刊论文）
        if not r.get("pdf_url") and r.get("doi"):
            try:
                up = sx.enrich_unpaywall(r["doi"])
                if up:
                    stats["unpaywall"] += 1
                    if up.get("pdf_url"):
                        upd["pdf_url"] = up["pdf_url"]
                        upd["open_access"] = 1
                        stats["pdf_added"] += 1
                time.sleep(0.4)
            except Exception:  # noqa: BLE001
                pass

        # ③ 从摘要再捞一次链接（源数据可能更新过摘要）
        if not upd.get("code_url") and not r.get("code_url"):
            found = linkutil.from_text(r.get("abstract") or "")
            if found.get("code"):
                upd["code_url"] = found["code"]
                stats["code_added"] += 1
            if found.get("project"):
                upd["project_url"] = found["project"]
                stats["proj_added"] += 1

        # ④ GitHub 搜索找代码仓库（只在完全没有代码链接时尝试 —— 省配额）
        if pat and not (upd.get("code_url") or r.get("code_url")) \
                and stats["github"] < github_max:
            try:
                url = linkutil.github_code_url(r["title"], token=pat)
                if url:
                    upd["code_url"] = url
                    stats["code_added"] += 1
                stats["github"] += 1
                time.sleep(2.2)      # GitHub 搜索：认证后 30 次/分钟
            except Exception:  # noqa: BLE001
                pass

        if upd:
            sets = ", ".join(f"{k}=?" for k in upd)
            con.execute(f"UPDATE papers SET {sets}, updated_at=? WHERE id=?",
                        (*upd.values(), now_iso(), r["id"]))
        stats["done"] += 1
        if verbose and stats["done"] % 25 == 0:
            con.commit()
            print(f"   … 已处理 {stats['done']}/{len(rows)}"
                  f"（补 PDF {stats['pdf_added']} ｜ 补代码 {stats['code_added']}）")

    con.commit()
    total = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    with_pdf = con.execute("SELECT COUNT(*) FROM papers WHERE pdf_url != '' "
                           "AND pdf_url IS NOT NULL").fetchone()[0]
    with_code = con.execute("SELECT COUNT(*) FROM papers WHERE code_url != '' "
                            "AND code_url IS NOT NULL").fetchone()[0]
    with_venue = con.execute("SELECT COUNT(*) FROM papers WHERE venue_tier = 'A'"
                             ).fetchone()[0]
    con.close()

    stats.update({"total": total, "have_pdf": with_pdf, "have_code": with_code,
                  "have_venue_a": with_venue,
                  "elapsed_s": round(time.time() - t0, 1)})
    if verbose:
        print(f"✅ 增强完成（{stats['elapsed_s']}s）："
              f"PDF {with_pdf}/{total} ｜ 代码 {with_code}/{total} ｜ "
              f"重要会议/期刊 {with_venue}/{total}")
        print(f"   本次：S2 {int(stats['s2'])} ｜ Unpaywall {int(stats['unpaywall'])}"
              f" ｜ GitHub 搜索 {int(stats['github'])} 次"
              f"（补代码 {int(stats['code_added'])} ｜ 补 PDF {int(stats['pdf_added'])}）")
    return stats

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
    migrate(con)          # 平滑补列（不重建库，保住既有数据）
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
              "arxiv_id", "doi", "code_url", "project_url", "tldr"]:
        if not existing.get(f) and new.get(f):
            existing[f] = new[f]
    if (new.get("citations") or 0) > (existing.get("citations") or 0):
        existing["citations"] = new["citations"]
    if (new.get("hotness") or 0) > (existing.get("hotness") or 0):
        existing["hotness"] = new["hotness"]
    if new.get("open_access"):
        existing["open_access"] = 1
    srcs = set(existing.get("_sources") or [])
    srcs.add(new.get("source", ""))
    existing["_sources"] = sorted(s for s in srcs if s)


# ══════════════════════════════════════════════════════════════
def run(date_from: str | None = None, date_to: str | None = None,
        verbose: bool = True, sort: str = "date",
        budget_s: int | None = 900) -> dict:
    """采集 → 过滤 → 入库。

    budget_s: 时间预算（秒）。超过后不再发起新查询（只收尾入库）。
              防某个查询疯狂重试把整个任务拖超时 —— 漏掉的明天会补回来。
    """
    import json

    started = now_iso()
    t_start = time.time()
    con = connect()
    by_doi, by_title = existing_index(con)
    if verbose:
        print(f"📚 库中已有 {len(by_title)} 篇"
              + (f"｜时间预算 {budget_s}s" if budget_s else ""))

    fetched = 0
    relevant = 0
    cand: dict = {}
    skipped = 0

    for q in QUERIES:
        for src_name in ["openalex", "crossref"]:
            if budget_s and (time.time() - t_start) > budget_s:
                skipped += 1
                continue
            if verbose:
                print(f"  🔎 [{src_name}] {q}")
            try:
                if src_name == "openalex":
                    rows = SOURCES[src_name](q, date_from=date_from,
                                             date_to=date_to, sort=sort)
                else:
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

    # ── 附加源（每轮只调一次，不按关键词循环）
    #
    # 为什么单独处理：OpenReview / HuggingFace 是"平台聚合接口"，
    # 一次请求就能拿到一批论文，按 30+ 个关键词反复请求既浪费又易被封。
    for src_name, fn in EXTRA_SOURCES.items():
        if budget_s and (time.time() - t_start) > budget_s * 0.8:
            skipped += 1
            continue
        if verbose:
            print(f"  🔎 [{src_name}] （平台聚合接口）")
        try:
            rows = fn(date_from=date_from) if src_name == "huggingface" \
                else fn("navigation", date_from=date_from)
        except Exception as e:  # noqa: BLE001
            print(f"     ⚠️ [{src_name}] {str(e)[:80]}")
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
            key = (p.get("doi") or "").lower() or p["title_norm"]
            if key in cand:
                merge_into(cand[key], p)
            else:
                cand[key] = {**p, "_sources": [p["source"]]}
        if verbose:
            print(f"      → {len(rows)} 抓取 / {kept} 相关")
        time.sleep(1.0)

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

        # ── 派生字段（每次入库都重算，保证规则升级后能回填）
        d = derive_fields(p)

        if tid:
            row = dict(con.execute("SELECT * FROM papers WHERE id=?", (tid,)).fetchone())
            merge_into(row, p)
            row.update({k: v for k, v in d.items() if v not in (None, "")})
            d = derive_fields(row)
            con.execute("""UPDATE papers SET abstract=?, venue=?, url=?, pdf_url=?,
                           published_date=?, arxiv_id=?, citations=?, open_access=?,
                           sources=?, relevance=?, category=?, venue_short=?,
                           venue_tier=?, venue_kind=?, venue_year=?, code_url=?,
                           project_url=?, topics=?, hotness=?, tldr=?, updated_at=?
                           WHERE id=?""",
                        (row.get("abstract"), row.get("venue"), row.get("url"),
                         row.get("pdf_url"), row.get("published_date"),
                         row.get("arxiv_id"), row.get("citations") or 0,
                         1 if row.get("open_access") else 0, srcs,
                         p["relevance"], d["category"], d["venue_short"],
                         d["venue_tier"], d["venue_kind"], d["venue_year"],
                         d["code_url"], d["project_url"], d["topics"],
                         row.get("hotness") or 0, row.get("tldr"), now_iso(), tid))
            updated += 1
        else:
            con.execute("""INSERT INTO papers
                (title, title_norm, authors, year, published_date, venue, abstract,
                 doi, arxiv_id, citations, url, pdf_url, open_access, category,
                 sources, relevance, venue_short, venue_tier, venue_kind, venue_year,
                 code_url, project_url, topics, hotness, tldr, first_seen, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (p["title"], p["title_norm"],
                         json.dumps(p.get("authors") or [], ensure_ascii=False),
                         p.get("year"), p.get("published_date"), p.get("venue"),
                         p.get("abstract"), p.get("doi"), p.get("arxiv_id"),
                         p.get("citations") or 0, p.get("url"), p.get("pdf_url"),
                         1 if p.get("open_access") else 0, d["category"], srcs,
                         p["relevance"], d["venue_short"], d["venue_tier"],
                         d["venue_kind"], d["venue_year"], d["code_url"],
                         d["project_url"], d["topics"], p.get("hotness") or 0,
                         p.get("tldr"), now_iso(), now_iso()))
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
              "updated": updated, "total": total, "skipped": skipped,
              "elapsed_s": round(time.time() - t_start, 1)}
    if verbose:
        print(f"\n✅ 新增 {inserted} 篇 / 更新 {updated} 篇 / 库中共 {total} 篇")
        if skipped:
            print(f"⏹ 因超时预算跳过 {skipped} 个查询（明天会补）")
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


def backfill(verbose: bool = True) -> dict:
    """重算所有论文的**派生字段**（不联网，秒级完成）。

    用途：规则升级后回填历史数据。
      例：新增「会议分级表」或「子专题标签」后，
          旧的 700 篇不会自动获得这些字段 —— 跑一次本函数即可。
    同时做 **URL 卫生**（去转义换行、GitHub 链接归一化到仓库根）。
    """
    con = connect()
    rows = [dict(r) for r in con.execute(
        "SELECT id, title, abstract, venue, year, tldr, code_url, project_url, "
        "pdf_url, url FROM papers")]
    changed = 0
    url_fixed = 0
    for r in rows:
        d = derive_fields(r)
        code = _clean_url(r.get("code_url") or d["code_url"], is_code=True)
        proj = _clean_url(r.get("project_url") or d["project_url"])
        pdf = _clean_url(r.get("pdf_url"))
        page = _clean_url(r.get("url"))
        if (code != r.get("code_url") or proj != r.get("project_url")
                or pdf != r.get("pdf_url") or page != r.get("url")):
            url_fixed += 1
        con.execute("""UPDATE papers SET category=?, venue_short=?, venue_tier=?,
                       venue_kind=?, venue_year=?, code_url=?, project_url=?,
                       pdf_url=?, url=?, topics=? WHERE id=?""",
                    (d["category"], d["venue_short"], d["venue_tier"],
                     d["venue_kind"], d["venue_year"], code, proj, pdf, page,
                     d["topics"], r["id"]))
        changed += 1
    con.commit()

    stats = {
        "total": changed,
        "venue_a": con.execute(
            "SELECT COUNT(*) FROM papers WHERE venue_tier='A'").fetchone()[0],
        "venue_b": con.execute(
            "SELECT COUNT(*) FROM papers WHERE venue_tier='B'").fetchone()[0],
        "with_code": con.execute(
            "SELECT COUNT(*) FROM papers WHERE code_url IS NOT NULL "
            "AND code_url != ''").fetchone()[0],
        "with_proj": con.execute(
            "SELECT COUNT(*) FROM papers WHERE project_url IS NOT NULL "
            "AND project_url != ''").fetchone()[0],
        "with_topics": con.execute(
            "SELECT COUNT(*) FROM papers WHERE topics IS NOT NULL "
            "AND topics != '[]'").fetchone()[0],
    }
    con.close()
    if verbose:
        print(f"✅ 回填完成 {stats['total']} 篇："
              f"重要会议/期刊 {stats['venue_a']} ｜ 有代码 {stats['with_code']} ｜ "
              f"有项目页 {stats['with_proj']} ｜ 有子专题标签 {stats['with_topics']}"
              + (f" ｜ URL 清洗 {url_fixed} 处" if url_fixed else ""))
    stats["url_fixed"] = url_fixed
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="全量（2021 年起）")
    ap.add_argument("--days", type=int, default=None, help="只抓最近 N 天")
    ap.add_argument("--year", type=str, default=None,
                    help="只抓某一年，如 2022（补历史用）")
    ap.add_argument("--refilter", action="store_true",
                    help="只用当前规则清洗已有数据（不采集）")
    ap.add_argument("--landmarks", action="store_true",
                    help="⭐ 抓经典论文（按引用量排序，跨全时段）")
    ap.add_argument("--budget", type=int, default=900,
                    help="时间预算秒数（默认 900；0=不限）")
    ap.add_argument("--enrich", action="store_true",
                    help="只跑增强环节（补 PDF/代码/引用数）")
    ap.add_argument("--enrich-all", action="store_true",
                    help="增强**全部**论文（不只补缺失的；耗时长）")
    ap.add_argument("--enrich-limit", type=int, default=None,
                    help="增强最多处理多少篇")
    ap.add_argument("--backfill", action="store_true",
                    help="只重算派生字段（会议分级/子专题/链接），不联网")
    args = ap.parse_args()

    budget = None if args.budget == 0 else args.budget

    if args.refilter:
        refilter()
        return

    if args.backfill:
        res = backfill(verbose=True)
        return res

    if args.enrich or args.enrich_all:
        res = enrich(limit=args.enrich_limit,
                     only_missing=not args.enrich_all,
                     budget_s=budget or 600)
        return res

    if args.landmarks:
        print("⭐ 采集经典/高引论文（sort=cited_by_count:desc，不设日期下限）")
        t0 = time.time()
        res = run(date_from=None, date_to=None, sort="cited", budget_s=budget)
        print(f"⏱ 耗时 {time.time()-t0:.1f}s")
        return res

    if args.year:
        date_from = f"{args.year}-01-01"
        date_to = f"{args.year}-12-31"
        print(f"🚀 采集 {args.year} 年（{date_from} → {date_to}）")
        t0 = time.time()
        res = run(date_from=date_from, date_to=date_to, budget_s=budget)
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
    res = run(date_from=date_from, budget_s=budget)
    print(f"⏱ 耗时 {time.time()-t0:.1f}s")
    return res


if __name__ == "__main__":
    main()
