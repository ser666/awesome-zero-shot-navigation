"""密钥解析。

设计原则（重要，别绕过）：

1. **密钥永不落进代码和配置** —— 配置里只出现"环境变量名"，
   真实值从环境变量读。这样配置文件可以公开提交。
2. **三级优先级**（后者覆盖前者）：
       config/secrets.env   ← 本机开发便利（已 gitignore）
       进程环境变量          ← CI / 服务器注入（生产首选）
   实际上"进程环境变量优先"更安全：已 export 的值不会被文件里的旧值悄悄覆盖。
3. **绝不在日志/错误里回显密钥值** —— 只展示掩码（前 4 位 + 长度），
   否则密钥会顺着日志流进 CI 输出。为此提供 `mask()`。
"""

from __future__ import annotations

import os
import pathlib
import re
from collections.abc import Mapping

from zenav.errors import ConfigError

# 支持：KEY=value / export KEY=value / KEY="value" / KEY='value'
_LINE_RE = re.compile(
    r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:(?P<q>["'])(?P<qv>.*?)(?P=q)|(?P<v>[^#\n]*))""",
)


def parse_env_file(path: str | pathlib.Path) -> dict[str, str]:
    """解析 .env 风格文件。不存在则返回空 dict（不报错）。

    刻意只实现最小子集（不做变量插值、不做多行值）——
    越复杂越容易在"密钥为什么没读到"上浪费时间。
    """
    p = pathlib.Path(path)
    if not p.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        val = m.group("qv") if m.group("q") else (m.group("v") or "")
        out[m.group(1)] = val.strip()
    return out


def mask(value: str | None, keep: int = 4) -> str:
    """把密钥变成可安全展示的形式。`None` → `(未配置)`，空串 → `(空)`。"""
    if value is None:
        return "(未配置)"
    if not value:
        return "(空)"
    if value.startswith("REPLACE"):
        return f"(占位符: {value[:12]}…)"
    head = value[:keep]
    return f"{head}{'*' * 8}  (len={len(value)})"


class SecretStore:
    """统一的密钥读取入口。

    Args:
        dotenv: 已解析的 .env 内容（通常来自 config/secrets.env）。
        environ: 环境变量映射（默认 os.environ，测试时可注入假值）。
    """

    def __init__(
        self,
        dotenv: Mapping[str, str] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._dotenv = dict(dotenv or {})
        self._environ = os.environ if environ is None else environ

    def get(self, env_name: str) -> str | None:
        """按优先级取值：进程环境变量 > .env 文件。返回 None 表示未配置。"""
        if not env_name:
            return None
        if env_name in self._environ and self._environ[env_name] != "":
            return self._environ[env_name]
        val = self._dotenv.get(env_name)
        return val or None

    def require(self, env_name: str, purpose: str, hint: str = "") -> str:
        """取必需密钥；缺失或仍是占位符时抛 ConfigError（带修复建议）。

        为什么把"占位符"也算未配置：否则会把 REPLACE_WITH_... 当成真 key
        发出去，然后收到一个 401，排查起来比直接报错费劲得多。
        """
        val = self.get(env_name)
        if not val or val.startswith("REPLACE"):
            raise ConfigError(
                f"{purpose}需要密钥，但环境变量 {env_name} "
                f"{'未设置' if not val else '仍是占位符'}",
                hint=hint
                or (
                    f"① 复制模板：cp config/secrets.env.example config/secrets.env\n"
                    f"     ② 填入 {env_name} 的真实值（chmod 600 该文件）\n"
                    f"     ③ 或直接注入环境变量：export {env_name}=..."
                ),
            )
        return val

    def describe(self, env_name: str) -> str:
        """给状态输出用：`ZOTERO_API_KEY = abcd********  (len=24)`"""
        return f"{env_name} = {mask(self.get(env_name))}"
