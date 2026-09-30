"""接口层 —— 对外暴露的形状。

本层只做"翻译"：把 services 的能力翻译成某种协议/形态。
    mcp_server.py  → MCP 协议（给 Agent）
    cli.py         → 命令行（给人 / cron / 脚本）
"""

__all__ = ["cli", "mcp_server"]
