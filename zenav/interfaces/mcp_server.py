"""MCP 接口 —— 让 Agent 能直接调用本项目（需求 4）。

════════════════════════════════════════════════════════════════════════
 这一层是"薄适配器"，刻意不做任何业务判断
════════════════════════════════════════════════════════════════════════
 上面的 services 层已经实现了全部逻辑。本层的唯一职责是：
     · 把 MCP 工具调用转成 service 方法调用
     · 把内部异常翻译成 Agent 看得懂的措辞（MCP 是 Agent 的"用户界面"）
     · 控制返回体积（Agent 上下文有限，不能把 756 篇全塞进去）

 ⇒ 所以本层几乎没有 if/else，也**不该**有新功能。加功能请改 services。

════════════════════════════════════════════════════════════════════════
 两种启动方式（同一份代码）
════════════════════════════════════════════════════════════════════════
    python3 -m zenav.interfaces.mcp_server                  # stdio（本地 Agent）
    python3 -m zenav.interfaces.mcp_server --transport http # HTTP（服务器部署）

 配置 MCP 客户端（以 Claude Code / Hermes 为例，stdio 方式）：

    {
      "mcpServers": {
        "zero-shot-nav": {
          "command": "python3",
          "args": ["-m", "zenav.interfaces.mcp_server"],
          "cwd": "/path/to/awesome-zero-shot-navigation"
        }
      }
    }
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from zenav.config import AppConfig
from zenav.errors import ZenavError
from zenav.log import get_logger, setup_logging

log = get_logger(__name__)

# 返回条数的硬上限 —— 防止 Agent 一次拉 756 篇把上下文撑爆
MAX_LIMIT = 100
DEFAULT_LIMIT = 20


def _tool_error(exc: Exception) -> dict[str, Any]:
    """把异常转成**给 Agent 看的**结构化结果。

    为什么返回 dict 而不是抛异常：
    MCP 客户端把工具异常包装成通用的 "tool call failed"，
    Agent 看不到 `hint`（"该怎么修"），只知道失败了 → 容易反复重试同样的调用。
    返回结构化错误，Agent 就能读懂并换策略（或向用户转述修复步骤）。
    """
    if isinstance(exc, ZenavError):
        return {
            "ok": False,
            "error": exc.message,
            "hint": exc.hint,
            "error_type": type(exc).__name__,
        }
    return {
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}",
        "hint": "这是非预期错误，请把原始信息反馈给维护者",
        "error_type": "Unexpected",
    }


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def build_server(cfg: AppConfig | None = None):
    """装配 MCP server（含全部工具）。

    单独抽成工厂的原因：测试可以 `build_server()` 后逐个调用工具函数，
    不需要真的启动 stdio 进程。
    """
    from mcp.server.fastmcp import FastMCP

    from zenav.infra.catalog import create_catalog
    from zenav.services import bibtex as bibtex_svc
    from zenav.services.papers import PaperService, render_overview_markdown

    config = cfg or AppConfig.load()
    catalog = create_catalog(config.catalog, config.root)
    papers = PaperService(catalog)

    mcp = FastMCP(
        config.mcp.server_name,
        instructions=(
            "Zero-shot / training-free embodied navigation 论文库。"
            f"覆盖 {catalog.load().total} 篇论文，按任务分类与子专题组织。\n"
            "典型用法：先 get_dataset_info 或 list_categories 了解数据范围，"
            "再用 search_papers / category_overview 取内容，"
            "需要导入文献管理器时用 export_bibtex 或 push_to_zotero。"
        ),
        host=config.mcp.http_host,
        port=config.mcp.http_port,
    )

    # ══════════════════════════════════════════════════════════════
    #  数据探索
    # ══════════════════════════════════════════════════════════════
    @mcp.tool()
    def get_dataset_info() -> dict:
        """查看论文库概况：总数、分类、子专题、会议分布、时间跨度。

        在不确定库里有什么、或需要向用户汇报数据覆盖范围时，先调用这个。
        """
        try:
            info = papers.stats()
            info["ok"] = True
            info["source_config"] = config.catalog_path
            return info
        except Exception as exc:                       # noqa: BLE001
            return _tool_error(exc)

    @mcp.tool()
    def list_categories() -> dict:
        """列出全部任务分类及其论文数量（按数量降序）。

        用于确定 category 参数的合法取值 —— 分类名从数据里来，
        不要凭记忆猜（猜错会返回空结果）。
        """
        try:
            return {"ok": True, "categories": papers.categories()}
        except Exception as exc:                       # noqa: BLE001
            return _tool_error(exc)

    @mcp.tool()
    def list_topics(limit: int = 0) -> dict:
        """列出子专题标签及其论文数量（如 Zero-Shot / Real Robot / VLM-MLLM）。

        Args:
            limit: 只返回前 N 个（0 = 全部）。
        """
        try:
            return {"ok": True, "topics": papers.topics(limit=_clamp(limit, 0, 500))}
        except Exception as exc:                       # noqa: BLE001
            return _tool_error(exc)

    # ══════════════════════════════════════════════════════════════
    #  检索
    # ══════════════════════════════════════════════════════════════
    @mcp.tool()
    def search_papers(
        query: str = "",
        category: str = "",
        topic: str = "",
        venue: str = "",
        year_from: int = 0,
        year_to: int = 0,
        has_code: bool = False,
        open_access: bool = False,
        min_citations: int = 0,
        sort: str = "date",
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict:
        """在大论文库里检索论文（条件之间是 AND 关系）。

        Args:
            query: 关键词，空格分隔；全部命中才算匹配（匹配标题/摘要/作者/会议）。
            category: 任务分类，如 "Object-Goal Navigation (ObjectNav)"；
                也接受片段（如 "ObjectNav"）。先用 list_categories 确认取值。
            topic: 子专题标签，如 "Zero-Shot" / "Real Robot" / "VLM / MLLM"。
            venue: 会议/期刊简称片段，如 "CoRL" / "ICRA"。
            year_from: 起始年份（含）。
            year_to: 结束年份（含）。
            has_code: 只要带开源代码的。
            open_access: 只要开放获取的。
            min_citations: 最低引用数。
            sort: date（默认，新→旧）| citations | hotness | year | title
            limit: 返回条数（上限 100）。需要更多请用 offset 分页。
            offset: 跳过前 N 条，用于分页。

        Returns:
            total_matched（符合条件总数）与 papers（精简条目，不含摘要全文）。
        """
        try:
            res = papers.search(
                query, category=category, topic=topic, venue=venue,
                year_from=year_from, year_to=year_to, has_code=has_code,
                open_access=open_access, min_citations=min_citations,
                sort=sort, limit=_clamp(limit, 1, MAX_LIMIT), offset=max(0, offset),
            )
            out = res.to_dict()
            out["ok"] = True
            return out
        except Exception as exc:                       # noqa: BLE001
            return _tool_error(exc)

    @mcp.tool()
    def get_paper(identifier: str) -> dict:
        """按 DOI / arXiv ID / 标题取单篇论文的**完整**信息（含摘要全文）。

        当需要摘要、PDF 链接、代码仓库、项目主页等细节时用这个
        （search_papers 为省上下文只返回精简字段）。

        Args:
            identifier: DOI（可带 https://doi.org/ 前缀）、arXiv ID（如 2603.28691）、
                或标题（支持片段，取最匹配的一篇）。
        """
        try:
            paper = papers.get(identifier)
            if paper is None:
                return {
                    "ok": False,
                    "error": f"未找到：{identifier}",
                    "hint": "试试 search_papers 用关键词找；标题请用较独特的片段",
                }
            out = paper.to_dict()
            out["ok"] = True
            out["bibtex"] = bibtex_svc.to_bibtex_entry(paper)
            return out
        except Exception as exc:                       # noqa: BLE001
            return _tool_error(exc)

    @mcp.tool()
    def latest_papers(days: int = 30, limit: int = DEFAULT_LIMIT,
                      category: str = "") -> dict:
        """取最近 N 天**发表**的论文（按发表/上线日期，不是采集日期）。

        Args:
            days: 回溯天数。
            limit: 返回条数上限（上限 100）。
            category: 限定某个分类（空 = 全部）。
        """
        try:
            res = papers.latest(days=_clamp(days, 1, 3650),
                                limit=_clamp(limit, 1, MAX_LIMIT),
                                category=category)
            out = res.to_dict()
            out["ok"] = True
            return out
        except Exception as exc:                       # noqa: BLE001
            return _tool_error(exc)

    # ══════════════════════════════════════════════════════════════
    #  整理：分类总览
    # ══════════════════════════════════════════════════════════════
    @mcp.tool()
    def category_overview(
        category: str,
        group_by: str = "year",
        limit_per_group: int = 0,
        format: str = "markdown",
    ) -> dict:
        """取某个分类的**总览表**（介绍该方向全部论文的概览）。

        Args:
            category: 分类名或其片段，如 "VLN" / "Aerial VLN"。
            group_by: year（默认，按年份倒序）| venue | topic。
            limit_per_group: 每组最多列几篇（0 = 全列。⚠️ 全列可能很大）。
            format: markdown（默认，返回可直接展示的表格）| data（只返回结构化数据）。

        Returns:
            markdown 格式带 tables 字段；data 格式只含 groups 结构。
        """
        try:
            ov = papers.category_overview(
                category, group_by=group_by,
                limit_per_group=_clamp(limit_per_group, 0, MAX_LIMIT),
            )
            if not ov.get("found"):
                return {
                    "ok": False,
                    "error": f"没有这个分类：{category}",
                    "hint": "先用 list_categories 看合法取值",
                    "available": list(papers.categories().keys()),
                }
            if format == "data":
                return {"ok": True, **ov}
            return {
                "ok": True,
                "category": ov["category"],
                "total": ov["total"],
                "tables": render_overview_markdown(ov),
            }
        except Exception as exc:                       # noqa: BLE001
            return _tool_error(exc)

    # ══════════════════════════════════════════════════════════════
    #  导出：BibTeX
    # ══════════════════════════════════════════════════════════════
    @mcp.tool()
    def export_bibtex(
        query: str = "",
        category: str = "",
        topic: str = "",
        limit: int = 50,
        dry_run: bool = False,
    ) -> dict:
        """导出 BibTeX（可直接贴进 LaTeX 或导入任何文献管理器）。

        Args:
            query: 关键词筛选（同 search_papers）。
            category: 按分类筛选。
            topic: 按子专题筛选。
            limit: 最多导出多少条（上限 200）。
            dry_run: 只要条数统计、不要正文时设 True。
        """
        try:
            res = papers.search(query, category=category, topic=topic,
                                sort="date", limit=_clamp(limit, 1, 200))
            if dry_run:
                return {"ok": True, "count": len(res.papers),
                        "total_matched": res.total_matched}
            body = bibtex_svc.to_bibtex(
                res.papers,
                header=f"Exported from awesome-zero-shot-navigation "
                       f"({len(res.papers)} entries)",
            )
            return {
                "ok": True,
                "count": len(res.papers),
                "total_matched": res.total_matched,
                "bibtex": body,
            }
        except Exception as exc:                       # noqa: BLE001
            return _tool_error(exc)

    # ══════════════════════════════════════════════════════════════
    #  Zotero 单向推送（需求 5）
    # ══════════════════════════════════════════════════════════════
    @mcp.tool()
    def zotero_status() -> dict:
        """查看 Zotero 推送的配置与进度：连不连得上、已推多少、还剩多少。

        在推送前先调用它，可以立刻区分"配置没配好"和"没有新论文"。
        """
        try:
            from zenav.services.zotero import ZoteroPushService

            if not config.zotero.enabled:
                return {
                    "ok": False,
                    "error": "Zotero 推送未启用",
                    "hint": "把 config/settings.toml 里 [zotero] enabled 设为 true，"
                            "并在 config/secrets.env 配好 API Key 与 userID",
                }
            svc = ZoteroPushService.from_config(config, catalog=catalog)
            out = svc.status()
            out["ok"] = bool(out.get("connection", {}).get("ok", True))
            return out
        except Exception as exc:                       # noqa: BLE001
            return _tool_error(exc)

    @mcp.tool()
    def push_to_zotero(
        category: str = "",
        since_days: int = 0,
        limit: int = 0,
        dry_run: bool = True,
    ) -> dict:
        """把**尚未推送过**的论文推送到 Zotero（单向：服务 → 你的 Zotero 库）。

        幂等：已推过的不会重复推（按 DOI/arXiv/标题指纹去重）。
        论文会按分类自动放进 Zotero 的对应子目录。

        ⚠️ 默认 dry_run=True（只报告将要做什么，不写入）。确认无误后再传 False。

        Args:
            category: 只推某个分类（空 = 全部）。
            since_days: 只推最近 N 天发表的（0 = 不限）。
            limit: 本次最多推多少条（0 = 用配置里的 max_batch）。
            dry_run: True = 演练（默认）；False = 真正写入 Zotero。
        """
        try:
            from zenav.services.zotero import ZoteroPushService

            svc = ZoteroPushService.from_config(config, catalog=catalog)
            report = svc.push(
                category=category, since_days=max(0, since_days),
                limit=max(0, limit), dry_run=dry_run,
            )
            out = report.to_dict()
            out["ok"] = True
            out["summary"] = report.summary()
            return out
        except Exception as exc:                       # noqa: BLE001
            return _tool_error(exc)

    return mcp


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        prog="python3 -m zenav.interfaces.mcp_server",
        description="本项目的 MCP 服务（供 Agent 调用）",
    )
    parser.add_argument("--transport", choices=("stdio", "http"),
                        default=None,
                        help="覆盖 config/settings.toml 里的 mcp.transport")
    parser.add_argument("--host", default=None, help="HTTP 监听地址（覆盖配置）")
    parser.add_argument("--port", type=int, default=None, help="HTTP 端口（覆盖配置）")
    parser.add_argument("--log-level", default="INFO",
                        choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    parser.add_argument("--root", default=None, help="仓库根目录（默认自动探测）")
    args = parser.parse_args(argv)

    # ⚠️ 日志必须走 stderr：stdio 传输下 stdout 是协议通道
    setup_logging(args.log_level, quiet=(args.transport or "") == "stdio")

    cfg = AppConfig.load(args.root)
    transport = args.transport or cfg.mcp.transport
    if args.host:
        object.__setattr__(cfg.mcp, "http_host", args.host)
    if args.port:
        object.__setattr__(cfg.mcp, "http_port", args.port)

    log.info("启动 MCP 服务：%s（传输=%s）", cfg.mcp.server_name, transport)
    try:
        server = build_server(cfg)
        if transport == "http":
            server.run(transport="streamable-http")
        else:
            server.run(transport="stdio")
    except KeyboardInterrupt:
        log.info("收到中断，退出")
        return 130
    except Exception as exc:                            # noqa: BLE001
        log.error("MCP 服务启动失败：%s", exc)
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
