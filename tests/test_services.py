"""BibTeX 与检索服务测试。"""

from __future__ import annotations

import unittest

from tests._support import FakeCatalog, make_paper
from zenav.services.bibtex import (
    citation_key,
    entry_type,
    escape_bibtex,
    to_bibtex,
    to_bibtex_entry,
)
from zenav.services.papers import PaperService, render_overview_markdown


# ══════════════════════════════════════════════════════════════════════
#  BibTeX
# ══════════════════════════════════════════════════════════════════════
class TestEscape(unittest.TestCase):
    def test_escapes_specials(self):
        """⚠️ 不转义会让生成的 .bib 编译报错或静默丢字。"""
        got = escape_bibtex("50% of A & B_C #1")
        self.assertIn(r"\%", got)
        self.assertIn(r"\&", got)
        self.assertIn(r"\_", got)
        self.assertIn(r"\#", got)

    def test_plain_text_untouched(self):
        self.assertEqual(escape_bibtex("Normal Title"), "Normal Title")

    def test_handles_empty(self):
        self.assertEqual(escape_bibtex(""), "")


class TestEntryType(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(entry_type(make_paper(venue_kind="journal")), "article")
        self.assertEqual(entry_type(make_paper(venue_kind="conference")), "inproceedings")
        self.assertEqual(entry_type(make_paper(venue_kind="proceedings")), "inproceedings")
        self.assertEqual(entry_type(make_paper(venue_kind="preprint")), "misc")
        self.assertEqual(entry_type(make_paper(venue_kind="")), "misc")


class TestCitationKey(unittest.TestCase):
    def test_basic_shape(self):
        key = citation_key(make_paper(authors=("He, Yu",), year=2026,
                                      title="Zero Shot Navigation"))
        self.assertTrue(key.startswith("He2026"))
        self.assertNotIn(" ", key)

    def test_unique_on_collision(self):
        """撞车要加后缀 —— 否则生成的 .bib 有重复 key，BibTeX 会报错。"""
        used: set[str] = set()
        p = make_paper(authors=("He, Yu",), year=2026, title="Same Title Here")
        k1 = citation_key(p, used)
        k2 = citation_key(p, used)
        self.assertNotEqual(k1, k2)
        self.assertTrue(k2.startswith(k1))

    def test_stopwords_skipped(self):
        key = citation_key(make_paper(title="The a of for Navigation"))
        self.assertTrue(key.endswith("Navigation"))

    def test_no_authors(self):
        self.assertTrue(citation_key(make_paper(authors=(), year=2026)).startswith("anon2026"))

    def test_no_year(self):
        self.assertIn("nd", citation_key(make_paper(year=0)))


class TestEntryRendering(unittest.TestCase):
    def test_basic_entry(self):
        text = to_bibtex_entry(make_paper())
        self.assertTrue(text.startswith("@inproceedings{"))
        self.assertIn("title", text)
        self.assertIn("author", text)
        # 会议/期刊用**全称**写进 booktitle（venue_full 优先，缺失才用简称）
        self.assertIn("Conference on Robot Learning", text)

    def test_venue_falls_back_to_short(self):
        """没有全称时用简称 —— 不能因此丢字段。"""
        text = to_bibtex_entry(make_paper(venue_full="", venue_short="CoRL"))
        self.assertIn("booktitle", text)
        self.assertIn("CoRL", text)

    def test_authors_joined_with_and(self):
        text = to_bibtex_entry(make_paper(authors=("He, Yu", "Wang, Li")))
        self.assertIn("He, Yu and Wang, Li", text)

    def test_arxiv_fields(self):
        text = to_bibtex_entry(make_paper(arxiv_id="2609.00001"))
        self.assertIn("eprint", text)
        self.assertIn("archivePrefix", text)

    def test_doi_included(self):
        self.assertIn("doi", to_bibtex_entry(make_paper(doi="10.1/x")))

    def test_missing_fields_omitted(self):
        text = to_bibtex_entry(make_paper(doi="", arxiv_id="", citations=0,
                                          open_access=False, code_url=""))
        self.assertNotIn("eprint", text)
        self.assertNotIn("DOI", text)

    def test_multi_entries_have_unique_keys(self):
        papers = [make_paper(title="Same Title", doi=f"10.1/{i}") for i in range(3)]
        text = to_bibtex(papers)
        keys = [ln.split("{")[1].rstrip(",")
                for ln in text.splitlines() if ln.startswith("@")]
        self.assertEqual(len(keys), len(set(keys)), "cite key 必须唯一")

    def test_header_comment(self):
        text = to_bibtex([make_paper()], header="From test")
        self.assertTrue(text.startswith("% From test"))

    def test_renders_for_all_venue_kinds(self):
        """各种形态都要能渲染出可编译的条目（不能抛异常）。"""
        for kind in ("journal", "conference", "proceedings", "preprint",
                     "unknown", "other", ""):
            with self.subTest(kind=kind):
                text = to_bibtex_entry(make_paper(venue_kind=kind))
                self.assertIn("@", text)
                self.assertEqual(text.count("{"), text.count("}"))


# ══════════════════════════════════════════════════════════════════════
#  检索服务
# ══════════════════════════════════════════════════════════════════════
def build_service(papers=None) -> PaperService:
    if papers is None:
        papers = [
            make_paper(title="ZeroShot Nav A", doi="10.1/a", year=2026,
                       date="2026-09-01", category="ObjectNav", citations=10,
                       topics=("Zero-Shot",), code_url="https://g/a"),
            make_paper(title="ZeroShot Nav B", doi="10.1/b", year=2025,
                       date="2025-01-01", category="VLN", citations=50,
                       topics=("VLM",), code_url=""),
            make_paper(title="Preprint C", doi="10.1/c", year=2024,
                       date="2024-01-01", category="VLN", citations=1,
                       topics=(), venue_kind="preprint", venue_short="",
                       code_url=""),
        ]
    return PaperService(FakeCatalog(papers))


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()

    def test_no_filter_returns_all(self):
        self.assertEqual(self.svc.search().total_matched, 3)

    def test_keyword_and_semantics(self):
        """多个关键词是 AND —— 用 guaranteed_miss 验证它真的在过滤。"""
        self.assertEqual(self.svc.search("zeroshot").total_matched, 2)
        self.assertEqual(self.svc.search("zeroshot nav").total_matched, 2)
        self.assertEqual(self.svc.search("zeroshot zzzz").total_matched, 0)

    def test_category_partial_match(self):
        """宽容匹配：写片段也能命中完整分类名。"""
        self.assertEqual(self.svc.search(category="Nav").total_matched, 1)
        self.assertEqual(self.svc.search(category="VLN").total_matched, 2)

    def test_has_code_filter(self):
        self.assertEqual(self.svc.search(has_code=True).total_matched, 1)

    def test_topic_filter(self):
        self.assertEqual(self.svc.search(topic="VLM").total_matched, 1)
        self.assertEqual(self.svc.search(topic="vlm").total_matched, 1)  # 大小写不敏感

    def test_year_range(self):
        self.assertEqual(self.svc.search(year_from=2026).total_matched, 1)
        self.assertEqual(self.svc.search(year_to=2025).total_matched, 2)

    def test_limit_and_offset(self):
        first = self.svc.search(limit=1, sort="citations").papers[0]
        second = self.svc.search(limit=1, sort="citations", offset=1).papers[0]
        self.assertNotEqual(first.doi, second.doi)
        self.assertEqual(first.citations, 50)

    def test_sort_citations(self):
        got = [p.citations for p in self.svc.search(sort="citations").papers]
        self.assertEqual(got, sorted(got, reverse=True))

    def test_sort_date_default(self):
        got = [p.date for p in self.svc.search().papers]
        self.assertEqual(got, sorted(got, reverse=True))

    def test_invalid_sort_falls_back(self):
        """非法排序值不能炸 —— 静默回落到默认（Agent 可能乱传）。"""
        self.assertEqual(self.svc.search(sort="; DROP TABLE").total_matched, 3)

    def test_min_citations(self):
        self.assertEqual(self.svc.search(min_citations=10).total_matched, 2)

    def test_result_to_dict(self):
        d = self.svc.search(limit=2).to_dict()
        self.assertEqual(d["total_matched"], 3)
        self.assertEqual(d["returned"], 2)
        self.assertIn("papers", d)


class TestGet(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()

    def test_by_doi(self):
        got = self.svc.get("10.1/a")
        assert got is not None          # 也让类型检查器缩窄类型
        self.assertEqual(got.title, "ZeroShot Nav A")

    def test_by_doi_url(self):
        """支持粘贴完整 DOI URL（用户实际会这么干）。"""
        self.assertIsNotNone(self.svc.get("https://doi.org/10.1/a"))

    def test_by_title(self):
        self.assertIsNotNone(self.svc.get("ZeroShot Nav A"))

    def test_by_title_fragment(self):
        got = self.svc.get("Nav B")
        self.assertIsNotNone(got)
        self.assertEqual(got.doi, "10.1/b")

    def test_miss_returns_none(self):
        self.assertIsNone(self.svc.get("nothing matches this"))
        self.assertIsNone(self.svc.get(""))
        self.assertIsNone(self.svc.get("   "))


class TestLatest(unittest.TestCase):
    def test_days_window(self):
        got = build_service().latest(days=400, limit=10)
        self.assertGreaterEqual(got.total_matched, 1)

    def test_category_filter(self):
        got = build_service().latest(days=3650, category="VLN")
        self.assertTrue(all("VLN" in p.category for p in got.papers))


class TestCategoryOverview(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()

    def test_not_found(self):
        ov = self.svc.category_overview("NoSuchCategory")
        self.assertFalse(ov["found"])
        self.assertEqual(ov["total"], 0)

    def test_group_by_year(self):
        ov = self.svc.category_overview("VLN", group_by="year")
        self.assertTrue(ov["found"])
        self.assertEqual(ov["total"], 2)
        self.assertEqual(ov["group_by"], "year")
        self.assertTrue(all(g["group"].isdigit() for g in ov["groups"]))

    def test_group_by_topic(self):
        ov = self.svc.category_overview("VLN", group_by="topic")
        self.assertTrue(ov["groups"])

    def test_invalid_group_by_falls_back(self):
        ov = self.svc.category_overview("VLN", group_by="bogus")
        self.assertEqual(ov["group_by"], "year")

    def test_limit_per_group(self):
        papers = [make_paper(title=f"T{i}", doi=f"10.1/{i}", category="C",
                             year=2026) for i in range(5)]
        ov = build_service(papers).category_overview("C", limit_per_group=2)
        self.assertLessEqual(len(ov["groups"][0]["papers"]), 2)
        self.assertEqual(ov["groups"][0]["count"], 5)   # count 仍是真实总数

    def test_markdown_rendering(self):
        md = render_overview_markdown(self.svc.category_overview("VLN"))
        self.assertIn("# VLN", md)
        self.assertIn("| 年份 |", md)
        self.assertIn("ZeroShot Nav B", md)

    def test_markdown_not_found(self):
        md = render_overview_markdown(self.svc.category_overview("Nope"))
        self.assertIn("暂无论文", md)

    def test_markdown_escapes_pipes_in_title(self):
        """标题里的 | 会破坏表格 —— 必须转义。"""
        papers = [make_paper(title="A | B pipe", doi="10.1/p", category="C")]
        md = render_overview_markdown(build_service(papers).category_overview("C"))
        self.assertIn(r"A \| B pipe", md)


class TestStats(unittest.TestCase):
    def test_stats_shape(self):
        info = build_service().stats()
        for key in ("total", "categories", "topics", "venues", "tiers",
                    "years", "with_code", "with_doi", "open_access"):
            self.assertIn(key, info)
        self.assertEqual(info["total"], 3)

    def test_categories(self):
        self.assertEqual(build_service().categories(), {"ObjectNav": 1, "VLN": 2})


if __name__ == "__main__":
    unittest.main()
