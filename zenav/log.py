"""日志。

统一入口的原因（否则每个模块各写一个 `logging.getLogger(__name__)` 会乱）：

1. **库不打日志配置** —— 在 logger 上加 handler 是"应用"的权利，
   不是"库"的。zenav 作为库被 import 时，绝不去改 root logger
   （否则会把宿主程序，如 MCP 客户端的日志配置覆盖掉）。
2. **一行开关** —— 应用（CLI / MCP server）只需调 `setup_logging()`。
3. **默认安静** —— 库默认 WARNING，避免污染 Agent 的 stdio 通道
   （⚠️ stdio 传输下，stdout 是协议通道，任何 print 都会破坏协议；
   所以日志一律走 stderr）。

用法：

    from zenav.log import get_logger, setup_logging

    setup_logging("DEBUG")          # 只应由应用入口调用
    log = get_logger(__name__)      # 各模块用这个
    log.info("...")
"""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "zenav"
_configured = False


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """取得子 logger。模块里统一用 `get_logger(__name__)`。"""
    return logging.getLogger(name)


def setup_logging(level: str | int = "INFO", quiet: bool = False) -> None:
    """由**应用入口**（CLI / MCP server）调用一次。

    ⚠️ 为什么必须写 stderr 而不是 stdout：
    MCP 的 stdio 传输把 stdout 当作协议通道，写一个字节都会让客户端解析失败。
    这是此类服务最常见的"跑起来但客户端连不上"的原因。

    Args:
        level: 日志级别名或数值。
        quiet: 为 True 时只留 WARNING 以上（Agent 自动调用场景用）。
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                              datefmt="%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.propagate = False       # 不往 root 冒泡，避免重复输出
        _configured = True
    logger.setLevel(logging.WARNING if quiet else level)
