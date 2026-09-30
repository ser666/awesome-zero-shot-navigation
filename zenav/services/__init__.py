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
    ZoteroWriter,
)
from zenav.services.zotero_local import (
    DEFAULT_LOCAL_BASE,
    LocalKeyStore,
    ZoteroLocalClient,
    probe_local,
)

__all__ = [
    "DEFAULT_LOCAL_BASE",
    "LocalKeyStore",
    "PaperService",
    "PushReport",
    "PushState",
    "SORT_FIELDS",
    "SearchResult",
    "ZoteroClient",
    "ZoteroLocalClient",
    "ZoteroPushService",
    "ZoteroWriter",
    "citation_key",
    "entry_type",
    "escape_bibtex",
    "probe_local",
    "render_overview_markdown",
    "to_bibtex",
    "to_bibtex_entry",
]
