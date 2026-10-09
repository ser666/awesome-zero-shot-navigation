"""增强队列排序的守卫测试（2026-10-09）。

为什么需要
----------
增强环节每轮只处理队首 `github_max`（250）条，而待补的常驻 700+ 篇
⇒ **排序规则直接决定"哪些论文永远补不到"**。

实测抓到的 bug：原排序只有「缺代码优先 + 最新优先」两档，
而**已正式发表的顶会/顶刊论文恰好最旧**（同一工作的 arXiv 预印本更早发表），
于是它们被永久压在队尾 —— 后果是「顶会 94 篇里只有 4 篇有代码（4%）」，
而全库是 13%；待补的 91 篇顶会里 **90 篇排在 250 名之后**。

⚠️ 这类 bug 不报错、功能照常，只是**稳定地漏掉某一类数据**。
   靠"跑一遍看看"发现不了（页面看起来都正常）。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sqlite3
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "papers.db"
GITHUB_MAX = 250          # 与 pipeline.enrich 的默认值保持一致


def _load_pipeline():
    spec = importlib.util.spec_from_file_location(
        "azn_pipeline", ROOT / "scripts" / "pipeline.py")
    assert spec and spec.loader, "无法加载 scripts/pipeline.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestEnrichQueueSql(unittest.TestCase):
    def setUp(self):
        self.mod = _load_pipeline()

    def test_sql_has_tier_a_priority_clause(self):
        """⭐ 回归：队列必须给顶会/顶刊一个优先档。"""
        sql = self.mod.enrich_queue_sql(True)
        self.assertIn("venue_tier = 'A'", sql,
                      "增强队列少了「顶会优先」那一档 —— 顶会会被永久压队尾")

    def test_missing_code_still_first_priority(self):
        """缺代码仍是最优先档（不能被顶会优先顶掉）。"""
        sql = self.mod.enrich_queue_sql(True)
        i_code = sql.index("code_url IS NULL OR code_url = ''")
        i_tier = sql.index("venue_tier = 'A'")
        self.assertLess(i_code, i_tier, "「缺代码优先」必须排在「顶会优先」之前")

    def test_only_missing_adds_filter(self):
        self.assertIn("WHERE", self.mod.enrich_queue_sql(True))
        self.assertNotIn("WHERE", self.mod.enrich_queue_sql(False))


class TestEnrichQueueAgainstRealDb(unittest.TestCase):
    """用真实库验证：顶会论文确实排进了前 `github_max` 条。

    ⚠️ 这是**端到端**的检查 —— 直接回答"顶会还会不会被压队尾"。
    """

    def setUp(self):
        if not DB.is_file():
            self.skipTest("data/papers.db 不存在")
        spec = importlib.util.spec_from_file_location(
            "azn_pipeline_db", ROOT / "scripts" / "pipeline.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.sql = mod.enrich_queue_sql(True)

    def _queue(self):
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(self.sql)]
        con.close()
        return rows

    def test_queue_is_nonempty(self):
        self.assertTrue(self._queue())

    def test_tier_a_papers_missing_code_land_in_first_batch(self):
        """⭐⭐ 核心断言：**缺代码**的顶会论文必须全部落在第一轮 250 条之内。

        队列 SQL 里没有 SELECT venue_tier（保持字段最小），所以回查 db 拿 tier。

        ⚠️ 注意断言的精确范围：只要求「缺 code」的顶会进第一轮。
           "已有 code、只缺 PDF" 的顶会可以排在后面 —— 那是合理的
           （代码链接是增强里最值钱的一项，PDF 次之）。
           第一版断言写成了"所有顶会"，结果被一篇缺 PDF 的顶会论文正确驳回。

        （若某天"缺代码的顶会"涨到超过 250，这条会失败 —— 那时该调 github_max
          或改成按轮次轮转，而不是重蹈覆辙。）
        """
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        meta = {r["id"]: (r["venue_tier"], r["code_url"] or "")
                for r in con.execute("SELECT id, venue_tier, code_url FROM papers")}
        rows = [dict(r) for r in con.execute(self.sql)]
        con.close()

        pos = [i for i, r in enumerate(rows)
               if meta.get(r["id"], ("", ""))[0] == "A"
               and not meta.get(r["id"], ("", ""))[1]]
        if not pos:
            self.skipTest("当前没有缺代码的顶会论文")
        stragglers = [i for i in pos if i >= GITHUB_MAX]
        self.assertEqual(
            stragglers, [],
            f"{len(stragglers)} 篇缺代码的顶会论文排在队首 {GITHUB_MAX} 条之外 —— "
            f"它们这一轮补不到（位置示例：{[p + 1 for p in stragglers[:5]]}）")


class TestTierACoverageNotDegenerate(unittest.TestCase):
    """顶会论文的代码覆盖率不应显著低于全库 —— 数据质量的护栏。

    ⚠️ 阈值取得很宽松（只防"明显退化"），因为真实覆盖率受数据源限制：
       它只用来抓住"整类论文被系统性漏掉"这种情况，不做质量评分。
    """

    def setUp(self):
        import json
        p = ROOT / "docs" / "data" / "papers.json"
        if not p.exists():
            self.skipTest("papers.json 不存在")
        self.papers = json.loads(p.read_text(encoding="utf-8"))["papers"]

    def test_tier_a_code_rate_is_not_an_order_of_magnitude_worse(self):
        top = [p for p in self.papers if p.get("vt") == "A"]
        if len(top) < 20:
            self.skipTest("顶会样本太少")
        top_rate = sum(1 for p in top if p.get("co")) / len(top)
        all_rate = sum(1 for p in self.papers if p.get("co")) / len(self.papers)
        self.assertGreater(
            top_rate * 4, all_rate,
            f"顶会有代码率 {top_rate:.1%} 远低于全库 {all_rate:.1%} —— "
            f"疑似又出现了「顶会被压在增强队列队尾」的问题")


if __name__ == "__main__":
    unittest.main()
