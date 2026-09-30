"""基础设施层 —— 所有 IO 都关在这一层。

纪律：
    · 上层（services）只调本层暴露的接口，不直接 urllib/sqlite/open()
    · 本层不包含业务规则（"什么算新论文"是 services 的事）
    · 外部世界的变化（API 改格式、换数据库）只需要改这里
"""

from zenav.infra.catalog import (
    CatalogData,
    LocalJsonCatalog,
    PaperCatalog,
    RemoteJsonCatalog,
    SqliteCatalog,
    create_catalog,
)
from zenav.infra.http import HttpClient, HttpResponse, redact_headers

__all__ = [
    "CatalogData",
    "HttpClient",
    "HttpResponse",
    "LocalJsonCatalog",
    "PaperCatalog",
    "RemoteJsonCatalog",
    "SqliteCatalog",
    "create_catalog",
    "redact_headers",
]
