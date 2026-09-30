"""异常体系。

为什么要一套自己的异常，而不是到处 `raise ValueError`：

1. **可区分** —— 调用方（MCP server / CLI）要能分辨"配置错了"
   （用户要改配置）和"上游 API 挂了"（重试即可），响应的措辞完全不同。
2. **可携带上下文** —— 每个异常带上 `hint`（怎么修），
   这样 MCP 工具可以把"怎么办"直接返回给 Agent，而不是只给一个堆栈。
3. **可稳定断言** —— 测试可以精确断言异常类型，而不是匹配报错字符串。
"""

from __future__ import annotations


class ZenavError(Exception):
    """本包所有异常的基类。

    Attributes:
        message: 人类可读的说明。
        hint: 建议的修复动作（会原样展示给用户/Agent，不要放堆栈）。
    """

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n  → 修复建议：{self.hint}"
        return self.message


class ConfigError(ZenavError):
    """配置缺失/非法。属于"用户要动手"的错误，不要重试。"""


class CatalogError(ZenavError):
    """论文目录数据不可用（文件不存在 / JSON 损坏 / 字段缺失）。"""


class UpstreamError(ZenavError):
    """上游 API 调用失败（网络 / 限流 / 5xx）。属于"可以重试"的错误。"""

    def __init__(self, message: str, hint: str = "", status: int | None = None) -> None:
        super().__init__(message, hint)
        self.status = status


class AuthError(UpstreamError):
    """凭据无效或缺失（401/403）。"""


class RateLimitError(UpstreamError):
    """被上游限流（429）。"""


class StateError(ZenavError):
    """本地状态文件损坏或不可写（如 Zotero 同步状态）。"""
