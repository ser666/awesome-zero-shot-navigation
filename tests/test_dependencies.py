"""依赖声明的守卫测试。

为什么需要：依赖问题**不会在开发机上暴露**（我这儿装着对的版本），
却在**新环境/陌生人**那里直接失败 —— 属于最难靠"本地跑一遍"发现的类别。
所以用测试把两条钉死：

  ① requirements 里 `mcp` 必须有 `<2` 上界
     （背景：无上界会装到 mcp 2.x，FastMCP 被改名 → 启动失败）
  ② MCP 层在版本不兼容时要**给出可执行的修复提示**，而不是裸的
     ModuleNotFoundError —— 否则用户/Agent 拿到报错也不知道怎么修
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestDependencyPins(unittest.TestCase):
    def test_mcp_has_upper_bound(self):
        """⭐ `mcp` 必须钉 `<2`（2.x 把 FastMCP 改名成 MCPServer）。

        ⚠️ 这条是从真事故来的：requirements 原写 `mcp>=1.9`（无上界），
        新环境装到 2.x → `ModuleNotFoundError: No module named
        'mcp.server.fastmcp'`，而我的开发机装着 1.x 所以一直没发现。
        一次"模拟陌生人从零部署"的验证才把它暴露出来。
        """
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        lines = [
            ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        reqs = [ln for ln in lines if re.match(r"^mcp\b", ln)]
        self.assertTrue(reqs, "requirements.txt 里应有 mcp 依赖行")
        for r in reqs:
            self.assertIn("<2", r.replace(" ", ""),
                          f"mcp 依赖必须带上界 <2，当前：{r!r}")

    def test_core_layers_need_no_third_party(self):
        """核心层（config/domain/infra/services）不得依赖第三方。

        与 tests/test_layering.py 同源，但这里顺带确认
        requirements 只声明了 mcp 一项 —— 多出来的第三方依赖要能解释。
        """
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        reqs = [
            ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        names = [re.split(r"[<>=!~\[]", r)[0].strip().lower() for r in reqs]
        self.assertEqual(names, ["mcp"],
                         f"服务层只应声明 mcp 一个第三方依赖，当前：{names}")


class TestIncompatibleVersionMessage(unittest.TestCase):
    def test_hint_is_actionable(self):
        """版本不兼容时的提示必须**可执行**（含确切安装命令）。

        做法：把 `mcp.server.fastmcp` 从模块表里摘掉，模拟 mcp 2.x 环境，
        然后调用 `_import_fastmcp()` 断言抛出的是带 hint 的 ConfigError。
        """
        import sys

        from zenav.errors import ConfigError
        from zenav.interfaces.mcp_server import _import_fastmcp

        saved = {
            k: v for k, v in list(sys.modules.items())
            if k == "mcp" or k.startswith("mcp.")
        }
        for k in saved:
            sys.modules.pop(k, None)
        # 放一个"假的 2.x"：有 mcp 包，但没有 mcp.server.fastmcp
        import types

        fake_mcp = types.ModuleType("mcp")
        fake_srv = types.ModuleType("mcp.server")
        fake_mcp.server = fake_srv            # type: ignore[attr-defined]
        sys.modules["mcp"] = fake_mcp
        sys.modules["mcp.server"] = fake_srv
        sys.modules.pop("mcp.server.fastmcp", None)
        try:
            with self.assertRaises(ConfigError) as ctx:
                _import_fastmcp()
            msg = str(ctx.exception)
            self.assertIn("不兼容", msg)
            self.assertIn("mcp>=1.9,<2", msg, "提示里要有可执行的安装命令")
        finally:
            for k in list(sys.modules):
                if k == "mcp" or k.startswith("mcp."):
                    sys.modules.pop(k, None)
            sys.modules.update(saved)

    def test_real_fastmcp_imports_in_current_env(self):
        """当前环境（若装了 mcp 1.x）应能正常导入 —— 防止上一条改坏真实路径。"""
        from zenav.interfaces.mcp_server import _import_fastmcp

        try:
            cls = _import_fastmcp()
        except Exception as exc:                        # noqa: BLE001
            self.skipTest(f"当前环境无可用 mcp 1.x：{type(exc).__name__}")
        self.assertTrue(callable(cls), "FastMCP 应可调用")


if __name__ == "__main__":
    unittest.main()
