.PHONY: help update full audit export serve test service-test enrich backfill probe clean extra \
        config info categories overview latest search mcp zotero-status zotero-push zotero-push-yes

help:
	@echo "awesome-zero-shot-navigation — 常用命令"
	@echo ""
	@echo "  ── 采集层（scripts/，零依赖，跑在 Actions） ──"
	@echo "  make update      增量采集（回看 21 天）→ 导出 README + 网站数据"
	@echo "  make extra       ⭐ 只跑聚合式源（OpenReview / HuggingFace）→ 导出"
	@echo "  make enrich      补数据（引用数 / 开放 PDF / 代码仓库），限流"
	@echo "  make backfill    只重算派生字段（会议分级 / 子专题 / 链接），不联网"
	@echo "  make export      只导出（不采集）"
	@echo "  make full        全量采集（从 2019 年起，慢）"
	@echo "  make probe       探测各学术源可用性（建议在 Actions 上跑）"
	@echo "  make audit       抽查数据质量（误收 / 分布）"
	@echo ""
	@echo "  ── 服务层（zenav/，给 Agent 与 Zotero 用） ──"
	@echo "  make config      查看服务层配置与密钥状态"
	@echo "  make info        论文库概况（总数 / 分类 / 会议分布）"
	@echo "  make categories  列出全部分类"
	@echo "  make overview    ⭐ 分类总览表：make overview C=\"VLN\""
	@echo "  make latest      最近发表：make latest D=30 N=10"
	@echo "  make search      ⭐ 检索：make search Q=\"zero-shot\" ARGS=\"--has-code\""
	@echo "  make mcp         启动 MCP 服务（供 Agent 调用，stdio）"
	@echo "  make zotero-status    Zotero 连接与推送进度（默认走**本地**，不用云端）"
	@echo "  make zotero-push      ⭐ 推送新论文（演练：只报告，不写入）"
	@echo "  make zotero-push-yes  真正写入 Zotero（本地后端首次会弹一次确认框）"
	@echo ""
	@echo "  ── 通用 ──"
	@echo "  make serve       本地预览网站 → http://127.0.0.1:8210"
	@echo "  make test        跑全部自测（采集层规则 + 服务层）"
	@echo "  make clean       清理 __pycache__"
	@echo ""

# ══════════════════════════════════════════════════════════════════════
#  采集层
# ══════════════════════════════════════════════════════════════════════
update:
	python3 scripts/pipeline.py --days 21
	python3 scripts/pipeline.py --refilter
	python3 scripts/pipeline.py --backfill
	python3 scripts/export.py

extra:
	python3 scripts/pipeline.py --extra-only --budget 600
	python3 scripts/pipeline.py --backfill
	python3 scripts/export.py

enrich:
	python3 scripts/pipeline.py --enrich --budget 900

backfill:
	python3 scripts/pipeline.py --backfill
	python3 scripts/export.py

export:
	python3 scripts/export.py

full:
	python3 scripts/pipeline.py --full --budget 1800
	python3 scripts/export.py

probe:
	python3 scripts/probe_sources.py

audit:
	python3 scripts/audit.py

serve:
	python3 -m http.server -d docs 8210 --bind 127.0.0.1

# ══════════════════════════════════════════════════════════════════════
#  服务层（zenav/）—— 与采集层共用一个配置文件，但代码完全分离
# ══════════════════════════════════════════════════════════════════════
ZENAV := python3 -m zenav.interfaces.cli

config:
	@$(ZENAV) config

info:
	@$(ZENAV) info

categories:
	@$(ZENAV) categories

overview:
	@test -n "$(C)" || (echo "用法: make overview C=\"Vision-and-Language Navigation (VLN)\""; exit 1)
	@$(ZENAV) overview "$(C)"

latest:
	@$(ZENAV) latest --days $(or $(D),30) --limit $(or $(N),10)

search:
	@$(ZENAV) search "$(Q)" $(ARGS)

mcp:
	@echo "启动 MCP 服务（stdio）—— 供 Agent 调用"
	@echo "客户端配置示例见 zenav/interfaces/mcp_server.py 顶部注释"
	@$(ZENAV) mcp

zotero-status:
	@$(ZENAV) zotero status

zotero-push:
	@$(ZENAV) zotero push

zotero-push-yes:
	@$(ZENAV) zotero push --yes

# ══════════════════════════════════════════════════════════════════════
#  测试
# ══════════════════════════════════════════════════════════════════════
test:
	@echo "── 相关性过滤 ──"
	@python3 scripts/relevance.py
	@echo ""
	@echo "── 分类与子专题 ──"
	@python3 scripts/topics.py
	@echo ""
	@echo "── 会议/期刊分级 ──"
	@python3 scripts/venues.py
	@echo ""
	@echo "── 链接提取与代码匹配 ──"
	@python3 scripts/links.py
	@echo ""
	@echo "── 数据源注册表契约 ──"
	@python3 scripts/sources.py --selftest
	@echo ""
	@echo "── 仓库一致性（工作流名/脚本引用/secrets） ──"
	@python3 scripts/selftest.py
	@echo ""
	@echo "── 服务层（配置 / 领域模型 / 检索 / BibTeX / Zotero 推送） ──"
	@python3 -m unittest discover -s tests -t . -q
	@echo ""
	@python3 scripts/export.py > /dev/null && echo "── 导出 ✅ ──"

service-test:
	@python3 -m unittest discover -s tests -t . -v

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "已清理 __pycache__"
