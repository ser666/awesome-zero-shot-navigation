"""命令行入口。

为什么 MCP 之外还要有 CLI（不是重复劳动）：

1. **调试** —— MCP 工具的调用要先过客户端，排查问题时不如直接敲命令。
2. **不需要 Agent 的场景** —— 想导出个 BibTeX、看看配置、手动推一次 Zotero，
   没必要为此拉起一个 MCP 客户端。
3. **cron / 脚本可用** —— 定时推送这类自动化用它更简单可靠。

 ⚠️ 关键纪律：**CLI 和 MCP 都必须调用同一个 services 层**。
    绝不允许 CLI 里出现业务逻辑的副本 —— 那样两边行为会漂移。

用法：
    python3 -m zenav.interfaces.cli info
    python3 -m zenav.interfaces.cli search "zero-shot object navigation" --limit 5
    python3 -m zenav.interfaces.cli overview "VLN" --out vln.md
    python3 -m zenav.interfaces.cli zotero push --dry-run
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from zenav.config import AppConfig, load_secret_store
from zenav.errors import ZenavError
from zenav.log import setup_logging
from zenav.services.bibtex import to_bibtex
from zenav.services.papers import PaperService, render_overview_markdown


# ══════════════════════════════════════════════════════════════════════
#  输出助手
# ══════════════════════════════════════════════════════════════════════
def _emit(data: Any, as_json: bool, *, text: str = "") -> None:
    """统一输出：--json 给机器，否则给文字。"""
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif text:
        print(text)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _emit_error(exc: Exception, as_json: bool) -> int:
    if isinstance(exc, ZenavError):
        if as_json:
            print(json.dumps({"ok": False, "error": exc.message, "hint": exc.hint},
                             ensure_ascii=False, indent=2))
        else:
            print(f"❌ {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"   → {exc.hint}", file=sys.stderr)
        return 2
    print(f"❌ 非预期错误：{type(exc).__name__}: {exc}", file=sys.stderr)
    return 1


def _papers_table(res: Any, *, show_abstract: bool = False) -> str:
    """把检索结果渲染成人看的列表。"""
    if not res.papers:
        return "（没有匹配的论文）"
    lines = [f"共 {res.total_matched} 篇匹配，显示 {len(res.papers)} 篇：", ""]
    for i, p in enumerate(res.papers, 1):
        lines.append(f"{i:>3}. {p.title}")
        meta = f"     {p.authors_display()} ｜ {p.venue_display()} ｜ {p.date}"
        lines.append(meta)
        tag = f"     [{p.category}]"
        if p.topics:
            tag += "  " + " ".join(f"#{t}" for t in p.topics[:5])
        lines.append(tag)
        if p.best_link:
            lines.append(f"     {p.best_link}")
        if p.code_url:
            lines.append(f"     代码: {p.code_url}")
        if show_abstract and p.abstract:
            lines.append(f"     摘要: {p.abstract[:300]}…")
        lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
#  子命令
# ══════════════════════════════════════════════════════════════════════
def _cmd_info(args, cfg: AppConfig, svc: PaperService) -> int:
    info = svc.stats()
    if args.json:
        _emit(info, True)
        return 0
    print(f"📚 论文库概况")
    print(f"   总计    : {info['total']} 篇")
    print(f"   数据来源: {info['source']}")
    print(f"   生成时间: {info['generated_at'][:19]}")
    print(f"   有 DOI  : {info['with_doi']} ｜ 有 arXiv: {info['with_arxiv']}"
          f" ｜ 有代码: {info['with_code']} ｜ 开放获取: {info['open_access']}")
    print(f"\n   会议级别: " + " ｜ ".join(f"{k}:{v}" for k, v in info["tiers"].items()))
    print(f"\n   分类（{len(info['categories'])} 个）:")
    for c, n in info["categories"].items():
        print(f"     {n:>4}  {c}")
    print(f"\n   子专题前 12:")
    for t, n in list(info["topics"].items())[:12]:
        print(f"     {n:>4}  {t}")
    print(f"\n   年份分布（近 8 年）:")
    for y, n in list(info["years"].items())[:8]:
        print(f"     {y}: {n}")
    return 0


def _cmd_search(args, cfg: AppConfig, svc: PaperService) -> int:
    res = svc.search(
        args.query, category=args.category, topic=args.topic, venue=args.venue,
        year_from=args.year_from, year_to=args.year_to, has_code=args.has_code,
        open_access=args.open_access, min_citations=args.min_citations,
        sort=args.sort, limit=args.limit, offset=args.offset,
    )
    _emit(res.to_dict(), args.json, text=_papers_table(res, show_abstract=args.abstract))
    return 0


def _cmd_show(args, cfg: AppConfig, svc: PaperService) -> int:
    p = svc.get(args.identifier)
    if p is None:
        print(f"❌ 未找到：{args.identifier}", file=sys.stderr)
        return 3
    if args.json:
        _emit(p.to_dict(), True)
        return 0
    print(f"📄 {p.title}\n")
    print(f"作者    : {', '.join(p.authors) or '—'}")
    print(f"发表    : {p.date or '—'}")
    print(f"会议    : {p.venue_display()}  ({p.venue_full or '—'})")
    print(f"分类    : {p.category}")
    print(f"子专题  : {', '.join(p.topics) or '—'}")
    print(f"引用    : {p.citations} ｜ 开放获取: {'是' if p.open_access else '否'}")
    print(f"DOI     : {p.doi or '—'}")
    print(f"arXiv   : {p.arxiv_id or '—'}")
    print(f"链接    : {p.url or '—'}")
    print(f"PDF     : {p.pdf_url or '—'}")
    print(f"代码    : {p.code_url or '—'}")
    print(f"项目页  : {p.project_url or '—'}")
    print(f"指纹    : {p.fingerprint()}")
    if p.abstract:
        print(f"\n摘要:\n{p.abstract}")
    print(f"\nBibTeX:\n{to_bibtex([p])}")
    return 0


def _cmd_categories(args, cfg: AppConfig, svc: PaperService) -> int:
    cats = svc.categories()
    if args.json:
        _emit(cats, True)
        return 0
    print(f"共 {len(cats)} 个分类：\n")
    for c, n in cats.items():
        print(f"  {n:>4}  {c}")
    return 0


def _cmd_topics(args, cfg: AppConfig, svc: PaperService) -> int:
    topics = svc.topics(limit=args.limit)
    if args.json:
        _emit(topics, True)
        return 0
    print(f"共 {len(topics)} 个子专题：\n")
    for t, n in topics.items():
        print(f"  {n:>4}  {t}")
    return 0


def _cmd_overview(args, cfg: AppConfig, svc: PaperService) -> int:
    ov = svc.category_overview(args.category, group_by=args.group_by,
                               limit_per_group=args.limit_per_group)
    if not ov.get("found"):
        print(f"❌ 没有这个分类：{args.category}", file=sys.stderr)
        print(f"   可选：{', '.join(svc.categories())}", file=sys.stderr)
        return 3
    text = render_overview_markdown(ov)
    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"✅ 已写入 {path}（{ov['total']} 篇）")
    else:
        _emit(ov, args.json, text=text)
    return 0


def _cmd_latest(args, cfg: AppConfig, svc: PaperService) -> int:
    res = svc.latest(days=args.days, limit=args.limit, category=args.category)
    _emit(res.to_dict(), args.json, text=_papers_table(res))
    return 0


def _cmd_bibtex(args, cfg: AppConfig, svc: PaperService) -> int:
    res = svc.search(args.query, category=args.category, topic=args.topic,
                     sort="date", limit=args.limit)
    if not res.papers:
        print("（没有匹配的论文）", file=sys.stderr)
        return 3
    body = to_bibtex(res.papers,
                     header=f"Exported from awesome-zero-shot-navigation "
                            f"({len(res.papers)} of {res.total_matched})")
    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        print(f"✅ 已导出 {len(res.papers)} 条 → {path}")
    else:
        print(body)
    return 0


def _cmd_config(args, cfg: AppConfig, svc: PaperService) -> int:
    if args.json:
        _emit({
            "root": str(cfg.root),
            "catalog": {"source": cfg.catalog_path, "is_remote": cfg.catalog.is_remote},
            "sources": [
                {"name": s.name, "enabled": s.enabled, "interval": s.interval,
                 "api_key_env": s.api_key_env, "group": s.group}
                for s in cfg.sources.all_specs()
            ],
            "zotero": {
                "enabled": cfg.zotero.enabled,
                "backend": cfg.zotero.backend,
                "uses_local": cfg.zotero.uses_local,
                "needs_zotero_org_account": cfg.zotero.needs_web_credentials,
                "local_api_base": cfg.zotero.local_api_base,
                "collection_root": cfg.zotero.collection_root,
                "state_file": str(cfg.zotero_state_path),
                "api_key_env": cfg.zotero.api_key_env,
                "library_id_env": cfg.zotero.library_id_env,
            },
            "mcp": {"server_name": cfg.mcp.server_name, "transport": cfg.mcp.transport},
        }, True)
        return 0
    print(cfg.summary())
    print("\n密钥状态（只显示掩码，不显示值）:")
    store = load_secret_store(cfg.root)
    envs = [cfg.mcp.auth_token_env, *cfg.sources.key_envs()]
    # ⚠️ 只有 web 后端才需要 zotero.org 凭据 —— 本地后端一个都不需要
    if cfg.zotero.needs_web_credentials:
        envs = [cfg.zotero.api_key_env, cfg.zotero.library_id_env, *envs]
    for name in dict.fromkeys(e for e in envs if e):
        print(f"  {store.describe(name)}")
    if cfg.zotero.uses_local:
        print("\n  ⭐ Zotero 用**本地后端**：不需要上面任何 Zotero 相关密钥。")
    elif not cfg.zotero.needs_web_credentials:
        print("\n  ⭐ Zotero 用**本地后端**（backend=local）：无需 zotero.org 账号。")
    return 0


def _cmd_zotero(args, cfg: AppConfig, svc: PaperService) -> int:
    from zenav.services.zotero import ZoteroPushService

    service = ZoteroPushService.from_config(cfg, catalog=svc.catalog)

    if args.action == "status":
        out = service.status()
        if args.json:
            _emit(out, True)
            return 0
        conn = out.get("connection", {})
        backend = out.get("backend_in_use", "?")
        mode = ("⭐ 本地 API（不用云端）" if backend == "local"
                else "Web API（需要 zotero.org 账号 + 云同步）")
        print("🔗 Zotero 单向推送状态")
        print(f"   后端        : {mode}")
        print(f"   本机库      : {out['library']}")
        print(f"   目录根      : {out['collection_root']}")
        print(f"   状态文件    : {out['state_file']}")
        print(f"   已推送      : {out['pushed_items']} 条")
        print(f"   本地论文    : {out['catalog_total']} 篇 ｜ 未推送: {out['remaining']}")
        if backend == "local":
            print(f"   数据去向    : ⭐ 只写入本机 Zotero，不经过任何服务器")
            print(f"   本地地址    : {conn.get('base_url', '')}")
            print(f"   实例 ID     : {str(conn.get('server_id') or '')[:16]}")
            has_key = conn.get("has_local_key")
            print(f"   本地 Key    : {'✅ 已授权（已缓存）' if has_key else '⬜ 尚未授权（首次写入时会弹确认框）'}")
            if conn.get("note"):
                print(f"   提示        : {conn['note']}")
        print(f"   连接        : {'✅ 正常' if conn.get('ok') else '❌ 失败'}"
              + (f"  {conn.get('error', '')}" if not conn.get("ok") else ""))
        if not conn.get("ok") and backend == "local":
            print("\n   ⚠️ 连不上本机 Zotero。请检查：")
            print("      ① Zotero 桌面端**正在运行**（且是 10+ 版本）")
            print("      ② Zotero → 设置 → 高级 → 勾选")
            print("         「Allow other applications on this computer "
                  "to communicate with Zotero」")
        return 0

    # push
    report = service.push(
        category=args.category, since_days=args.since_days,
        limit=args.limit, dry_run=not args.yes,
    )
    if args.json:
        _emit(report.to_dict(), True)
        return 0
    print(report.summary())
    if not args.yes and report.candidates:
        print("\n（这是演练；确认无误后加 --yes 真正写入）")
    return 0


def _cmd_mcp(args, cfg: AppConfig, svc: PaperService) -> int:
    from zenav.interfaces.mcp_server import main as mcp_main

    argv: list[str] = ["--log-level", args.log_level]
    if args.transport:
        argv += ["--transport", args.transport]
    return mcp_main(argv)


# ══════════════════════════════════════════════════════════════════════
#  参数解析
# ══════════════════════════════════════════════════════════════════════
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zenav",
        description="awesome-zero-shot-navigation 服务层命令行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  %(prog)s info\n"
            "  %(prog)s search 'zero-shot object navigation' --limit 5\n"
            "  %(prog)s overview 'VLN' --out vln.md\n"
            "  %(prog)s zotero status\n"
            "  %(prog)s zotero push            # 演练\n"
            "  %(prog)s zotero push --yes      # 真推\n"
        ),
    )
    p.add_argument("--root", default=None, help="仓库根目录（默认自动探测）")
    p.add_argument("--json", action="store_true", help="输出 JSON（给脚本用）")
    p.add_argument("--log-level", default="WARNING",
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="论文库概况").set_defaults(func=_cmd_info)
    sub.add_parser("categories", help="列出分类").set_defaults(func=_cmd_categories)

    sp = sub.add_parser("topics", help="列出子专题")
    sp.add_argument("--limit", type=int, default=0)
    sp.set_defaults(func=_cmd_topics)

    sp = sub.add_parser("search", help="检索论文")
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--category", default="")
    sp.add_argument("--topic", default="")
    sp.add_argument("--venue", default="")
    sp.add_argument("--year-from", type=int, default=0)
    sp.add_argument("--year-to", type=int, default=0)
    sp.add_argument("--has-code", action="store_true")
    sp.add_argument("--open-access", action="store_true")
    sp.add_argument("--min-citations", type=int, default=0)
    sp.add_argument("--sort", default="date",
                    choices=("date", "citations", "hotness", "year", "title"))
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--offset", type=int, default=0)
    sp.add_argument("--abstract", action="store_true", help="结果里带摘要片段")
    sp.set_defaults(func=_cmd_search)

    sp = sub.add_parser("show", help="单篇详情（DOI / arXiv / 标题）")
    sp.add_argument("identifier")
    sp.set_defaults(func=_cmd_show)

    sp = sub.add_parser("overview", help="⭐ 分类总览表")
    sp.add_argument("category")
    sp.add_argument("--group-by", default="year", choices=("year", "venue", "topic"))
    sp.add_argument("--limit-per-group", type=int, default=0)
    sp.add_argument("--out", default="", help="写到文件（Markdown）")
    sp.set_defaults(func=_cmd_overview)

    sp = sub.add_parser("latest", help="最近发表的论文")
    sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--category", default="")
    sp.set_defaults(func=_cmd_latest)

    sp = sub.add_parser("bibtex", help="导出 BibTeX")
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--category", default="")
    sp.add_argument("--topic", default="")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--out", default="", help="写到 .bib 文件")
    sp.set_defaults(func=_cmd_bibtex)

    sub.add_parser("config", help="查看配置与密钥状态").set_defaults(func=_cmd_config)

    sp = sub.add_parser("zotero", help="Zotero 单向推送（需求 5）")
    zsub = sp.add_subparsers(dest="action", required=True)
    zsub.add_parser("status", help="连接与进度")
    zp = zsub.add_parser("push", help="推送新论文（默认演练）")
    zp.add_argument("--category", default="")
    zp.add_argument("--since-days", type=int, default=0)
    zp.add_argument("--limit", type=int, default=0)
    zp.add_argument("--yes", action="store_true",
                    help="⭐ 真正写入（不加则只演练）")
    sp.set_defaults(func=_cmd_zotero)

    sp = sub.add_parser("mcp", help="启动 MCP 服务（供 Agent 调用）")
    sp.add_argument("--transport", choices=("stdio", "http"), default=None)
    sp.set_defaults(func=_cmd_mcp, log_level="INFO")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(getattr(args, "log_level", "WARNING"))
    try:
        from zenav.infra.catalog import create_catalog

        cfg = AppConfig.load(args.root)
        svc = PaperService(create_catalog(cfg.catalog, cfg.root))
        return args.func(args, cfg, svc)
    except ZenavError as exc:
        return _emit_error(exc, getattr(args, "json", False))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
