"""配置模型 —— 把 TOML 变成**有类型、可校验**的对象。

为什么用 dataclass 而不是直接传 dict：

1. **IDE/静态检查能给提示** —— `cfg.zotero.collection_root` 拼错会立刻发现，
   `cfg["zotero"]["collectoin_root"]` 要等运行时。
2. **默认值集中在一处** —— 不必在每个使用点写 `get(k, default)`。
3. **frozen=True** —— 配置在进程内不可变，避免某个函数偷偷改全局配置
   （这类 bug 极难查：行为取决于调用顺序）。
4. **校验前置** —— 非法值在 `load()` 时就炸，而不是在推送第 50 篇论文时炸。
"""

from __future__ import annotations

import pathlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from zenav.errors import ConfigError

# ── 合法取值（用于校验，避免拼写错误静默生效） ─────────────────────────
VALID_LIBRARY_TYPES = ("user", "group")
VALID_TRANSPORTS = ("stdio", "http")
VALID_ITEM_TYPES = ("conferencePaper", "journalArticle", "preprint", "report")


@dataclass(frozen=True)
class SourceSpec:
    """单个数据源的配置。"""

    name: str
    enabled: bool = True
    interval: float = 0.8
    api_key_env: str = ""
    group: str = "sources"          # sources | extra_sources
    options: Mapping[str, Any] = field(default_factory=dict)

    @property
    def requires_key(self) -> bool:
        return bool(self.api_key_env)

    @property
    def is_extra(self) -> bool:
        return self.group == "extra_sources"


@dataclass(frozen=True)
class SourcesConfig:
    """全部数据源（采集层与共用）。"""

    sources: Mapping[str, SourceSpec]
    extra_sources: Mapping[str, SourceSpec]
    default_enabled: bool = True
    default_interval: float = 0.8

    def all_specs(self) -> tuple[SourceSpec, ...]:
        return tuple(self.sources.values()) + tuple(self.extra_sources.values())

    def enabled(self, *, include_extra: bool = True) -> tuple[SourceSpec, ...]:
        specs = self.all_specs() if include_extra else tuple(self.sources.values())
        return tuple(s for s in specs if s.enabled)

    def interval_for(self, name: str) -> float:
        spec = self.sources.get(name) or self.extra_sources.get(name)
        return spec.interval if spec else self.default_interval

    def key_envs(self) -> tuple[str, ...]:
        """所有需要密钥的源，返回值非空的环境变量名（状态展示用）。"""
        return tuple(s.api_key_env for s in self.all_specs() if s.requires_key)


@dataclass(frozen=True)
class CatalogConfig:
    """论文目录数据来源。"""

    source: str = "docs/data/papers.json"
    cache_ttl: int = 300

    @property
    def is_remote(self) -> bool:
        return self.source.startswith(("http://", "https://"))

    @property
    def is_sqlite(self) -> bool:
        return self.source.endswith(".db")

    def resolve(self, root: pathlib.Path) -> str:
        """相对路径 → 基于仓库根解析；URL / 绝对路径原样返回。"""
        if self.is_remote:
            return self.source
        p = pathlib.Path(self.source)
        return str(p if p.is_absolute() else root / p)


@dataclass(frozen=True)
class ZoteroConfig:
    """Zotero 单向推送配置。

    ⭐ 只做"推"，不做"读"（Boss 2026-09-30 明确）：
    Zotero 官方客户端自带云同步，推上去本机自动就有了，
    所以不需要 Local API、不需要装插件。
    """

    enabled: bool = False
    api_base: str = "https://api.zotero.org"
    api_key_env: str = "ZOTERO_API_KEY"
    library_type: str = "user"
    library_id_env: str = "ZOTERO_LIBRARY_ID"
    collection_root: str = "Zero-Shot Navigation"
    create_subcollections: bool = True
    state_file: str = "data/zotero_state.json"
    push_interval: float = 0.6
    max_batch: int = 50
    default_item_type: str = "conferencePaper"

    def validate(self) -> None:
        if self.library_type not in VALID_LIBRARY_TYPES:
            raise ConfigError(
                f"zotero.library_type={self.library_type!r} 非法",
                hint=f"可选值：{' / '.join(VALID_LIBRARY_TYPES)}",
            )
        if self.default_item_type not in VALID_ITEM_TYPES:
            raise ConfigError(
                f"zotero.default_item_type={self.default_item_type!r} 非法",
                hint=f"可选值：{' / '.join(VALID_ITEM_TYPES)}",
            )
        if not self.collection_root.strip():
            raise ConfigError("zotero.collection_root 不能为空")
        if self.max_batch < 1:
            raise ConfigError("zotero.max_batch 必须 ≥ 1")


@dataclass(frozen=True)
class McpConfig:
    """MCP 服务配置。"""

    server_name: str = "zero-shot-nav"
    transport: str = "stdio"          # stdio | http
    http_host: str = "127.0.0.1"
    http_port: int = 8765
    auth_token_env: str = "ZENAV_MCP_TOKEN"

    def validate(self) -> None:
        if self.transport not in VALID_TRANSPORTS:
            raise ConfigError(
                f"mcp.transport={self.transport!r} 非法",
                hint=f"可选值：{' / '.join(VALID_TRANSPORTS)}",
            )
        if self.transport == "http" and self.http_host not in ("127.0.0.1", "localhost", "::1"):
            # ⚠️ 不是硬错误（确实有人要对内网暴露），但必须显式知道风险
            raise ConfigError(
                f"mcp.transport=http 且 http_host={self.http_host!r} 不是回环地址",
                hint=(
                    "对外暴露 MCP 服务前，请确认：\n"
                    f"     ① 已设置 {self.auth_token_env}（否则任何人都能调用）\n"
                    "     ② 前面有 HTTPS 反代（token 走明文 HTTP 会泄露）\n"
                    "     ③ 只想本机用的话，把 http_host 改回 127.0.0.1"
                ),
            )


@dataclass(frozen=True)
class AppConfig:
    """服务层总配置。由 `AppConfig.load()` 组装。"""

    root: pathlib.Path
    sources: SourcesConfig
    catalog: CatalogConfig
    zotero: ZoteroConfig
    mcp: McpConfig

    # ── 加载 ────────────────────────────────────────────────────────
    @classmethod
    def load(cls, root: str | pathlib.Path | None = None) -> AppConfig:
        """读 config/*.toml 并组装。缺文件则用内置默认值（不报错）。"""
        from zenav.config.loader import build_config  # 延迟导入避免循环

        return build_config(root)

    # ── 便捷属性 ────────────────────────────────────────────────────
    @property
    def catalog_path(self) -> str:
        return self.catalog.resolve(self.root)

    @property
    def zotero_state_path(self) -> pathlib.Path:
        p = pathlib.Path(self.zotero.state_file)
        return p if p.is_absolute() else self.root / p

    def summary(self) -> str:
        """人类可读的配置摘要（CLI `zenav config` 用，不含任何密钥值）。"""
        lines = [
            f"仓库根目录 : {self.root}",
            f"论文目录   : {self.catalog_path}"
            f"{'  (远程)' if self.catalog.is_remote else ''}",
            "",
            "数据源:",
        ]
        for spec in self.sources.all_specs():
            mark = "✅" if spec.enabled else "⬜"
            key = f"  🔑{spec.api_key_env}" if spec.requires_key else ""
            group = "聚合式" if spec.is_extra else "检索式"
            lines.append(
                f"  {mark} {spec.name:<16s} [{group}] 间隔 {spec.interval}s{key}"
            )
        lines += [
            "",
            "Zotero 单向推送:",
            f"  {'✅ 已启用' if self.zotero.enabled else '⬜ 未启用'}"
            f"  目录根：{self.zotero.collection_root}"
            f"  子目录：{'按分类自动建' if self.zotero.create_subcollections else '不建'}",
            "",
            "MCP:",
            f"  {self.mcp.server_name}  传输={self.mcp.transport}"
            + (f"  {self.mcp.http_host}:{self.mcp.http_port}"
               if self.mcp.transport == "http" else ""),
        ]
        return "\n".join(lines)
