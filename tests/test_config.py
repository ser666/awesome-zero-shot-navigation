"""配置层测试：加载、默认值、ENV 展开、校验、密钥遮罩。

⚠️ 这里最该守住的一条：**密钥值绝不能出现在任何输出里**。
   所以有专门的测试检查 mask() 不泄露、require() 会把占位符当未配置。
"""

from __future__ import annotations

import os
import pathlib
import unittest
from unittest import mock

from tests._support import TempDir
from zenav.config import (
    SECRETS_FILE,
    SETTINGS_FILE,
    SOURCES_FILE,
    AppConfig,
    SecretStore,
    build_config,
    find_project_root,
    load_toml,
    mask,
    parse_env_file,
)
from zenav.config.models import McpConfig, ZoteroConfig
from zenav.errors import ConfigError

SOURCES_TOML = """
[defaults]
enabled = true
interval = 0.9

[sources.alpha]
interval = 2.0
per_page = 100

[sources.beta]
enabled = false

[sources.gamma]
enabled = true
api_key_env = "GAMMA_KEY"

[extra_sources.delta]
enabled = true
max_age_days = 500
"""

SETTINGS_TOML = """
[catalog]
source = "docs/data/papers.json"
cache_ttl = 60

[zotero]
enabled = true
library_type = "user"
collection_root = "Zero-Shot Navigation"
state_file = "data/zotero_state.json"

[mcp]
server_name = "test-server"
transport = "stdio"
"""


def write_cfg(root: pathlib.Path, sources: str = SOURCES_TOML,
              settings: str = SETTINGS_TOML) -> pathlib.Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / SOURCES_FILE).write_text(sources, encoding="utf-8")
    (root / SETTINGS_FILE).write_text(settings, encoding="utf-8")
    return root


class TestFindRoot(unittest.TestCase):
    def test_finds_dir_with_config(self):
        with TempDir() as tmp:
            (tmp / "config").mkdir()
            nested = tmp / "a" / "b"
            nested.mkdir(parents=True)
            self.assertEqual(find_project_root(nested), tmp)

    def test_finds_dir_with_git(self):
        with TempDir() as tmp:
            (tmp / ".git").mkdir()
            self.assertEqual(find_project_root(tmp), tmp)

    def test_falls_back_to_start(self):
        with TempDir() as tmp:
            self.assertEqual(find_project_root(tmp), tmp)


class TestLoadToml(unittest.TestCase):
    def test_missing_file_is_empty(self):
        with TempDir() as tmp:
            self.assertEqual(load_toml(tmp / "nope.toml"), {})

    def test_bad_syntax_gives_actionable_error(self):
        with TempDir() as tmp:
            p = tmp / "bad.toml"
            p.write_text("[sec\nkey = ", encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                load_toml(p)
            self.assertIn("语法错误", str(ctx.exception))
            # 提示里应包含"怎么修"的信息
            self.assertIn("TOML", ctx.exception.hint)


class TestSourcesConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.root = write_cfg(self._tmp.__enter__())
        self.cfg = build_config(self.root)

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def test_source_parsed(self):
        s = self.cfg.sources.sources
        self.assertEqual(set(s), {"alpha", "beta", "gamma"})
        self.assertEqual(s["alpha"].interval, 2.0)
        self.assertEqual(s["alpha"].options.get("per_page"), 100)

    def test_defaults_inherited(self):
        """未写 enabled 的源继承 [defaults].enabled。"""
        self.assertTrue(self.cfg.sources.sources["alpha"].enabled)
        self.assertFalse(self.cfg.sources.sources["beta"].enabled)

    def test_enabled_filtering(self):
        names = [s.name for s in self.cfg.sources.enabled(include_extra=False)]
        self.assertIn("alpha", names)
        self.assertNotIn("beta", names)

    def test_extra_sources_separate(self):
        self.assertIn("delta", self.cfg.sources.extra_sources)
        self.assertNotIn("delta", self.cfg.sources.sources)
        self.assertTrue(self.cfg.sources.extra_sources["delta"].is_extra)

    def test_interval_lookup_and_fallback(self):
        self.assertEqual(self.cfg.sources.interval_for("alpha"), 2.0)
        # 未配置的已知源 → 默认间隔
        self.assertEqual(self.cfg.sources.interval_for("beta"), 0.9)
        # 完全未知的源 → 也走默认，不炸
        self.assertEqual(self.cfg.sources.interval_for("nonexistent"), 0.9)

    def test_key_envs(self):
        self.assertIn("GAMMA_KEY", self.cfg.sources.key_envs())
        self.assertNotIn("", self.cfg.sources.key_envs())

    def test_unknown_keys_preserved(self):
        """向前兼容：老代码读新配置不能炸。"""
        s = self.cfg.sources.sources["alpha"]
        self.assertEqual(s.options.get("per_page"), 100)


class TestCatalogAndZoteroAndMcp(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.root = write_cfg(self._tmp.__enter__())
        self.cfg = build_config(self.root)

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def test_catalog_path_resolution(self):
        self.assertEqual(self.cfg.catalog_path,
                         str(self.root / "docs/data/papers.json"))
        self.assertFalse(self.cfg.catalog.is_remote)

    def test_remote_catalog_not_resolved(self):
        root = write_cfg(pathlib.Path(self._tmp._ctx.name))  # noqa: SLF001
        cfg = build_config(root)
        object.__setattr__(cfg, "catalog",
                           type(cfg.catalog)("https://x.org/papers.json", 0))
        self.assertTrue(cfg.catalog.is_remote)
        self.assertEqual(cfg.catalog.resolve(root), "https://x.org/papers.json")

    def test_zotero_state_path(self):
        self.assertEqual(self.cfg.zotero_state_path,
                         self.root / "data/zotero_state.json")

    def test_mcp_values(self):
        self.assertEqual(self.cfg.mcp.server_name, "test-server")
        self.assertEqual(self.cfg.mcp.transport, "stdio")

    def test_summary_has_no_secrets(self):
        """摘要里绝不能出现密钥值（会进日志/终端回看）。"""
        text = self.cfg.summary()
        self.assertIn("Zero-Shot Navigation", text)
        self.assertNotIn("API_KEY=", text)


class TestConfigValidation(unittest.TestCase):
    def test_zotero_bad_library_type(self):
        with self.assertRaises(ConfigError):
            ZoteroConfig(library_type="org").validate()

    def test_zotero_bad_item_type(self):
        with self.assertRaises(ConfigError):
            ZoteroConfig(default_item_type="video").validate()

    def test_zotero_bad_max_batch(self):
        with self.assertRaises(ConfigError):
            ZoteroConfig(max_batch=0).validate()

    def test_zotero_empty_collection_root(self):
        with self.assertRaises(ConfigError):
            ZoteroConfig(collection_root="   ").validate()

    def test_mcp_bad_transport(self):
        with self.assertRaises(ConfigError):
            McpConfig(transport="sse").validate()

    def test_mcp_public_host_requires_awareness(self):
        """⚠️ 对外暴露必须显式确认风险 —— 否则一个配置笔误就把服务开到公网。"""
        with self.assertRaises(ConfigError) as ctx:
            McpConfig(transport="http", http_host="0.0.0.0").validate()
        self.assertIn("回环", str(ctx.exception))

    def test_mcp_loopback_ok(self):
        McpConfig(transport="http", http_host="127.0.0.1").validate()


class TestEnvExpansion(unittest.TestCase):
    def test_expand_from_environ(self):
        with TempDir() as tmp:
            write_cfg(tmp, settings="""
[catalog]
source = "${MY_DATA_BASE}/papers.json"
""")
            with mock.patch.dict(os.environ, {"MY_DATA_BASE": "/data"}):
                cfg = build_config(tmp)
            self.assertEqual(cfg.catalog.source, "/data/papers.json")

    def test_expand_with_default(self):
        with TempDir() as tmp:
            write_cfg(tmp, settings="""
[catalog]
source = "${NOPE_VAR:-fallback}/papers.json"
""")
            os.environ.pop("NOPE_VAR", None)
            cfg = build_config(tmp)
            self.assertEqual(cfg.catalog.source, "fallback/papers.json")

    def test_unset_without_default_kept_literal(self):
        """未设置且无默认值 → 原样保留（便于 `config` 命令展示"待配项"）。"""
        with TempDir() as tmp:
            write_cfg(tmp, settings='[catalog]\nsource = "${UNSET_XYZ}/p.json"\n')
            os.environ.pop("UNSET_XYZ", None)
            cfg = build_config(tmp)
            self.assertIn("UNSET_XYZ", cfg.catalog.source)


class TestSecretStore(unittest.TestCase):
    def test_env_overrides_dotenv(self):
        """⚠️ 已 export 的值必须赢过 .env 里的旧值（生产环境靠这个）。"""
        st = SecretStore(dotenv={"K": "from_file"}, environ={"K": "from_env"})
        self.assertEqual(st.get("K"), "from_env")

    def test_dotenv_used_when_no_env(self):
        st = SecretStore(dotenv={"K": "from_file"}, environ={})
        self.assertEqual(st.get("K"), "from_file")

    def test_empty_env_value_falls_through(self):
        st = SecretStore(dotenv={"K": "file"}, environ={"K": ""})
        self.assertEqual(st.get("K"), "file")

    def test_missing_returns_none(self):
        self.assertIsNone(SecretStore(dotenv={}, environ={}).get("NOPE"))
        self.assertIsNone(SecretStore(dotenv={}, environ={}).get(""))

    def test_require_raises_with_hint(self):
        with self.assertRaises(ConfigError) as ctx:
            SecretStore(dotenv={}, environ={}).require("NOPE", "测试")
        self.assertIn("修复建议", str(ctx.exception))

    def test_require_rejects_placeholder(self):
        """⚠️ 占位符必须当"未配置" —— 否则会拿 REPLACE_ME 去请求、收到费解的 401。"""
        st = SecretStore(dotenv={"K": "REPLACE_WITH_YOUR_KEY"}, environ={})
        with self.assertRaises(ConfigError) as ctx:
            st.require("K", "测试")
        self.assertIn("占位符", str(ctx.exception))

    def test_require_returns_real_value(self):
        st = SecretStore(dotenv={"K": "real-value-1234"}, environ={})
        self.assertEqual(st.require("K", "测试"), "real-value-1234")

    def test_describe_masks(self):
        st = SecretStore(dotenv={"K": "supersecretvalue"}, environ={})
        text = st.describe("K")
        self.assertIn("K =", text)
        self.assertNotIn("supersecretvalue", text)


class TestMask(unittest.TestCase):
    def test_never_leaks_full_value(self):
        secret = "abcdefghijklmnop"
        self.assertNotIn(secret, mask(secret))

    def test_labels(self):
        self.assertEqual(mask(None), "(未配置)")
        self.assertEqual(mask(""), "(空)")
        self.assertIn("占位符", mask("REPLACE_ME_NOW"))

    def test_shows_length(self):
        self.assertIn("len=16", mask("a" * 16))


class TestParseEnvFile(unittest.TestCase):
    def test_parses_variants(self):
        with TempDir() as tmp:
            p = tmp / "s.env"
            p.write_text(
                "# comment\n"
                "PLAIN=value1\n"
                'export EXPORTED=value2\n'
                'QUOTED="value3"\n'
                "SINGLE='value4'\n"
                "\n"
                "WITH_HASH=value5  # trailing comment\n",
                encoding="utf-8",
            )
            got = parse_env_file(p)
            self.assertEqual(got["PLAIN"], "value1")
            self.assertEqual(got["EXPORTED"], "value2")
            self.assertEqual(got["QUOTED"], "value3")
            self.assertEqual(got["SINGLE"], "value4")
            self.assertEqual(got["WITH_HASH"], "value5")

    def test_missing_file_is_empty(self):
        self.assertEqual(parse_env_file("/tmp/definitely-not-here.env"), {})


class TestRealProjectConfig(unittest.TestCase):
    """对**仓库里真实的 config/** 做断言 —— 防止配置文件被改坏。"""

    def test_repo_config_loads(self):
        cfg = AppConfig.load()
        self.assertTrue(cfg.root.exists())
        self.assertIn("openalex", cfg.sources.sources)
        self.assertIn("openreview", cfg.sources.extra_sources)

    def test_repo_zotero_config_valid(self):
        cfg = AppConfig.load()
        self.assertEqual(cfg.zotero.collection_root, "Zero-Shot Navigation")
        self.assertTrue(cfg.zotero.create_subcollections)

    def test_repo_mcp_is_stdio_by_default(self):
        """⚠️ 默认必须是 stdio（本地）—— 不能默认就把服务开到网络。"""
        cfg = AppConfig.load()
        self.assertEqual(cfg.mcp.transport, "stdio")

    def test_secrets_example_has_no_real_values(self):
        """模板文件里只能有占位符 —— 防止有人误提交真 key。"""
        example = AppConfig.load().root / (SECRETS_FILE + ".example")
        self.assertTrue(example.is_file(), "缺少密钥模板文件")
        for line in example.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if not key.strip() or not val.strip():
                continue
            self.assertTrue(
                val.startswith("REPLACE") or val.startswith("#"),
                f"{key} 的值看起来不是占位符：{val[:20]}",
            )


if __name__ == "__main__":
    unittest.main()
