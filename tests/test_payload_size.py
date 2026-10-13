"""首屏载荷守门测试（2026-10-13）。

背景（真实故障，Boss 反馈「看不到论文」）
----------------------------------------
`docs/data/papers.json` 里曾内嵌每篇的**完整摘要**（`s`，上限 1800 字）。
实测：835 篇 → 1.70 MB，其中摘要占 **71%**。

后果：页面首屏必须等整个 1.7 MB 下完才渲染。国内直连 GitHub Pages
实测 **3~18 秒**空白（前端一直停在 "loading…"、侧栏为空）——
用户看到的就是「没有论文」。

修法：拆成两个文件
    docs/data/papers.json    —— 列表/筛选/排序字段（首屏只等它）
    docs/data/abstracts.json —— 完整摘要，首屏渲染完后由前端**懒加载**

这组测试守的是：
  ① 导出侧：`s` 不许回到 papers.json；每篇要有 `id` 供关联
  ② 导出侧：摘要要真的产在 abstracts.json 里，且 id 能与 papers.json 对上
  ③ ⭐ 体积红线：papers.json 超过阈值即失败（含线上产物的实测值）
  ④ ⭐ 前端确实会去懒加载 —— 否则文件拆了但没人读，功能**静默丢失**
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE_JSON = ROOT / "docs" / "data" / "papers.json"
ABS_JSON = ROOT / "docs" / "data" / "abstracts.json"
INDEX_HTML = ROOT / "docs" / "index.html"

# ⚠️ 首屏载荷红线：实测 616 KB（旧版内嵌摘要时 1695 KB）
#    留足余量给正常增长（论文数增加），但绝不允许摘要被塞回来。
MAX_SITE_JSON_BYTES = 900 * 1024


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader, f"无法加载 {rel}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSiteJsonOmitsAbstracts(unittest.TestCase):
    """导出侧：完整摘要不得出现在首屏载荷里。"""

    def setUp(self):
        self.ex = _load("azn_export_payload", "scripts/export.py")

    def _row(self, title: str, abstract: str):
        return {
            "title": title, "authors": ["A, B"], "published_date": "2026-10-01",
            "venue_short": "CoRL", "venue_kind": "conference", "venue_tier": "A",
            "category": "Other", "topics": ["Zero-Shot"], "url": "http://u",
            "pdf_url": "", "code_url": "", "project_url": "",
            "doi": f"10.1/{title}", "arxiv_id": "", "citations": 0,
            "open_access": False, "hotness": 0,
            "abstract": abstract, "tldr": "Short tldr.",
            "sources": [], "year": 2026, "venue_year": 2026,
        }

    def test_abstract_not_in_site_json(self):
        d = self.ex.build_site_json([self._row("t1", "X" * 1800)])
        self.assertNotIn("s", d["papers"][0],
                         "完整摘要 `s` 又回到 papers.json 了 —— 首屏会重新变慢")

    def test_every_paper_has_id(self):
        d = self.ex.build_site_json([self._row("t1", "x"), self._row("t2", "")])
        for p in d["papers"]:
            self.assertTrue(p.get("id"), "每篇必须有 id —— abstracts.json 靠它关联")

    def test_paper_id_is_stable_and_unique(self):
        row = self._row("t1", "x")
        self.assertEqual(self.ex.paper_id(row), self.ex.paper_id(dict(row)),
                         "同一篇的 id 必须跨运行稳定")
        a = self.ex.build_site_json([self._row("t1", ""), self._row("t2", "")])
        ids = [p["id"] for p in a["papers"]]
        self.assertEqual(len(ids), len(set(ids)), "id 不能重复")

    def test_abstracts_file_covers_ids(self):
        rows = [self._row("t1", "Full abstract one."), self._row("t2", "")]
        d = self.ex.build_site_json(rows)
        abs_map = self.ex.build_abstracts(rows)
        # 有摘要的才收；空摘要的跳过（前端会退回 tldr）
        self.assertEqual(len(abs_map), 1)
        self.assertIn(d["papers"][0]["id"], abs_map)
        self.assertEqual(abs_map[d["papers"][0]["id"]], "Full abstract one.")

    def test_abstract_whitespace_normalised(self):
        rows = [self._row("t1", "a\n\n  b   c ")]
        abs_map = self.ex.build_abstracts(rows)
        self.assertEqual(list(abs_map.values()), ["a b c"])


class TestShippedPayloadSize(unittest.TestCase):
    """线上产物红线 —— 即使有人改了导出路径，这里也会拦住。"""

    def test_site_json_under_budget(self):
        self.assertTrue(SITE_JSON.exists(), f"缺少 {SITE_JSON}")
        n = SITE_JSON.stat().st_size
        self.assertLess(
            n, MAX_SITE_JSON_BYTES,
            f"docs/data/papers.json = {n/1024:.0f} KB，超过红线 "
            f"{MAX_SITE_JSON_BYTES/1024:.0f} KB —— 首屏会重新变慢（摘要是不是被塞回来了？）",
        )

    def test_no_full_abstract_key_in_shipped_json(self):
        d = json.loads(SITE_JSON.read_text(encoding="utf-8"))
        bad = sum(1 for p in d["papers"] if "s" in p)
        self.assertEqual(bad, 0, f"{bad} 篇仍带 `s`（完整摘要）字段")

    def test_abstracts_file_shipped_and_keyed(self):
        self.assertTrue(ABS_JSON.exists(), "abstracts.json 没有产出 —— 懒加载会 404")
        abs_map = json.loads(ABS_JSON.read_text(encoding="utf-8"))
        self.assertGreater(len(abs_map), 100, "摘要文件过小，疑似没产出")
        d = json.loads(SITE_JSON.read_text(encoding="utf-8"))
        ids = {p.get("id") for p in d["papers"]}
        missing = [k for k in list(abs_map)[:2000] if k not in ids]
        self.assertEqual(missing, [], "abstracts.json 的 key 与 papers.json 的 id 对不上")

    def test_frontend_actually_lazy_loads_abstracts(self):
        """文件拆了但前端不读 = 摘要功能静默丢失（最难发现的坏法）。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("abstracts.json", html, "前端没有引用 abstracts.json")
        self.assertIn("loadAbstracts", html, "前端缺少懒加载入口 loadAbstracts()")
        self.assertNotIn("p.s||", html, "前端仍在读已被移除的 `p.s` 字段")


class TestWeeklyWorkflowShipsAbstracts(unittest.TestCase):
    """周更工作流必须把 abstracts.json 一起提交并校验。

    ⚠️ 这是实测差点踩到的坑：`git add` 里漏掉新文件 ⇒ 本地一切正常、
    CI 也不报错，但线上那个文件**永远不更新**（新手最容易漏的一环）。
    """

    def setUp(self):
        self.wf = (ROOT / ".github" / "workflows" / "weekly-update.yml").read_text(
            encoding="utf-8")

    def test_abstracts_in_git_add(self):
        import re
        adds = re.findall(r"git add [^\n]*", self.wf)
        self.assertTrue(adds, "工作流里找不到 git add")
        self.assertTrue(any("abstracts.json" in a for a in adds),
                        f"git add 没包含 abstracts.json：{adds}")

    def test_workflow_guards_payload(self):
        self.assertIn("abstracts.json", self.wf)
        self.assertIn("papers.json 过大", self.wf,
                      "工作流缺少首屏载荷红线校验")


if __name__ == "__main__":
    unittest.main()
