"""领域模型测试：Paper 的构造、指纹、派生属性、序列化。"""

from __future__ import annotations

import unittest

from tests._support import make_paper
from zenav.domain.paper import Paper, normalize_title


class TestPaperConstruction(unittest.TestCase):
    def test_from_short_keys(self):
        """短键记录（papers.json 格式）要能正确还原。"""
        rec = {
            "t": "Title X", "a": ["A, B"], "d": "2026-01-02", "y": 2026,
            "v": "CoRL", "vk": "conference", "vt": "A", "vf": "Conf on Robot Learning",
            "c": "VLN", "tp": ["Zero-Shot"], "u": "http://u", "p": "http://p",
            "co": "http://c", "pr": "http://pr", "doi": "10.1/x", "arx": "2601.1",
            "cite": 7, "oa": 1, "hot": 3, "s": "Abstract", "tl": "TLDR",
            "src": ["openalex"],
        }
        p = Paper.from_record(rec)
        self.assertEqual(p.title, "Title X")
        self.assertEqual(p.authors, ("A, B",))
        self.assertEqual(p.year, 2026)
        self.assertEqual(p.venue_short, "CoRL")
        self.assertEqual(p.category, "VLN")
        self.assertEqual(p.topics, ("Zero-Shot",))
        self.assertTrue(p.open_access)
        self.assertEqual(p.citations, 7)
        self.assertEqual(p.sources, ("openalex",))

    def test_from_long_keys_also_works(self):
        """长键 dict 也要接受（SQLite catalog 走这条路）。"""
        p = Paper.from_record({"title": "T", "authors": ["X, Y"], "year": 2020})
        self.assertEqual((p.title, p.year), ("T", 2020))

    def test_unknown_fields_preserved_in_raw(self):
        """未知字段不能丢 —— 采集层加字段时服务层不该崩。"""
        p = Paper.from_record({"t": "T", "brand_new_field": 42})
        self.assertEqual(p.raw.get("brand_new_field"), 42)

    def test_rejects_non_dict(self):
        with self.assertRaises(TypeError):
            Paper.from_record(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_frozen(self):
        """领域对象不可变（避免被就地改坏）。"""
        p = make_paper()
        with self.assertRaises(Exception):
            p.title = "changed"  # type: ignore[misc]


class TestFingerprint(unittest.TestCase):
    """指纹是 Zotero 幂等的基石 —— 必须稳定、必须区分得开。"""

    def test_prefers_doi(self):
        p = make_paper(doi="10.1/ABC", arxiv_id="2609.1")
        self.assertEqual(p.fingerprint(), "doi:10.1/abc")   # DOI 大小写归一

    def test_falls_back_to_arxiv(self):
        p = make_paper(doi="", arxiv_id="2609.00001")
        self.assertEqual(p.fingerprint(), "arx:2609.00001")

    def test_falls_back_to_title_hash(self):
        """⚠️ 实测有 54 篇既无 DOI 也无 arXiv —— 这个分支必需。"""
        p = make_paper(doi="", arxiv_id="")
        fp = p.fingerprint()
        self.assertTrue(fp.startswith("ttl:"))
        self.assertEqual(len(fp), len("ttl:") + 16)

    def test_title_hash_ignores_punctuation_and_case(self):
        """标点/大小写差异不该产生两个指纹（否则 Zotero 会重复入库）。"""
        a = make_paper(doi="", arxiv_id="", title="Zero-Shot Navigation: A Survey!")
        b = make_paper(doi="", arxiv_id="", title="zero shot navigation a survey")
        self.assertEqual(a.fingerprint(), b.fingerprint())

    def test_different_papers_differ(self):
        a = make_paper(doi="10.1/a", arxiv_id="")
        b = make_paper(doi="10.1/b", arxiv_id="")
        self.assertNotEqual(a.fingerprint(), b.fingerprint())

    def test_stable_across_calls(self):
        p = make_paper(doi="", arxiv_id="")
        self.assertEqual(p.fingerprint(), p.fingerprint())


class TestDerivedProperties(unittest.TestCase):
    def test_is_published_vs_preprint(self):
        self.assertTrue(make_paper(venue_kind="conference").is_published)
        self.assertTrue(make_paper(venue_kind="journal").is_published)
        self.assertFalse(make_paper(venue_kind="preprint").is_preprint is False)
        self.assertTrue(make_paper(venue_kind="preprint").is_preprint)
        # ⚠️ 边界：venue_kind 为空也算 preprint（数据里有 75 篇 unknown）
        self.assertTrue(make_paper(venue_kind="").is_preprint)

    def test_best_link_priority(self):
        self.assertEqual(make_paper(pdf_url="P", url="U").best_link, "P")
        self.assertEqual(make_paper(pdf_url="", url="U").best_link, "U")
        self.assertEqual(
            make_paper(pdf_url="", url="", doi="10.1/x").best_link,
            "https://doi.org/10.1/x",
        )

    def test_authors_display_truncates(self):
        p = make_paper(authors=("A", "B", "C", "D", "E"))
        self.assertEqual(p.authors_display(limit=3), "A, B, C et al.")
        self.assertEqual(make_paper(authors=("A", "B")).authors_display(), "A, B")
        self.assertEqual(make_paper(authors=()).authors_display(), "—")

    def test_venue_display(self):
        self.assertEqual(make_paper().venue_display(), "CoRL 2026 [A]")
        self.assertEqual(
            make_paper(venue_short="", venue_kind="preprint").venue_display(),
            "Preprint",
        )


class TestSerialization(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        p = make_paper()
        again = Paper.from_record(p.to_dict())
        self.assertEqual(p.title, again.title)
        self.assertEqual(p.authors, again.authors)
        self.assertEqual(p.fingerprint(), again.fingerprint())

    def test_short_keys_output(self):
        """short_keys=True 应产出与 papers.json 一致的键（供网站/兼容用）。"""
        rec = make_paper().to_dict(short_keys=True)
        self.assertIn("t", rec)
        self.assertIn("arx", rec)
        self.assertNotIn("title", rec)

    def test_to_brief_excludes_abstract(self):
        """⚠️ Agent 上下文保护：精简输出绝不能带摘要全文。"""
        brief = make_paper(abstract="x" * 5000).to_brief()
        self.assertNotIn("abstract", brief)
        self.assertLess(len(str(brief)), 1500)


class TestNormalizeTitle(unittest.TestCase):
    def test_collapses_punctuation_and_space(self):
        self.assertEqual(normalize_title("A  B--C"), "a b c")
        self.assertEqual(normalize_title("  X:Y!  "), "x y")

    def test_keeps_chinese(self):
        self.assertIn("导航", normalize_title("零样本导航 Navigation"))

    def test_handles_empty(self):
        self.assertEqual(normalize_title(""), "")
        self.assertEqual(normalize_title(None), "")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
