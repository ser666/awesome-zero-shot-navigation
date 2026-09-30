"""论文目录 —— 服务层的数据访问层（DAL）。

为什么要抽象出 `PaperCatalog`（而不是到处 `json.load(...)`）：

    数据可能来自三个地方，而**上层逻辑不该关心是哪个**：
        · 本地文件        docs/data/papers.json   （开发 / 单机 Agent）
        · 远程 URL        https://..../papers.json （服务器部署，永远是最新的）
        · SQLite          data/papers.db           （本地要原始字段时）

    把差异关在这里，服务层只调 `catalog.load()`。
    将来加"从 Zotero 反向读"或"从别的仓库聚合"也只改这一层。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from zenav.config.models import CatalogConfig
from zenav.domain.paper import Paper
from zenav.errors import CatalogError
from zenav.log import get_logger

log = get_logger(__name__)

# SQLite 列 → Paper 字段（DB 用长键，JSON 用短键，这里补齐第三种表示）
_DB_COLUMNS = (
    "title", "authors", "year", "published_date", "venue", "abstract", "doi",
    "arxiv_id", "citations", "url", "pdf_url", "open_access", "category",
    "sources", "venue_short", "venue_tier", "venue_kind", "venue_year",
    "code_url", "project_url", "topics", "hotness", "tldr",
)
_DB_JSON_COLUMNS = ("authors", "sources", "topics")   # DB 里存 JSON 字符串的列


@dataclass
class CatalogData:
    """一次加载的完整结果（附统计，省得上层再算一遍）。"""

    papers: list[Paper]
    generated_at: str = ""
    categories: dict[str, int] = field(default_factory=dict)
    topics: dict[str, int] = field(default_factory=dict)
    venues: dict[str, int] = field(default_factory=dict)
    source_desc: str = ""
    loaded_at: float = field(default_factory=time.time)

    @property
    def total(self) -> int:
        return len(self.papers)

    def describe(self) -> str:
        return (
            f"{self.total} 篇论文 ｜ 来源：{self.source_desc}"
            + (f" ｜ 数据生成于 {self.generated_at[:19]}" if self.generated_at else "")
        )


class PaperCatalog(ABC):
    """数据源接口。新增数据来源 = 继承本类 + 在 `create_catalog` 注册。"""

    def __init__(self, cache_ttl: int = 300) -> None:
        self._ttl = cache_ttl
        self._cached: CatalogData | None = None

    @abstractmethod
    def _read(self) -> CatalogData:
        """真正去取数据（子类实现）。"""

    @property
    @abstractmethod
    def describe(self) -> str:
        """给日志/诊断用的一句话描述。"""

    def load(self, *, refresh: bool = False) -> CatalogData:
        """取数据（带进程内缓存）。

        Args:
            refresh: 忽略缓存，强制重取（远程源会真的发请求）。
        """
        if not refresh and self._cached is not None:
            age = time.time() - self._cached.loaded_at
            if self._ttl <= 0 or age < self._ttl:
                log.debug("命中缓存（%.1fs 前加载）", age)
                return self._cached
        data = self._read()
        self._cached = data
        return data

    @classmethod
    def from_config(cls, cfg: Any) -> PaperCatalog:
        return create_catalog(cfg)


def create_catalog(cfg: CatalogConfig, root: pathlib.Path | None = None) -> PaperCatalog:
    """工厂：按配置挑实现。

    ⚠️ 这是**唯一**决定"用哪种数据源"的地方 —— 新增一种就在这里加一个分支。
    """
    source = cfg.resolve(root) if root else cfg.source
    if cfg.is_remote:
        return RemoteJsonCatalog(source, cache_ttl=cfg.cache_ttl)
    if cfg.is_sqlite:
        return SqliteCatalog(source, cache_ttl=cfg.cache_ttl)
    return LocalJsonCatalog(source, cache_ttl=cfg.cache_ttl)


# ══════════════════════════════════════════════════════════════════════
#  实现 1：本地 JSON
# ══════════════════════════════════════════════════════════════════════
class LocalJsonCatalog(PaperCatalog):
    """读本地 papers.json（开发默认）。"""

    def __init__(self, path: str | pathlib.Path, cache_ttl: int = 300) -> None:
        super().__init__(cache_ttl)
        self.path = pathlib.Path(path)

    @property
    def describe(self) -> str:
        return f"本地 JSON · {self.path.name}"

    def _read(self) -> CatalogData:
        if not self.path.is_file():
            raise CatalogError(
                f"论文数据文件不存在：{self.path}",
                hint="先跑 `make export` 生成（或运行 `make update` 采集后导出）",
            )
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CatalogError(
                f"论文数据不是合法 JSON：{self.path}",
                hint=f"{exc}\n     重新生成：make export",
            ) from exc
        return _from_site_json(raw, self.describe)


# ══════════════════════════════════════════════════════════════════════
#  实现 2：远程 JSON（服务器部署用 —— 永远读到最新数据）
# ══════════════════════════════════════════════════════════════════════
class RemoteJsonCatalog(PaperCatalog):
    """读远程 papers.json。

    为什么服务器端优先用它（而不是 clone 仓库）：
      · Actions 每周更新后，服务端自动就是最新的，无需 git pull
      · 服务进程不需要有仓库写权限
    """

    def __init__(self, url: str, cache_ttl: int = 300) -> None:
        super().__init__(cache_ttl)
        self.url = url

    @property
    def describe(self) -> str:
        return f"远程 JSON · {self.url}"

    def _read(self) -> CatalogData:
        from zenav.infra.http import HttpClient     # 延迟导入，避免环

        client = HttpClient(user_agent="awesome-zero-shot-navigation/zenav")
        resp = client.get(self.url)
        if resp.status != 200:
            raise CatalogError(
                f"读取远程数据失败（HTTP {resp.status}）：{self.url}",
                hint="检查 URL 是否可访问（浏览器打开试一下）、网络是否通",
            )
        try:
            raw = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise CatalogError(
                f"远程数据不是合法 JSON：{self.url}",
                hint=str(exc),
            ) from exc
        return _from_site_json(raw, self.describe)


# ══════════════════════════════════════════════════════════════════════
#  实现 3：SQLite（本地开发，字段最全）
# ══════════════════════════════════════════════════════════════════════
class SqliteCatalog(PaperCatalog):
    """读 data/papers.db。

    什么时候用它：需要 DB 独有的字段（如 first_seen / relevance 命中规则），
    或想按 SQL 做复杂筛选时。日常用 JSON 就够。
    """

    def __init__(self, path: str | pathlib.Path, cache_ttl: int = 300) -> None:
        super().__init__(cache_ttl)
        self.path = pathlib.Path(path)

    @property
    def describe(self) -> str:
        return f"SQLite · {self.path.name}"

    def _read(self) -> CatalogData:
        if not self.path.is_file():
            raise CatalogError(f"数据库不存在：{self.path}",
                               hint="先跑 `make update` 采集")
        try:
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            raise CatalogError(f"打开数据库失败：{self.path}", hint=str(exc)) from exc
        try:
            cols = ", ".join(_DB_COLUMNS)
            rows = con.execute(
                f"SELECT {cols} FROM papers ORDER BY published_date DESC"
            ).fetchall()
        except sqlite3.Error as exc:
            raise CatalogError(
                f"查询 papers 表失败（表结构可能已变）：{self.path}",
                hint=str(exc),
            ) from exc
        finally:
            con.close()

        papers: list[Paper] = []
        for row in rows:
            rec = dict(row)
            for c in _DB_JSON_COLUMNS:
                rec[c] = _maybe_json_list(rec.get(c))
            # DB 的 venue 是全称、published_date 是日期 —— 映射到 Paper 的字段名
            rec["venue_full"] = rec.pop("venue", "")
            rec["date"] = rec.pop("published_date", "")
            rec["year"] = rec.get("venue_year") or rec.get("year") or 0
            papers.append(Paper.from_record(rec))

        return CatalogData(
            papers=papers,
            generated_at="",
            categories=_count(lambda p: [p.category], papers),
            topics=_count(lambda p: p.topics, papers),
            venues=_count(
                lambda p: [p.venue_short] if p.venue_short and p.venue_kind
                not in ("preprint", "proceedings", "other") else [],
                papers,
            ),
            source_desc=self.describe,
        )


# ══════════════════════════════════════════════════════════════════════
#  辅助
# ══════════════════════════════════════════════════════════════════════
def _maybe_json_list(value: Any) -> list:
    """DB 里 JSON 数组列可能是字符串，统一成 list。"""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            got = json.loads(value)
            return got if isinstance(got, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _count(key_fn, papers: list[Paper]) -> dict[str, int]:
    """按 key_fn 返回的多个键计数，降序排列。"""
    counter: Counter[str] = Counter()
    for p in papers:
        for k in key_fn(p):
            if k:
                counter[k] += 1
    return dict(sorted(counter.items(), key=lambda kv: -kv[1]))


def _from_site_json(raw: dict[str, Any], desc: str) -> CatalogData:
    """把 papers.json 的顶层结构转成 CatalogData。"""
    if not isinstance(raw, dict) or "papers" not in raw:
        raise CatalogError(
            "数据格式不对：顶层缺少 'papers' 字段",
            hint="这似乎不是本项目导出的 papers.json；确认 catalog.source 配置",
        )
    papers_raw = raw.get("papers") or []
    papers = [Paper.from_record(r) for r in papers_raw if isinstance(r, dict)]
    return CatalogData(
        papers=papers,
        generated_at=str(raw.get("generated_at", "")),
        # 统计优先用文件里已有的（采集层算过），缺失则现场算
        categories=dict(raw.get("categories") or _count(lambda p: [p.category], papers)),
        topics=dict(raw.get("topics") or _count(lambda p: p.topics, papers)),
        venues=dict(raw.get("venues") or _count(
            lambda p: [p.venue_short] if p.venue_short and p.venue_kind
            not in ("preprint", "proceedings", "other") else [], papers)),
        source_desc=desc,
    )
