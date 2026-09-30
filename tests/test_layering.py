"""架构不变量测试 —— 用测试守住"分层"这个约定。

为什么需要它：
    分层是**口头约定**，很容易被无意破坏，而且破坏时**不会报错**：
      · 某天有人为了图方便，在 services/ 里 `import mcp` →
        服务层的"零依赖核心"这个卖点就没了，但功能照常工作，没人会发现
      · 某天有人在采集层里 `import zenav` → 分层方向反了，
        采集（跑在 Actions）开始依赖服务层的部署形态
      · 某天有人把业务逻辑复制到 MCP 工具里 → 两边行为开始漂移

    这些都属于**静默腐坏**：不报错、不影响功能，只是慢慢变差。
    用测试把它们变成**显式失败**，是唯一可靠的守门方式。

⚠️ 本文件用**静态源码扫描**（不 import 被测模块），
   所以即使依赖缺失也能跑 —— 它检查的是"代码写了什么"。
"""

from __future__ import annotations

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ⚠️ 用解释器自带的权威清单，不要手写白名单 ——
#    手写会漏（第一版就漏了 base64 / inspect / socket / platform，
#    结果测试自己误报）。sys.stdlib_module_names 是 Python 3.10+ 的官方答案。
STDLIB = set(sys.stdlib_module_names) | {"__future__"}

# ── 分层定义（依赖只能向下，不能反向） ──────────────────────────────
LAYERS = ("config", "domain", "infra", "services", "interfaces")

# 各层**允许** import 的本包前缀
ALLOWED_INTERNAL = {
    "config": ("zenav.config", "zenav.errors", "zenav.log"),
    "domain": ("zenav.domain", "zenav.errors", "zenav.log"),
    "infra": ("zenav.infra", "zenav.domain", "zenav.config",
              "zenav.errors", "zenav.log"),
    "services": ("zenav.services", "zenav.domain", "zenav.infra",
                 "zenav.config", "zenav.errors", "zenav.log"),
    "interfaces": ("zenav.interfaces", "zenav.services", "zenav.domain",
                   "zenav.infra", "zenav.config", "zenav.errors", "zenav.log"),
}

# ⭐ 核心层不得依赖的第三方包（MCP SDK 只允许出现在 interfaces/）
MCP_PACKAGES = ("mcp", "fastmcp", "uvicorn", "starlette")

# 采集层（跑在 GitHub Actions，必须零第三方依赖）
COLLECTION_DIR = ROOT / "scripts"


def iter_py(directory: pathlib.Path):
    for p in sorted(directory.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        yield p


def imports_of(path: pathlib.Path) -> set[str]:
    """用 AST 取出所有 import 的顶层模块名（比正则可靠：不会命中注释/字符串）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:              # 语法错本身就该让测试失败
        raise AssertionError(f"{path} 语法错误：{exc}") from exc

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            # 相对导入（level>0）视为本包内部
            if node.level and node.level > 0:
                names.add("zenav." + (node.module or ""))
            elif node.module:
                names.add(node.module)
    return names


class TestLayerDirection(unittest.TestCase):
    """依赖方向：只能 interfaces → services → infra/domain → config。"""

    def test_no_upward_imports(self):
        violations: list[str] = []
        for layer in LAYERS:
            layer_dir = ROOT / "zenav" / layer
            if not layer_dir.is_dir():
                continue
            allowed = ALLOWED_INTERNAL[layer]
            for path in iter_py(layer_dir):
                for imp in imports_of(path):
                    if not imp.startswith("zenav"):
                        continue
                    if not any(imp == a or imp.startswith(a + ".")
                               for a in allowed):
                        violations.append(
                            f"{path.relative_to(ROOT)} 导入了 {imp}"
                            f"（{layer} 层只允许：{', '.join(allowed)}）"
                        )
        self.assertEqual(
            violations, [],
            "发现反向/跨层导入：\n  " + "\n  ".join(violations),
        )

    def test_collection_layer_does_not_import_service_layer(self):
        """⭐ 采集层不得 import 服务层 —— 否则分层方向反了。

        采集层跑在 GitHub Actions（零依赖、无部署形态）；
        服务层跑在本机/服务器。让前者依赖后者，等于把部署约束引入 CI。
        """
        violations = []
        for path in iter_py(COLLECTION_DIR):
            for imp in imports_of(path):
                if imp.startswith("zenav"):
                    violations.append(f"{path.relative_to(ROOT)} → {imp}")
        self.assertEqual(
            violations, [],
            "采集层导入了服务层：\n  " + "\n  ".join(violations),
        )


class TestZeroDependencyCore(unittest.TestCase):
    """⭐ 服务层核心（不含 interfaces/）必须零第三方依赖。

    这是本项目的关键卖点：CLI、Zotero 推送、Python import
    在没有任何依赖的环境下都能用。MCP SDK 只允许出现在 interfaces/。
    """

    def test_core_layers_have_no_third_party_imports(self):
        violations: list[str] = []
        for layer in ("config", "domain", "infra", "services"):
            layer_dir = ROOT / "zenav" / layer
            if not layer_dir.is_dir():
                continue
            for path in iter_py(layer_dir):
                for imp in imports_of(path):
                    top = imp.split(".")[0]
                    if imp.startswith("zenav") or top in STDLIB:
                        continue
                    violations.append(f"{path.relative_to(ROOT)} → {imp}")
        self.assertEqual(
            violations, [],
            "服务层核心引入了第三方依赖（应只出现在 interfaces/）：\n  "
            + "\n  ".join(violations),
        )

    def test_mcp_sdk_only_in_interfaces(self):
        """MCP SDK 只能在 interfaces/ 里出现。"""
        violations = []
        for layer in ("config", "domain", "infra", "services"):
            layer_dir = ROOT / "zenav" / layer
            if not layer_dir.is_dir():
                continue
            for path in iter_py(layer_dir):
                for imp in imports_of(path):
                    if imp.split(".")[0] in MCP_PACKAGES:
                        violations.append(f"{path.relative_to(ROOT)} → {imp}")
        self.assertEqual(violations, [], "MCP SDK 泄漏到了核心层")

    def test_collection_layer_is_zero_dependency(self):
        """⭐ 采集层必须零第三方依赖（跑在 Actions，不做 pip install）。"""
        # 采集层内部模块互相 import（同目录）—— 这些没有点号
        local = {p.stem for p in iter_py(COLLECTION_DIR)}
        violations = []
        for path in iter_py(COLLECTION_DIR):
            for imp in imports_of(path):
                top = imp.split(".")[0]
                if top in STDLIB or top in local or imp.startswith("zenav"):
                    continue
                violations.append(f"{path.relative_to(ROOT)} → {imp}")
        self.assertEqual(
            violations, [],
            "采集层引入第三方依赖（会破坏 Actions 免安装）：\n  "
            + "\n  ".join(violations),
        )


class TestNoLogicDuplication(unittest.TestCase):
    """⚠️ 业务逻辑不得在 interfaces/ 里复制一份。

    怎么测：接口层应当 *调用* services 的方法，而不是自己实现规则。
    这里用一个粗糙但有效的信号：接口层文件里不应出现
    数据筛选/排序/指纹等领域的核心表达式。
    """

    FORBIDDEN_PATTERNS = (
        # 指纹算法（属于 domain）
        "SHA1", "sha1(",
        # 条目类型映射表（属于 services.zotero）
        "_ITEM_TYPE_BY_KIND",
        # 排序键（属于 services.papers）
        "lambda p: (-p.citations",
        # 会议分级判定（属于采集层 venues）
        "_DECISION_STATE",
    )

    def test_interfaces_delegate_instead_of_reimplementing(self):
        violations = []
        for path in iter_py(ROOT / "zenav" / "interfaces"):
            text = path.read_text(encoding="utf-8")
            # 去掉注释行后再查（注释里提到这些名字是正常的）
            code = "\n".join(
                ln for ln in text.splitlines()
                if not ln.strip().startswith("#")
            )
            for pat in self.FORBIDDEN_PATTERNS:
                if pat in code:
                    violations.append(f"{path.relative_to(ROOT)} 含 {pat!r}")
        self.assertEqual(
            violations, [],
            "接口层疑似复制了业务逻辑（应改为调用 services）：\n  "
            + "\n  ".join(violations),
        )


class TestConfigFilesAreTracked(unittest.TestCase):
    """配置文件必须存在且被 git 跟踪（否则 CI 里行为与本地不一致）。"""

    def test_config_files_exist(self):
        for name in ("config/sources.toml", "config/settings.toml",
                     "config/secrets.env.example"):
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file(), f"缺少 {name}")

    def test_gitignore_excludes_secrets(self):
        """⚠️ 密钥文件必须被 .gitignore 排除（防止误提交）。

        包括两类：
          · config/secrets.env            —— web 后端的 zotero.org API Key
          · data/zotero_local_keys.json   —— 本地后端的本地 API Key
            （它等同于"改写你文献库的权力"，泄露后果更直接）
        """
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("config/secrets.env", text)
        self.assertIn("zotero_local_keys.json", text,
                      "本地 Zotero API Key 必须被忽略")

    def test_gitignore_does_not_exclude_needed_data(self):
        """但数据文件不能被排除（它们是列表的历史）。"""
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#") or not s:
                continue
            self.assertNotIn(s, ("data/papers.db", "docs/data/papers.json",
                                 "README.md", "config/sources.toml"),
                             f"误屏蔽了必需文件：{s}")


if __name__ == "__main__":
    unittest.main()
