"""测试用的替身与工具。

为什么需要它们：真正的 Zotero API 需要凭据 + 网络，不能出现在自动化测试里。
用替身（fake）就能把"推送逻辑"从头到尾测完 —— 包括幂等、分批、错误处理，
这些恰恰是最容易出错、也最值得测的部分。
"""

from __future__ import annotations

import pathlib
import tempfile
from typing import Any

from zenav.domain.paper import Paper
from zenav.infra.catalog import CatalogData, PaperCatalog

# ══════════════════════════════════════════════════════════════════════
#  数据替身
# ══════════════════════════════════════════════════════════════════════
def make_paper(**over: Any) -> Paper:
    """造一篇论文，只覆盖测试关心的字段。"""
    base: dict[str, Any] = {
        "title": "A Zero-Shot Object Navigation Method",
        "authors": ("He, Yu", "Zhou, Kang"),
        "date": "2026-09-01",
        "year": 2026,
        "venue_short": "CoRL",
        "venue_kind": "conference",
        "venue_tier": "A",
        "venue_full": "Conference on Robot Learning",
        "category": "Object-Goal Navigation (ObjectNav)",
        "topics": ("Zero-Shot", "Real Robot"),
        "url": "https://example.org/paper",
        "pdf_url": "https://example.org/paper.pdf",
        "code_url": "https://github.com/example/code",
        "doi": "10.1234/test.001",
        "arxiv_id": "2609.00001",
        "citations": 5,
        "open_access": True,
        "abstract": "We propose a method.",
        "tldr": "A short summary.",
        "sources": ("openalex",),
    }
    base.update(over)
    return Paper.from_record(base)


class FakeCatalog(PaperCatalog):
    """内存目录（不读文件）。"""

    def __init__(self, papers: list[Paper], describe: str = "fake") -> None:
        super().__init__(cache_ttl=0)
        self._papers = papers
        self._describe = describe

    @property
    def describe(self) -> str:
        return self._describe

    def _read(self) -> CatalogData:
        cats: dict[str, int] = {}
        tops: dict[str, int] = {}
        for p in self._papers:
            cats[p.category] = cats.get(p.category, 0) + 1
            for t in p.topics:
                tops[t] = tops.get(t, 0) + 1
        return CatalogData(
            papers=list(self._papers),
            generated_at="2026-09-30T00:00:00Z",
            categories=cats,
            topics=tops,
            source_desc=self._describe,
        )


class TempDir:
    """临时目录上下文（比 setUp/tearDown 更少样板）。"""

    def __enter__(self) -> pathlib.Path:
        self._ctx = tempfile.TemporaryDirectory()
        return pathlib.Path(self._ctx.name)

    def __exit__(self, *exc: Any) -> None:
        self._ctx.cleanup()


# ══════════════════════════════════════════════════════════════════════
#  Zotero 替身
# ══════════════════════════════════════════════════════════════════════
class FakeZoteroClient:
    """假的 Zotero 客户端 —— 记录所有调用，返回符合真实形状的响应。

    刻意**模仿真实 API 的坑**（这样测试才有效）：
      · 写入响应用 **字符串下标**（"0"、"1"），不是整数
      · 目录重名会复用（真实 Zotero 允许重名，但我们的代码应避免重复创建）
    """

    def __init__(self, fail_titles: tuple[str, ...] = ()) -> None:
        self.collections: dict[str, str] = {}      # name → key
        self.items: list[dict[str, Any]] = []
        self.calls: list[tuple[str, Any]] = []
        self.fail_titles = set(fail_titles)
        self._counter = 0
        self._fail_next_items = False

    # ── 模拟 API ────────────────────────────────────────────────────
    def check_access(self) -> dict[str, Any]:
        self.calls.append(("check_access", None))
        return {"ok": True, "library": "user:999", "total_collections": len(self.collections)}

    def list_collections(self) -> list[dict[str, Any]]:
        self.calls.append(("list_collections", None))
        return [
            {"key": k, "data": {"name": n, "parentCollection": False}}
            for n, k in self.collections.items()
        ]

    def create_collection(self, name: str, parent: str | None = None) -> str:
        self.calls.append(("create_collection", name))
        if name in self.collections:            # 幂等：重名复用
            return self.collections[name]
        self._counter += 1
        key = f"COLL{self._counter:04d}"
        self.collections[name] = key
        return key

    def create_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.calls.append(("create_items", len(items)))
        if self._fail_next_items:
            self._fail_next_items = False
            return []
        successful: dict[str, dict[str, Any]] = {}
        failed: dict[str, dict[str, Any]] = {}
        for i, item in enumerate(items):
            title = item.get("title", "")
            if title in self.fail_titles:
                failed[str(i)] = {"code": 400, "message": "simulated failure"}
                continue
            self._counter += 1
            key = f"ITEM{self._counter:04d}"
            self.items.append({**item, "key": key})
            successful[str(i)] = {"key": key, "version": 1}
        # ⚠️ 真实 Zotero 用字符串下标 —— 替身必须一致，否则测不出这个坑
        self.last_response = {"successful": successful, "failed": failed}
        return [
            {"index": int(i), "key": v["key"], "title": items[int(i)].get("title", "")}
            for i, v in successful.items()
        ]

    def fail_next_items(self) -> None:
        """让下一次 create_items 返回空（模拟上游故障）。"""
        self._fail_next_items = True
