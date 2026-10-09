"""「新论文」判据（NEW 徽章）的守卫测试。

为什么需要
----------
NEW 判据有两类**静默失效** —— 都不报错、页面也照常渲染，只是悄悄标错：

  ① **未来日期**：旧实现用 `(Date.now() - date) <= 14 天`，
     期刊的 "in press" 会给未来月份 ⇒ 差值为负 ⇒ **永远**显示 NEW（实测 40 篇）。
  ② **精度不足的日期**：`d` 有三种精度（YYYY / YYYY-MM / YYYY-MM-DD）。
     旧实现在浏览器里拼成 `"2026-11T00:00:00Z"` → Invalid Date
     ⇒ 静默**永不**显示 NEW（实测 40 篇）。

⚠️ 这类 bug 靠"打开页面看一眼"是发现不了的（看起来正常）。
   所以把判据从浏览器搬到数据侧（scripts/export.py），再用测试钉住。

另外守一条**一致性问题**：生成器与已提交的 papers.json 必须同代
（防止"改了生成器但忘了重新导出"）。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import unittest
from datetime import date, datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE_DATA = ROOT / "docs" / "data" / "papers.json"

# 固定基准日 —— 判据结果必须与"跑测试的当天"无关，否则测试会随日期飘
REF = date(2026, 10, 8)


def _load_export():
    """按文件路径加载 scripts/export.py（scripts/ 不是包）。"""
    spec = importlib.util.spec_from_file_location(
        "azn_export", ROOT / "scripts" / "export.py")
    assert spec and spec.loader, "无法加载 scripts/export.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ex = _load_export()


class TestParsePartialDate(unittest.TestCase):
    """三种精度都要能解析，并标出精度 —— 判据要靠精度分流。"""

    def test_day_precision(self):
        self.assertEqual(ex.parse_partial_date("2026-10-03"),
                         (date(2026, 10, 3), "day"))

    def test_month_precision(self):
        self.assertEqual(ex.parse_partial_date("2026-11"),
                         (date(2026, 11, 1), "month"))

    def test_year_precision(self):
        self.assertEqual(ex.parse_partial_date("2026"),
                         (date(2026, 1, 1), "year"))

    def test_surrounding_whitespace_tolerated(self):
        self.assertEqual(ex.parse_partial_date("  2026-10-03 "),
                         (date(2026, 10, 3), "day"))

    def test_unparseable_returns_none(self):
        """脏数据不能抛异常（导出流程会整体挂掉），要温和地返回 (None, "")。"""
        for bad in ("", None, "n/a", "2026-13-01", "2026-02-30",
                    "2026/10/03", "Oct 2026", "2026-1"):
            with self.subTest(bad=bad):
                self.assertEqual(ex.parse_partial_date(bad), (None, ""))


class TestIsFresh(unittest.TestCase):
    def test_today_is_fresh(self):
        self.assertTrue(ex.is_fresh("2026-10-08", REF))

    def test_window_boundary_inclusive(self):
        """正好落在窗口边上要算「新」（否则边界行为随实现细节漂移）。"""
        n = ex.NEW_DAYS
        edge = date.fromordinal(REF.toordinal() - n)
        self.assertTrue(ex.is_fresh(edge.isoformat(), REF))
        self.assertFalse(ex.is_fresh(
            date.fromordinal(REF.toordinal() - n - 1).isoformat(), REF))

    def test_future_dates_are_not_fresh(self):
        """⭐ 回归：未来日期必须**不算**新 —— 否则 NEW 永远不消失。"""
        for fut in ("2026-10-09", "2026-11", "2027", "2030-01-01"):
            with self.subTest(fut=fut):
                self.assertFalse(ex.is_fresh(fut, REF))

    def test_year_precision_is_never_fresh(self):
        """精度太粗 → 不可判定。标 NEW 会误导（旧论文被当成新论文）。"""
        for y in ("2025", "2026", "2027"):
            with self.subTest(y=y):
                self.assertFalse(ex.is_fresh(y, REF))

    def test_current_month_precision_is_fresh(self):
        """⭐ 当月精度必须算新：期刊给「2026-10」时，月末还在未来，
        但这条论文确实是本月的 —— 不能因为"夹到月末"而变成未来日期。"""
        self.assertTrue(ex.is_fresh("2026-10", REF))

    def test_previous_month_precision_is_fresh(self):
        """⭐ 回归（旧规则会漏）：9 月号按月初算 = 37 天前 → 已过期，
        但它可能 9/28 才上线。月精度取**月末**才不会系统性漏掉新论文。"""
        self.assertTrue(ex.is_fresh("2026-09", REF))

    def test_two_months_back_is_stale(self):
        self.assertFalse(ex.is_fresh("2026-08", REF))

    def test_unparseable_is_not_fresh(self):
        self.assertFalse(ex.is_fresh("", REF))
        self.assertFalse(ex.is_fresh(None, REF))


def _row(**kw):
    base = {
        "title": "T", "authors": [], "published_date": "2026-10-01",
        "venue_short": "arXiv", "venue_kind": "preprint", "category": "Other",
        "topics": [], "url": "", "pdf_url": "", "code_url": "",
        "project_url": "", "doi": "", "arxiv_id": "", "citations": 0,
        "open_access": False, "hotness": 0, "abstract": "", "tldr": "",
        "sources": [], "year": 2026, "venue_year": 2026,
    }
    base.update(kw)
    return base


class TestBuildSiteJson(unittest.TestCase):
    """⭐ `now` 注入是刻意设计的：判据必须可复现，不能跟运行时钟走。"""

    NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def _json(self, rows):
        return ex.build_site_json(rows, now=self.NOW)

    def test_nw_flag_and_window_exposed(self):
        d = self._json([_row(published_date="2026-10-05")])
        self.assertEqual(d["papers"][0]["nw"], 1)
        self.assertEqual(d["new_days"], ex.NEW_DAYS)
        self.assertEqual(d["generated_at"], self.NOW.isoformat())

    def test_fresh_and_stale_rows(self):
        d = self._json([
            _row(published_date="2026-10-05"),   # 3 天前 → 新
            _row(published_date="2026-01-15"),   # 9 个月前 → 不新
        ])
        self.assertEqual([p["nw"] for p in d["papers"]], [1, 0])

    def test_future_row_never_new(self):
        """⭐ 回归测试（旧前端的实测 bug）：未来日期曾经永远显示 NEW。"""
        d = self._json([_row(published_date="2026-12-01"),
                        _row(published_date="2027")])
        self.assertEqual([p["nw"] for p in d["papers"]], [0, 0])

    def test_empty_date_row_never_new(self):
        d = self._json([_row(published_date="")])
        self.assertEqual(d["papers"][0]["nw"], 0)


class TestShippedSiteData(unittest.TestCase):
    """生成器与已提交的数据必须同代 —— 防「改了生成器忘了重新导出」。"""

    def setUp(self):
        if not SITE_DATA.exists():
            self.skipTest("docs/data/papers.json 不存在（未导出过）")
        self.data = json.loads(SITE_DATA.read_text(encoding="utf-8"))

    def test_new_days_present(self):
        self.assertIn("new_days", self.data)

    def test_every_paper_has_nw_flag(self):
        missing = [p.get("t") for p in self.data["papers"] if "nw" not in p]
        self.assertEqual(missing, [], "有论文缺 nw 字段 → 忘了重新导出")

    def test_no_future_paper_is_marked_new(self):
        """⭐ 端到端回归：真实数据里不能存在「未来日期 + NEW」的组合。"""
        gen = datetime.fromisoformat(self.data["generated_at"]).date()
        bad = []
        for p in self.data["papers"]:
            if not p.get("nw"):
                continue
            d, prec = ex.parse_partial_date(p.get("d", ""))
            if d is None:
                continue
            if prec == "day" and d > gen:
                bad.append((p["d"], p["t"]))
            elif prec == "month" and (d.year, d.month) > (gen.year, gen.month):
                bad.append((p["d"], p["t"]))
        self.assertEqual(bad, [], "未来日期的论文被标成了 NEW")


class TestReadmeAndSiteAgree(unittest.TestCase):
    """⭐ README 的「New in the last N days」与网站 NEW 徽章必须同源同数。

    为什么需要：这两个数字来自同一份数据、面向同一批读者。
    历史上它们是**两套实现**（README 用字符串比较、网站用 JS 时钟），
    于是同一个仓库对「新论文」给出两个答案，而且两边各自有各自的 bug。
    """

    NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.rows = [
            _row(published_date="2026-10-05"),   # 新
            _row(published_date="2026-09-20"),   # 新（18 天）
            _row(published_date="2026-01-15"),   # 旧
            _row(published_date="2026-11"),      # 未来月份
            _row(published_date="2027"),         # 只有年份
            _row(published_date=""),             # 空
        ]

    def _site_new_count(self):
        d = ex.build_site_json(self.rows, now=self.NOW)
        return sum(p["nw"] for p in d["papers"])

    def _readme_new_count(self):
        md = ex.build_readme(self.rows, now=self.NOW)
        m = re.search(r"New in the last (\d+) days\*\*: \*\*(\d+)\*\*", md)
        self.assertIsNotNone(m, "README 里找不到「New in the last N days」统计行")
        assert m is not None
        return int(m.group(2)), int(m.group(1))

    def test_counts_are_equal(self):
        site_new = self._site_new_count()
        readme_new, window = self._readme_new_count()
        self.assertEqual(readme_new, site_new)
        self.assertEqual(window, ex.NEW_DAYS)

    def test_window_label_comes_from_the_same_constant(self):
        """窗口数字不能被硬编码 —— 改 NEW_DAYS 后两边都要跟着变。"""
        _, window = self._readme_new_count()
        self.assertEqual(window, ex.NEW_DAYS)
        d = ex.build_site_json(self.rows, now=self.NOW)
        self.assertEqual(d["new_days"], ex.NEW_DAYS)


if __name__ == "__main__":
    unittest.main()
