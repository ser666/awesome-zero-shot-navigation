"""zenav —— awesome-zero-shot-navigation 的服务层。

本包是本项目的**服务层（service layer）**，与采集层（`scripts/`）刻意分离：

    ┌──────────────────────────────────────────────────────────────┐
    │ 采集层  scripts/       零第三方依赖，跑在 GitHub Actions     │
    │         产出 → data/papers.db + docs/data/papers.json        │
    └──────────────────────────────────────────────────────────────┘
                              ↓ 文件 / HTTP（唯一的耦合点）
    ┌──────────────────────────────────────────────────────────────┐
    │ 服务层  zenav/         本包                                  │
    │   阅读方：Agent（MCP）/ 命令行 / 任何 Python 程序 / Zotero   │
    └──────────────────────────────────────────────────────────────┘

**为什么分两层**（设计取舍）：

1. **采集层保持零依赖** —— Actions 里不用 `pip install`，跑得快、
   也不会因为依赖腐坏在半年后突然失败。这条原则不能破坏。
2. **服务层允许有依赖** —— 它跑在本机/服务器（常驻），
   需要 MCP SDK 这类东西。把依赖关在这一层，脏不出去。
3. **两层只通过"数据文件"耦合** —— 服务层不认识任何采集脚本，
   采集层不认识服务层。任一侧重写都不影响另一侧。

用法：

    from zenav import AppConfig, PaperCatalog, PaperService

    cfg = AppConfig.load()
    svc = PaperService(PaperCatalog.from_config(cfg))
    svc.search("zero-shot object navigation", limit=5)
"""

from zenav.config import AppConfig
from zenav.errors import ConfigError, ZenavError
from zenav.infra.catalog import PaperCatalog
from zenav.services.papers import PaperService

__all__ = [
    "AppConfig",
    "ConfigError",
    "PaperCatalog",
    "PaperService",
    "ZenavError",
]

__version__ = "1.0.0"
