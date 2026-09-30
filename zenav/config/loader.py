"""配置加载器：TOML → 带默认值合并 → ${ENV} 展开 → 校验 → dataclass。

处理顺序刻意固定为：

    读文件 → 合并默认值 → 展开 ${ENV} → 构造 dataclass → validate()

**为什么"展开 ENV"必须在"构造 dataclass"之前**：
否则 TOML 里的 `source = "${ZENAV_DATA_BASE}/papers.json"` 会原样进入对象，
到用的时候才发现是个字面量字符串 —— 这类问题只在部署环境暴露，很难查。

**容错策略**（刻意选择，方便"没配也能跑"）：
    · 缺 config/ 目录        → 全套内置默认值（服务层仍可用本地 papers.json）
    · 缺某个 section         → 该 section 用默认值
    · 出现未知键             → 保留进 `options`，只警告不报错
      （向前兼容：将来加的配置项，在旧代码上不会炸）
"""

from __future__ import annotations

import os
import pathlib
import re
import tomllib
from typing import Any

from zenav.config.models import (
    AppConfig,
    CatalogConfig,
    McpConfig,
    SourcesConfig,
    SourceSpec,
    ZoteroConfig,
)
from zenav.config.secrets import SecretStore, parse_env_file
from zenav.errors import ConfigError
from zenav.log import get_logger

log = get_logger(__name__)

# ${VAR} 或 ${VAR:-default}
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

SOURCES_FILE = "config/sources.toml"
SETTINGS_FILE = "config/settings.toml"
SECRETS_FILE = "config/secrets.env"


def find_project_root(start: str | pathlib.Path | None = None) -> pathlib.Path:
    """向上查找仓库根（含 config/ 或 .git 的目录）。

    为什么不用 `__file__.parent.parent`：那样在软链接、site-packages
    安装等场景下会得到错误路径。向上找标记文件是更稳的做法。
    """
    here = pathlib.Path(start or pathlib.Path.cwd()).resolve()
    for cand in (here, *here.parents):
        if (cand / ".git").exists() or (cand / "config").is_dir():
            return cand
    return here


def load_toml(path: str | pathlib.Path) -> dict[str, Any]:
    """读 TOML。文件不存在返回空 dict；语法错误抛 ConfigError（带行号）。"""
    p = pathlib.Path(path)
    if not p.is_file():
        log.debug("配置文件不存在，使用默认值: %s", p)
        return {}
    try:
        with p.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"配置文件语法错误：{p}",
            hint=f"{exc}\n     TOML 常见坑：字符串要用引号、section 用 [方括号]、"
                 "布尔是小写 true/false",
        ) from exc


def _expand(value: Any, label: str = "", strict: bool = False) -> Any:
    """递归展开全部字符串里的 ${ENV} / ${ENV:-default}。

    Args:
        strict: True 时，未设置且无默认值的变量直接报错；
                False 时保留原字面量（便于 `zenav config` 展示待配项）。
    """
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            got = os.environ.get(name)
            if got:
                return got
            if default is not None:
                return default
            if strict:
                raise ConfigError(
                    f"配置项 {label or '(未知)'} 引用了未设置的环境变量 ${{{name}}}",
                    hint=f"设置它：export {name}=...  （或在 config/secrets.env 里配置）",
                )
            return m.group(0)          # 原样保留，由上层决定是否必需
        return _ENV_RE.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand(v, f"{label}.{k}" if label else k, strict) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, f"{label}[]", strict) for v in value]
    return value


def _build_sources(raw: dict[str, Any]) -> SourcesConfig:
    """把 [defaults] / [sources.*] / [extra_sources.*] 变成 SourcesConfig。"""
    defaults = raw.get("defaults") or {}
    default_enabled = bool(defaults.get("enabled", True))
    default_interval = float(defaults.get("interval", 0.8))

    reserved = {"enabled", "interval", "api_key_env", "note"}

    def spec(name: str, body: dict[str, Any], group: str) -> SourceSpec:
        if not isinstance(body, dict):
            raise ConfigError(
                f"[{group}.{name}] 必须是一个表（section）",
                hint=f"写成：\n     [{group}.{name}]\n     enabled = true",
            )
        # ⚠️ 未知键保留进 options（向前兼容），只告警
        unknown = set(body) - reserved
        if unknown:
            log.debug("[%s.%s] 未知键（保留在 options）: %s",
                      group, name, ", ".join(sorted(unknown)))
        return SourceSpec(
            name=name,
            enabled=bool(body.get("enabled", default_enabled)),
            interval=float(body.get("interval", default_interval)),
            api_key_env=str(body.get("api_key_env", "") or ""),
            group=group,
            options={k: v for k, v in body.items() if k in unknown},
        )

    return SourcesConfig(
        sources={n: spec(n, b, "sources")
                 for n, b in (raw.get("sources") or {}).items()},
        extra_sources={n: spec(n, b, "extra_sources")
                       for n, b in (raw.get("extra_sources") or {}).items()},
        default_enabled=default_enabled,
        default_interval=default_interval,
    )


def _build_zotero(raw: dict[str, Any]) -> ZoteroConfig:
    z = raw.get("zotero") or {}
    cfg = ZoteroConfig(
        enabled=bool(z.get("enabled", False)),
        api_base=str(z.get("api_base", "https://api.zotero.org")).rstrip("/"),
        api_key_env=str(z.get("api_key_env", "ZOTERO_API_KEY")),
        library_type=str(z.get("library_type", "user")),
        library_id_env=str(z.get("library_id_env", "ZOTERO_LIBRARY_ID")),
        collection_root=str(z.get("collection_root", "Zero-Shot Navigation")),
        create_subcollections=bool(z.get("create_subcollections", True)),
        state_file=str(z.get("state_file", "data/zotero_state.json")),
        push_interval=float(z.get("push_interval", 0.6)),
        max_batch=int(z.get("max_batch", 50)),
        default_item_type=str(z.get("default_item_type", "conferencePaper")),
    )
    cfg.validate()
    return cfg


def _build_catalog(raw: dict[str, Any]) -> CatalogConfig:
    c = raw.get("catalog") or {}
    ttl = int(c.get("cache_ttl", 300))
    if ttl < 0:
        raise ConfigError("catalog.cache_ttl 不能为负")
    return CatalogConfig(
        source=str(c.get("source", "docs/data/papers.json")),
        cache_ttl=ttl,
    )


def _build_mcp(raw: dict[str, Any]) -> McpConfig:
    m = raw.get("mcp") or {}
    cfg = McpConfig(
        server_name=str(m.get("server_name", "zero-shot-nav")),
        transport=str(m.get("transport", "stdio")),
        http_host=str(m.get("http_host", "127.0.0.1")),
        http_port=int(m.get("http_port", 8765)),
        auth_token_env=str(m.get("auth_token_env", "ZENAV_MCP_TOKEN")),
    )
    cfg.validate()
    return cfg


def build_config(root: str | pathlib.Path | None = None) -> AppConfig:
    """组装 AppConfig。"""
    base = pathlib.Path(root).resolve() if root else find_project_root()

    sources_raw = _expand(load_toml(base / SOURCES_FILE), "sources")
    settings_raw = _expand(load_toml(base / SETTINGS_FILE), "settings")

    return AppConfig(
        root=base,
        sources=_build_sources(sources_raw),
        catalog=_build_catalog(settings_raw),
        zotero=_build_zotero(settings_raw),
        mcp=_build_mcp(settings_raw),
    )


def load_secret_store(root: str | pathlib.Path | None = None) -> SecretStore:
    """构造密钥读取器（会自动读 config/secrets.env）。"""
    base = pathlib.Path(root).resolve() if root else find_project_root()
    dotenv = parse_env_file(base / SECRETS_FILE)
    if dotenv:
        log.debug("已加载 %d 条密钥（来自 %s）", len(dotenv), SECRETS_FILE)
    return SecretStore(dotenv=dotenv)
