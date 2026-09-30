"""服务层 —— 业务逻辑，与传输方式无关。

三个入口共用本层（这是"可复用性"的落点）：
    interfaces/mcp_server.py   Agent（MCP 协议）
    interfaces/cli.py          命令行 / 人
    外部 Python 程序           `from zenav.services import PaperService`
"""

from zenav.services.bibtex import (
    citation_key,
    entry_type,
    escape_bibtex,
    to_bibtex,
    to_bibtex_entry,
)
from zenav.services.papers import (
    SORT_FIELDS,
    PaperService,
    SearchResult,
    render_overview_markdown,
)
from zenav.services.zotero import (
    PushReport,
    PushState,
    ZoteroClient,
    ZoteroPushService,
)

__all__ = [
    "SORT_FIELDS",
    "PaperService",
    "PushReport",
    "PushState",
    "SearchResult",
    "ZoteroClient",
    "ZoteroPushService",
    "citation_key",
    "entry_type",
    "escape_bibtex",
    "render_overview_markdown",
    "to_bibtex",
    "to_bibtex_entry",
]
