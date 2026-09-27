.PHONY: help update full audit export serve test clean

help:
	@echo "awesome-zero-shot-navigation — 常用命令"
	@echo ""
	@echo "  make update   增量采集（最近 14 天）→ 导出 README + 网站数据"
	@echo "  make full     全量采集（2021 年起）→ 导出"
	@echo "  make export   只重新导出（不采集）"
	@echo "  make audit    质量审计（抽查误收/分类）"
	@echo "  make test     跑相关性规则自测"
	@echo "  make serve    本地预览网站（http://localhost:8000）"
	@echo "  make clean    清理 __pycache__"

update:
	python3 scripts/pipeline.py
	python3 scripts/export.py

full:
	python3 scripts/pipeline.py --full
	python3 scripts/export.py

export:
	python3 scripts/export.py

audit:
	python3 scripts/audit.py

test:
	python3 scripts/relevance.py
	python3 scripts/sources.py

serve: export
	python3 -m http.server -d docs 8000

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "已清理"
