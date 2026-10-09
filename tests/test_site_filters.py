"""网站/服务层「筛选能力」的守门测试（2026-10-09）。

覆盖三件这次新增的能力，防止以后被无意改坏：

  ① 数据侧导出 `venues_top`（顶会/顶刊清单）—— 前端快捷筛选依赖它
  ② 服务层 `has_project` / `new_only` 两个筛选
  ③ ⭐ **CLI / MCP / 服务层三层参数对齐** —— 新增筛选最常见的漏法是
     "服务层加了、但 CLI 或 MCP 没透传"，功能静默不可用（不报错）。
"""

from __future__ import annotations

import importlib.util
import inspect
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader, f"无法加载 {rel}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestVenuesTopExported(unittest.TestCase):
    """顶会清单必须由导出侧产出 —— 前端不做判据。

    为什么：判断"哪些会议算顶会"用的是 `venue_tier == "A"`，
    而 tier 的判定逻辑在 scripts/venues.py。前端再实现一遍必然漂移。
    """

    def setUp(self):
        self.ex = _load("azn_export_vt", "scripts/export.py")

    def _row(self, venue, tier):
        return {
            "title": f"T-{venue}", "authors": [], "published_date": "2026-10-01",
            "venue_short": venue, "venue_kind": "conference", "venue_tier": tier,
            "category": "Other", "topics": [], "url": "", "pdf_url": "",
            "code_url": "", "project_url": "", "doi": "", "arxiv_id": "",
            "citations": 0, "open_access": False, "hotness": 0, "abstract": "",
            "tldr": "", "sources": [], "year": 2026, "venue_year": 2026,
        }

    def test_only_tier_a_in_venues_top(self):
        d = self.ex.build_site_json([
            self._row("CoRL", "A"), self._row("CoRL", "A"),
            self._row("Sensors", "B"), self._row("Some Journal", ""),
        ])
        self.assertEqual(d["venues_top"], {"CoRL": 2})
        # 全量 venues 仍然包含非顶会
        self.assertIn("Sensors", d["venues"])
        self.assertIn("Some Journal", d["venues"])

    def test_venues_top_sorted_desc(self):
        d = self.ex.build_site_json([
            self._row("ICRA", "A"), self._row("NeurIPS", "A"),
            self._row("NeurIPS", "A"), self._row("NeurIPS", "A"),
        ])
        self.assertEqual(list(d["venues_top"].keys()), ["NeurIPS", "ICRA"])

    def test_empty_venue_excluded(self):
        d = self.ex.build_site_json([self._row("", "A")])
        self.assertEqual(d["venues_top"], {})


class TestShippedVenuesTop(unittest.TestCase):
    def setUp(self):
        import json
        p = ROOT / "docs" / "data" / "papers.json"
        if not p.exists():
            self.skipTest("papers.json 不存在")
        self.data = json.loads(p.read_text(encoding="utf-8"))

    def test_field_present_and_nonempty(self):
        self.assertIn("venues_top", self.data)
        self.assertTrue(self.data["venues_top"], "顶会清单为空 → 忘了重新导出")

    def test_every_top_venue_is_tier_a(self):
        """⭐ 反向核对：清单里的每个会议，在 papers 里必须真的都是 tier A。"""
        top = set(self.data["venues_top"])
        bad = {}
        for p in self.data["papers"]:
            v = p.get("v") or ""
            if v in top and p.get("vt") != "A":
                bad[v] = bad.get(v, 0) + 1
        self.assertEqual(bad, {}, f"这些会议混进了非 A 档论文: {bad}")

    def test_counts_match_paper_records(self):
        cnt = {}
        for p in self.data["papers"]:
            if p.get("vt") == "A" and p.get("v"):
                cnt[p["v"]] = cnt.get(p["v"], 0) + 1
        self.assertEqual(self.data["venues_top"], cnt)


class TestServiceFilters(unittest.TestCase):
    """服务层的 has_project / new_only 必须真的过滤（不是只收参数）。"""

    def setUp(self):
        import sys
        sys.path.insert(0, str(ROOT))
        # ⚠️ 必须用正常 import（不能按文件路径 exec_module）——
        #    `@dataclass` 会在 sys.modules 里回查宿主模块，手工加载会炸。
        from zenav.domain.paper import Paper
        self.Paper = Paper

    def _papers(self, **kw):
        base = dict(title="T", category="Other", topics=())
        base.update(kw)
        return self.Paper(**base)

    def test_is_new_parsed_from_short_key(self):
        """papers.json 用短键 `nw` —— 领域模型必须能从它读出来。"""
        p = self.Paper.from_record({"t": "X", "nw": 1})
        self.assertTrue(p.is_new)
        p2 = self.Paper.from_record({"t": "X", "nw": 0})
        self.assertFalse(p2.is_new)
        p3 = self.Paper.from_record({"t": "X"})       # 缺字段
        self.assertFalse(p3.is_new)

    def test_is_new_in_serialized_output(self):
        p = self._papers(is_new=True)
        self.assertTrue(p.to_dict()["is_new"])
        self.assertTrue(p.to_brief()["is_new"])


class TestFilterParamsAlignedAcrossLayers(unittest.TestCase):
    """⭐⭐ 三层参数对齐 —— 新增筛选最容易漏的一环。

    场景：服务层加了 `new_only`，但 CLI 忘了 `--new-only` 或 MCP 忘了透传
    ⇒ 功能对用户**静默不可用**（不报错、只是筛不出东西）。
    这个测试把三层参数名钉在一起。
    """

    def setUp(self):
        import sys
        sys.path.insert(0, str(ROOT))

    def _service_params(self):
        from zenav.services.papers import PaperService
        sig = inspect.signature(PaperService.search)
        return set(sig.parameters)

    def test_service_has_all_filter_params(self):
        params = self._service_params()
        for name in ("has_code", "has_project", "new_only", "open_access",
                     "min_citations", "venue", "topic", "category"):
            with self.subTest(param=name):
                self.assertIn(name, params)

    def test_cli_passes_every_bool_filter_through(self):
        """CLI 的 _cmd_search 源码里必须出现每个布尔筛选的名字。"""
        src = (ROOT / "zenav" / "interfaces" / "cli.py").read_text(encoding="utf-8")
        start = src.index("def _cmd_search")
        body = src[start:src.index("\ndef ", start + 10)]
        for name in ("has_code", "has_project", "new_only", "open_access"):
            with self.subTest(param=name):
                self.assertIn(name, body, f"CLI 没把 {name} 透传给服务层")

    def test_cli_defines_flags_for_filters(self):
        src = (ROOT / "zenav" / "interfaces" / "cli.py").read_text(encoding="utf-8")
        for flag in ("--has-code", "--has-project", "--new-only", "--open-access"):
            with self.subTest(flag=flag):
                self.assertIn(f'"{flag}"', src, f"CLI 缺少 {flag} 开关")

    def test_mcp_exposes_filters(self):
        src = (ROOT / "zenav" / "interfaces" / "mcp_server.py").read_text(encoding="utf-8")
        start = src.index("def search_papers")
        # 工具函数签名 + 调用体
        body = src[start:start + 6000]
        for name in ("has_code", "has_project", "new_only", "open_access"):
            with self.subTest(param=name):
                self.assertIn(name, body, f"MCP 工具没有暴露 {name}")


class TestSiteHasGroupedView(unittest.TestCase):
    """网站的分组视图 / 顶会列表 —— 静态检查关键挂点存在。

    ⚠️ 这里只做"存在性"检查（HTML 无法在 Python 里跑）。
       真正的行为验证走浏览器实测（见技能里的 DOM 断言清单）。
    """

    def setUp(self):
        self.html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

    def test_topic_view_switch_present(self):
        self.assertIn('data-view="topic"', self.html)
        self.assertIn("renderGrouped", self.html)
        self.assertIn("groupByTopic", self.html)

    def test_top_venue_quick_list_present(self):
        self.assertIn('id="venlist"', self.html)
        self.assertIn("venues_top", self.html)

    def test_venue_select_grouped_by_optgroup(self):
        self.assertIn("<optgroup", self.html)

    def test_new_filter_and_note_present(self):
        self.assertIn('id="fnew"', self.html)
        self.assertIn("newnote", self.html)

    def test_url_state_and_shortcut_present(self):
        self.assertIn("syncURL", self.html)
        self.assertIn("applyURL", self.html)
        self.assertIn("replaceState", self.html)


if __name__ == "__main__":
    unittest.main()
