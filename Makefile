.PHONY: help update full audit export serve test enrich backfill probe clean

help:
	@echo "awesome-zero-shot-navigation — 常用命令"
	@echo ""
	@echo "  make update    增量采集（回看 21 天）→ 导出 README + 网站数据"
	@echo "  make enrich    补数据（引用数 / 开放 PDF / 代码仓库），限流"
	@echo "  make backfill  只重算派生字段（会议分级 / 子专题 / 链接），不联网"
	@echo "  make export    只导出（不采集）"
	@echo "  make serve     本地预览网站 → http://127.0.0.1:8210"
	@echo "  make test      跑全部规则自测（离线）"
	@echo "  make audit     抽查数据质量（误收 / 分布）"
	@echo "  make probe     探测各学术源可用性（建议在 Actions 上跑）"
	@echo "  make full      全量采集（从 2019 年起，慢）"
	@echo ""

update:
	python3 scripts/pipeline.py --days 21
	python3 scripts/pipeline.py --refilter
	python3 scripts/pipeline.py --backfill
	python3 scripts/export.py

enrich:
	python3 scripts/pipeline.py --enrich --budget 900

backfill:
	python3 scripts/pipeline.py --backfill
	python3 scripts/export.py

export:
	python3 scripts/export.py

serve:
	python3 -m http.server -d docs 8210 --bind 127.0.0.1

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
	@python3 scripts/export.py > /dev/null && echo "── 导出 ✅ ──"

audit:
	python3 scripts/audit.py

probe:
	python3 scripts/probe_sources.py

full:
	python3 scripts/pipeline.py --full --budget 1800
	python3 scripts/export.py

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "已清理 __pycache__"
