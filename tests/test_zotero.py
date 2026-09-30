"""Zotero 单向推送测试（需求 5）—— 本套件是这次交付最关键的验证。

为什么重点测这里：
    · 推送会**写入用户的真实文献库**，出错代价高（重复条目、脏数据）
    · 幂等逻辑一旦退化，会在 Zotero 里产生成百上千条重复
    · 这些错误**不会报错**，只会安静地把库弄脏 —— 所以必须有测试兜住
"""

from __future__ import annotations

import json
import pathlib
import unittest

from tests._support import FakeCatalog, FakeZoteroClient, TempDir, make_paper
from zenav.config.models import ZoteroConfig
from zenav.errors import ConfigError
from zenav.services.zotero import (
    PushState,
    ZoteroClient,
    ZoteroPushService,
    _creator,
    _subcollection_name,
)


def make_service(papers, tmp: pathlib.Path, client=None, **cfg_over):
    """装配一个用假客户端/真状态文件的服务。"""
    cfg = ZoteroConfig(enabled=True, state_file=str(tmp / "state.json"), **cfg_over)
    cfg.validate()
    return ZoteroPushService(
        catalog=FakeCatalog(papers),
        config=cfg,
        state=PushState.load(tmp / "state.json", library="user:999"),
        client=client if client is not None else FakeZoteroClient(),
    )


# ══════════════════════════════════════════════════════════════════════
#  ZoteroClient：构造与参数校验
# ══════════════════════════════════════════════════════════════════════
class TestZoteroClientValidation(unittest.TestCase):
    def test_rejects_non_numeric_library_id(self):
        """⚠️ library_id 是数字 userID，不是用户名 —— 必须早报错。"""
        with self.assertRaises(ConfigError) as ctx:
            ZoteroClient("https://api.zotero.org", "key", "Merlincs")
        self.assertIn("数字", str(ctx.exception))

    def test_rejects_bad_library_type(self):
        with self.assertRaises(ConfigError):
            ZoteroClient("https://api.zotero.org", "key", "123", library_type="org")

    def test_url_prefix(self):
        c = ZoteroClient("https://api.zotero.org/", "k", "123", "user")
        self.assertEqual(c._prefix, "https://api.zotero.org/users/123")
        g = ZoteroClient("https://api.zotero.org", "k", "456", "group")
        self.assertEqual(g._prefix, "https://api.zotero.org/groups/456")


# ══════════════════════════════════════════════════════════════════════
#  条目映射：Paper → Zotero item
# ══════════════════════════════════════════════════════════════════════
class TestItemMapping(unittest.TestCase):
    def setUp(self):
        self.svc = make_service([make_paper()], pathlib.Path("/tmp"))

    def test_item_type_from_venue_kind(self):
        cases = {
            "journal": "journalArticle",
            "conference": "conferencePaper",
            "proceedings": "conferencePaper",
            "preprint": "preprint",
            "other": "report",
            "unknown": "preprint",
            "": "preprint",
        }
        for kind, expect in cases.items():
            with self.subTest(kind=kind):
                item = self.svc.to_zotero_item(make_paper(venue_kind=kind))
                self.assertEqual(item["itemType"], expect)

    def test_venue_name_goes_to_right_field(self):
        """⚠️ Zotero 对不同 itemType 用不同字段名，塞错会被静默忽略。"""
        conf = self.svc.to_zotero_item(make_paper(venue_kind="conference"))
        self.assertIn("proceedingsTitle", conf)
        self.assertNotIn("publicationTitle", conf)

        jrn = self.svc.to_zotero_item(make_paper(venue_kind="journal"))
        self.assertIn("publicationTitle", jrn)
        self.assertNotIn("proceedingsTitle", jrn)

    def test_creators_split(self):
        item = self.svc.to_zotero_item(make_paper(authors=("He, Yu", "Wang, Li")))
        self.assertEqual(item["creators"][0],
                         {"creatorType": "author", "firstName": "Yu", "lastName": "He"})
        self.assertEqual(item["creators"][1]["lastName"], "Wang")

    def test_collection_attached(self):
        item = self.svc.to_zotero_item(make_paper(), collection_key="COLL0001")
        self.assertEqual(item["collections"], ["COLL0001"])
        # 没有目录时该字段被省略（Zotero 视为"不在任何 collection"），
        # 而不是留一个空数组 —— 空值一律过滤掉，减少无效字段
        bare = self.svc.to_zotero_item(make_paper(), collection_key="")
        self.assertNotIn("collections", bare)

    def test_empty_values_dropped(self):
        """空字符串/空列表不该出现在 item 里（Zotero 会当成非法值）。"""
        item = self.svc.to_zotero_item(
            make_paper(url="", doi="", abstract="", topics=()))
        for key in ("url", "DOI", "abstractNote"):
            self.assertNotIn(key, item)

    def test_tags_from_topics(self):
        item = self.svc.to_zotero_item(make_paper(topics=("Zero-Shot", "VLM")))
        self.assertEqual(item["tags"], [{"tag": "Zero-Shot"}, {"tag": "VLM"}])

    def test_extra_carries_code_and_project(self):
        """Zotero 没有代码/项目页字段 → 必须落到 extra，否则信息丢失。"""
        item = self.svc.to_zotero_item(make_paper())
        self.assertIn("Code:", item["extra"])
        self.assertIn("awesome-zero-shot-navigation", item["extra"])


class TestCreatorHelper(unittest.TestCase):
    def test_comma_format(self):
        self.assertEqual(_creator("He, Yu"),
                         {"creatorType": "author", "lastName": "He", "firstName": "Yu"})

    def test_space_format(self):
        self.assertEqual(_creator("Yu He"),
                         {"creatorType": "author", "lastName": "He", "firstName": "Yu"})

    def test_single_name(self):
        self.assertEqual(_creator("Aristotle")["lastName"], "Aristotle")

    def test_empty(self):
        self.assertEqual(_creator("")["lastName"], "")

    def test_multi_space_name(self):
        got = _creator("van der Berg, Jan")
        self.assertEqual(got["lastName"], "van der Berg")
        self.assertEqual(got["firstName"], "Jan")


class TestSubcollectionName(unittest.TestCase):
    def test_slash_replaced(self):
        """⚠️ 分类名里含 "/"（如 "LLM / VLM Navigation Agents"）会造成路径歧义。"""
        got = _subcollection_name("LLM / VLM Navigation Agents", "root")
        self.assertNotIn("/", got)
        self.assertIn("VLM", got)

    def test_empty_falls_back(self):
        self.assertEqual(_subcollection_name("", "r"), "Other")


# ══════════════════════════════════════════════════════════════════════
#  推送编排
# ══════════════════════════════════════════════════════════════════════
class TestPushFlow(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        """⭐ 演练必须**完全不写入**（条目、目录、状态文件都不许动）。

        ⚠️ 这条测试是从一个真 bug 来的：早先实现在 dry_run 时也调了
        `_ensure_collections()`，导致"演练"真的在用户库里建了目录
        （本地后端还会弹出授权对话框）。原来的断言只看 items 与状态文件，
        **漏掉了 collections**，所以没抓到。
        → 现在把"目录也不能动"和"不能触发授权"都写进断言。
        """
        with TempDir() as tmp:
            client = FakeZoteroClient()
            svc = make_service([make_paper()], tmp, client=client)
            report = svc.push(dry_run=True)
            self.assertEqual(report.pushed, 0)
            self.assertEqual(report.candidates, 1)      # 候选算了
            self.assertEqual(len(client.items), 0)      # 没写条目
            self.assertEqual(client.collections, {}, "演练不该创建目录")
            self.assertFalse((tmp / "state.json").exists())
            # 应当**报告**将会建哪些目录，供人预览
            self.assertTrue(report.collections_planned, "应报告将建的目录")
            self.assertEqual(report.collections_created, [], "演练不该真建")

    def test_push_creates_collections_and_items(self):
        with TempDir() as tmp:
            client = FakeZoteroClient()
            papers = [
                make_paper(title="P1", doi="10.1/1"),
                make_paper(title="P2", doi="10.1/2"),
            ]
            svc = make_service(papers, tmp, client=client)
            report = svc.push(dry_run=False)

            self.assertEqual(report.pushed, 2)
            self.assertEqual(report.failed, 0)
            self.assertTrue(report.state_saved)
            self.assertIn("Zero-Shot Navigation", client.collections)
            self.assertEqual(len(client.items), 2)
            # 每个条目都挂到了子目录
            self.assertTrue(all(i["collections"] for i in client.items))

    def test_second_push_skips_already_pushed(self):
        """⭐ 幂等：第二次运行不能再写一遍（否则 Zotero 会重复）。"""
        with TempDir() as tmp:
            client = FakeZoteroClient()
            papers = [make_paper(title="P1", doi="10.1/1")]

            svc1 = make_service(papers, tmp, client=client)
            r1 = svc1.push(dry_run=False)
            self.assertEqual(r1.pushed, 1)

            # 新的 service 实例，从磁盘读状态（模拟"下次运行"）
            svc2 = make_service(papers, tmp, client=client)
            r2 = svc2.push(dry_run=False)

            self.assertEqual(r2.pushed, 0)
            self.assertEqual(r2.already, 1)
            self.assertEqual(r2.candidates, 0)
            self.assertEqual(len(client.items), 1, "不该重复写入")

    def test_state_file_persists_fingerprints(self):
        with TempDir() as tmp:
            svc = make_service([make_paper(title="P", doi="10.1/x")], tmp)
            svc.push(dry_run=False)
            raw = json.loads((tmp / "state.json").read_text(encoding="utf-8"))
            self.assertIn("doi:10.1/x", raw["pushed"])
            self.assertEqual(raw["library"], "user:999")

    def test_respects_max_batch(self):
        with TempDir() as tmp:
            papers = [make_paper(title=f"P{i}", doi=f"10.1/{i}") for i in range(20)]
            client = FakeZoteroClient()
            svc = make_service(papers, tmp, client=client, max_batch=5)
            report = svc.push(dry_run=False)
            self.assertEqual(report.pushed, 5)
            self.assertEqual(len(client.items), 5)

    def test_category_filter(self):
        with TempDir() as tmp:
            papers = [
                make_paper(title="A", doi="10.1/a", category="VLN"),
                make_paper(title="B", doi="10.1/b", category="ObjectNav"),
            ]
            client = FakeZoteroClient()
            svc = make_service(papers, tmp, client=client)
            report = svc.push(category="VLN", dry_run=False)
            self.assertEqual(report.pushed, 1)
            self.assertEqual(client.items[0]["title"], "A")

    def test_since_days_filter(self):
        with TempDir() as tmp:
            papers = [
                make_paper(title="New", doi="10.1/n", date="2026-09-30"),
                make_paper(title="Old", doi="10.1/o", date="2020-01-01"),
            ]
            client = FakeZoteroClient()
            svc = make_service(papers, tmp, client=client)
            report = svc.push(since_days=30, dry_run=False)
            self.assertEqual(report.pushed, 1)
            self.assertEqual(client.items[0]["title"], "New")

    def test_partial_failure_does_not_lose_rest(self):
        """一篇失败不该影响其它篇 —— 且失败的不能记进状态（下次会重试）。"""
        with TempDir() as tmp:
            client = FakeZoteroClient(fail_titles=("Bad",))
            papers = [
                make_paper(title="Good1", doi="10.1/g1"),
                make_paper(title="Bad", doi="10.1/bad"),
                make_paper(title="Good2", doi="10.1/g2"),
            ]
            svc = make_service(papers, tmp, client=client)
            report = svc.push(dry_run=False)

            self.assertEqual(report.pushed, 2)
            self.assertEqual(report.failed, 1)
            state = PushState.load(tmp / "state.json")
            self.assertTrue(state.is_pushed("doi:10.1/g1"))
            self.assertFalse(state.is_pushed("doi:10.1/bad"), "失败的不能被记为已推")
            self.assertTrue(state.is_pushed("doi:10.1/g2"))

    def test_upstream_failure_counted_not_crash(self):
        with TempDir() as tmp:
            client = FakeZoteroClient()
            client.fail_next_items()
            svc = make_service([make_paper(doi="10.1/1")], tmp, client=client)
            report = svc.push(dry_run=False)
            self.assertEqual(report.pushed, 0)
            self.assertEqual(report.failed, 1)

    def test_state_marks_by_fingerprint_not_count(self):
        """⚠️ 关键回归：状态必须按"论文指纹"记，不能按"第几条"记。

        早期实现用批内偏移量对齐论文与写入结果，分类一变就错位，
        导致 A 论文的 itemKey 被记到 B 论文头上 → 下次 B 不会被推、A 会重复推。
        """
        with TempDir() as tmp:
            client = FakeZoteroClient()
            papers = [
                make_paper(title="X", doi="10.1/x", category="CatA"),
                make_paper(title="Y", doi="10.1/y", category="CatB"),
            ]
            svc = make_service(papers, tmp, client=client)
            svc.push(dry_run=False)

            state = PushState.load(tmp / "state.json")
            key_x = state.pushed["doi:10.1/x"]["k"]
            key_y = state.pushed["doi:10.1/y"]["k"]
            self.assertNotEqual(key_x, key_y)
            # 键与论文应对应（X 排在前面，应拿到先创建的 key）
            self.assertLess(key_x, key_y)


class TestCollectionHandling(unittest.TestCase):
    def test_subcollections_disabled(self):
        with TempDir() as tmp:
            client = FakeZoteroClient()
            svc = make_service([make_paper(category="CatA")], tmp, client=client,
                               create_subcollections=False)
            svc.push(dry_run=False)
            self.assertEqual(len(client.collections), 1, "只该有顶层目录")
            self.assertIn("Zero-Shot Navigation", client.collections)

    def test_existing_collection_reused(self):
        """已存在的目录要复用，不能重复创建（否则 Zotero 里一堆重名目录）。"""
        with TempDir() as tmp:
            client = FakeZoteroClient()
            client.collections["Zero-Shot Navigation"] = "COLLPRE"
            svc = make_service([make_paper()], tmp, client=client)
            report = svc.push(dry_run=False)
            self.assertEqual(client.collections["Zero-Shot Navigation"], "COLLPRE")
            self.assertNotIn("Zero-Shot Navigation", report.collections_created)

    def test_collection_root_customizable(self):
        with TempDir() as tmp:
            client = FakeZoteroClient()
            svc = make_service([make_paper()], tmp, client=client,
                               collection_root="我的论文")
            svc.push(dry_run=False)
            self.assertIn("我的论文", client.collections)


class TestPushState(unittest.TestCase):
    def test_load_missing_file(self):
        with TempDir() as tmp:
            st = PushState.load(tmp / "nope.json", library="user:1")
            self.assertEqual(st.pushed, {})
            self.assertEqual(st.library, "user:1")

    def test_corrupt_file_recovers(self):
        """⚠️ 状态损坏必须能自愈（否则每次运行都崩，推送完全停摆）。"""
        with TempDir() as tmp:
            p = tmp / "state.json"
            p.write_text("{ this is not json", encoding="utf-8")
            st = PushState.load(p, library="user:1")
            self.assertEqual(st.pushed, {})
            self.assertTrue((tmp / "state.json.corrupt").exists())

    def test_save_is_atomic(self):
        """不留 .tmp 残留（原子写：写临时文件后 replace）。"""
        with TempDir() as tmp:
            st = PushState.load(tmp / "state.json")
            st.mark("doi:1", "ITEM1")
            st.save()
            self.assertTrue((tmp / "state.json").exists())
            self.assertFalse((tmp / "state.json.tmp").exists())

    def test_save_is_idempotent_and_accumulates(self):
        with TempDir() as tmp:
            st = PushState.load(tmp / "state.json")
            st.mark("doi:1", "I1")
            st.save()
            st.mark("doi:2", "I2")
            st.save()
            again = PushState.load(tmp / "state.json")
            self.assertEqual(len(again.pushed), 2)

    def test_stats(self):
        st = PushState.load(pathlib.Path("/tmp/nonexistent-state.json"))
        st.mark("a", "K1")
        st.set_collection("Cat", "C1")
        self.assertEqual(st.stats, {"pushed_items": 1, "known_collections": 1})


class TestStatusReport(unittest.TestCase):
    def test_status_shape(self):
        with TempDir() as tmp:
            client = FakeZoteroClient()
            svc = make_service([make_paper()], tmp, client=client)
            st = svc.status()
            self.assertTrue(st["connection"]["ok"])
            self.assertEqual(st["catalog_total"], 1)
            self.assertEqual(st["remaining"], 1)

    def test_status_survives_connection_error(self):
        """连接失败时 status() 不该抛异常（否则 Agent 拿不到诊断信息）。"""
        class Boom(FakeZoteroClient):
            def check_access(self):
                raise RuntimeError("network down")

        with TempDir() as tmp:
            svc = make_service([make_paper()], tmp, client=Boom())
            st = svc.status()
            self.assertFalse(st["connection"]["ok"])
            self.assertIn("network down", st["connection"]["error"])

    def test_report_summary_text(self):
        with TempDir() as tmp:
            svc = make_service([make_paper()], tmp)
            text = svc.push(dry_run=True).summary()
            self.assertIn("演练", text)


if __name__ == "__main__":
    unittest.main()
