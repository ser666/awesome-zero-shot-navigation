"""Zotero 单向推送（需求 5）。

════════════════════════════════════════════════════════════════════════
 设计边界（Boss 2026-09-30 明确）—— 请勿擅自扩大
════════════════════════════════════════════════════════════════════════
  ✅ 做：**部署的服务 → 本地 Zotero** 单向推送新论文，分门别类放好
  ❌ 不做：从 Zotero 反过来读；不做双向同步；不做冲突合并

 为什么单向就够（这是本设计里最省事的一步）：
     Zotero **官方客户端自带云同步**。我们只要把条目写进 Zotero 云
     （Web API），本机 Zotero 打开后自动就同步下来了。
     ⇒ 因此**不需要**本地 Local API、**不需要**装插件、**不需要**常驻进程，
        也不需要自己实现任何同步逻辑 —— 官方的同步就是我们的同步。

三层结构（可分别测试、分别替换）：
     ZoteroClient      ← 只管 HTTP 细节（Zotero Web API v3 的形状）
     PushState         ← 只管"推过哪些"（幂等，防止重复入库）
     ZoteroPushService ← 业务编排（选论文 → 建目录 → 批量写 → 记状态）
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from zenav.config.models import ZoteroConfig
from zenav.config.secrets import SecretStore
from zenav.domain.paper import Paper
from zenav.errors import ConfigError, StateError, UpstreamError
from zenav.infra.catalog import PaperCatalog
from zenav.infra.http import HttpClient
from zenav.log import get_logger

log = get_logger(__name__)

# Zotero Web API 单次请求最多 50 个对象（官方限制）
ZOTERO_BATCH_LIMIT = 50
API_VERSION = "3"


@runtime_checkable
class ZoteroWriter(Protocol):
    """推送所需要的 Zotero 能力（**接口**，不是实现）。

    为什么用 Protocol 而不是直接依赖 `ZoteroClient` 类（依赖倒置原则）：

        业务编排（ZoteroPushService）真正需要的是"能建目录、能写条目"，
        而不是"用 HTTP 调 api.zotero.org"。

        好处：
          · 测试可以注入替身，**不需要网络和凭据**就能测完整的推送逻辑
          · 将来 Zotero 换 API 版本、或改用本地 Local API，
            只要新客户端满足这个协议，业务代码一行都不用改
          · 类型检查器能验证"替身与真身形状一致"，防止替身漂移

    ⚠️ 改本协议 = 破坏兼容，请同步改 `ZoteroClient` 与测试替身。
    """

    def check_access(self) -> dict[str, Any]: ...

    def list_collections(self) -> list[dict[str, Any]]: ...

    def create_collection(self, name: str, parent: str | None = None) -> str: ...

    def create_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

# 论文形态 → Zotero 条目类型
#
# ⚠️ 两个坑（都有测试兜着）：
#   1. `""`（空 venue_kind）**必须**映射到 preprint，不能落到 default_item_type。
#      数据里有 venue_kind 缺失/unknown 的条目（实测 75 篇 unknown），
#      它们没有任何"已发表"证据。若映射成 conferencePaper，
#      等于在 Zotero 里声称"这是会议论文" —— 与"撤稿/投稿中不算已发表"
#      是同一类**过度声称**错误。
#   2. 兜底方向永远是"更保守"，而不是"更响亮"。
_ITEM_TYPE_BY_KIND = {
    "journal": "journalArticle",
    "conference": "conferencePaper",
    "proceedings": "conferencePaper",
    "preprint": "preprint",
    "unknown": "preprint",
    "": "preprint",
    "other": "report",
}

# 分类名里的 / 会与"路径"语义混淆，建目录时换成这个
_SUBCOLLECTION_SEP = " · "


# ══════════════════════════════════════════════════════════════════════
#  一、HTTP 客户端：Zotero Web API v3
# ══════════════════════════════════════════════════════════════════════
class ZoteroClient:
    """Zotero Web API 的薄封装。

    只负责"把请求发对、把响应解析对"，不含任何业务判断。

    Args:
        api_base: 如 https://api.zotero.org
        api_key: 个人 API Key（需 write access）
        library_id: 数字 userID（或 group ID）
        library_type: user | group
    """

    def __init__(
        self,
        api_base: str,
        api_key: str,
        library_id: str,
        library_type: str = "user",
        *,
        http: HttpClient | None = None,
        request_interval: float = 0.6,
    ) -> None:
        if library_type not in ("user", "group"):
            raise ConfigError(f"library_type 必须是 user 或 group，收到 {library_type!r}")
        if not library_id.isdigit():
            raise ConfigError(
                f"library_id 必须是数字（收到 {library_id!r}）",
                hint="在 https://www.zotero.org/settings/keys 页面找 "
                     '「Your userID for use in API calls is 1234567」那串数字',
            )
        self.api_base = api_base.rstrip("/")
        self.library_id = library_id
        self.library_type = library_type
        self._api_key = api_key
        self._http = http or HttpClient(timeout=30, retries=3, backoff=1.5)
        self._interval = request_interval
        self._last_call = 0.0

    # ── 基础 ────────────────────────────────────────────────────────
    @property
    def _prefix(self) -> str:
        seg = "users" if self.library_type == "user" else "groups"
        return f"{self.api_base}/{seg}/{self.library_id}"

    def _headers(self) -> dict[str, str]:
        return {
            "Zotero-API-Key": self._api_key,     # ⚠️ HttpClient 日志会自动打码
            "Zotero-API-Version": API_VERSION,
        }

    def _throttle(self) -> None:
        """本地限速：Zotero 对写操作有速率限制，主动放慢比被 429 强。"""
        import time
        gap = time.time() - self._last_call
        if gap < self._interval:
            time.sleep(self._interval - gap)
        self._last_call = time.time()

    def _request(self, method: str, path: str, **kw: Any):
        self._throttle()
        url = f"{self._prefix}{path}"
        headers = self._headers()
        headers.update(kw.pop("headers", {}) or {})
        resp = self._http.request(method, url, headers=headers,
                                  raise_for_status=True, **kw)
        return resp

    # ── 读 ──────────────────────────────────────────────────────────
    def check_access(self) -> dict[str, Any]:
        """验证 Key 是否可用（并顺带拿到用户名，便于确认账号对不对）。

        用这个端点而不是 /items，因为它在**没有任何条目**时也能验证，
        而且能把"Key 无效"和"库是空的"两种情况区分开。
        """
        resp = self._request("GET", "/collections", params={"limit": 1})
        return {
            "ok": True,
            "library": f"{self.library_type}:{self.library_id}",
            "total_collections": int(resp.headers.get("total-results", 0) or 0),
        }

    def list_collections(self) -> list[dict[str, Any]]:
        """列出全部 collections（自动翻页）。"""
        out: list[dict[str, Any]] = []
        start = 0
        while True:
            resp = self._request("GET", "/collections",
                                 params={"limit": 100, "start": start})
            batch = resp.json() if resp.body else []
            if not isinstance(batch, list):
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            start += 100
        return out

    # ── 写 ──────────────────────────────────────────────────────────
    def create_collection(self, name: str, parent: str | None = None) -> str:
        """建 collection，返回 key。`parent` 为父 collection 的 key。"""
        item: dict[str, Any] = {"name": name}
        # Zotero 用 parentCollection=False 表示"顶层"，而不是 null
        item["parentCollection"] = parent if parent else False
        resp = self._request("POST", "/collections", json_body=[item])
        return self._extract_key(resp, name)

    def create_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量创建条目（自动按 50 分批）。

        Returns:
            每个成功项：{"index": i, "key": "...", "title": "..."}
        """
        created: list[dict[str, Any]] = []
        for i in range(0, len(items), ZOTERO_BATCH_LIMIT):
            chunk = items[i:i + ZOTERO_BATCH_LIMIT]
            resp = self._request("POST", "/items", json_body=chunk)
            payload = resp.json() if resp.body else {}
            successful = payload.get("successful") or {}
            failed = payload.get("failed") or {}
            for idx_s, obj in successful.items():
                local_idx = i + int(idx_s)
                created.append({
                    "index": local_idx,
                    "key": (obj or {}).get("key", ""),
                    "title": chunk[int(idx_s)].get("title", ""),
                })
            for idx_s, err in failed.items():
                local_idx = i + int(idx_s)
                log.warning("条目写入失败 #%d %r：%s",
                            local_idx, chunk[int(idx_s)].get("title", "")[:60], err)
        return created

    def _extract_key(self, resp: Any, label: str) -> str:
        """从 Zotero 的写入响应里取 key。

        Zotero 的写入响应形状：{"successful": {"0": {"key": "ABCD1234", ...}},
                               "failed": {"1": {...}}}
        用 "0" 这种**字符串下标**，很容易踩坑（写成 int 0 就取不到）。
        """
        payload = resp.json() if resp.body else {}
        ok = (payload or {}).get("successful") or {}
        if not ok:
            raise UpstreamError(
                f"创建失败（{label}）",
                hint=f"Zotero 返回：{json.dumps(payload, ensure_ascii=False)[:300]}",
                status=getattr(resp, "status", None),
            )
        first = next(iter(ok.values())) or {}
        key = first.get("key")
        if not key:
            raise UpstreamError(f"创建成功但未返回 key（{label}）",
                                hint=str(first)[:200])
        return str(key)


# ══════════════════════════════════════════════════════════════════════
#  二、幂等状态
# ══════════════════════════════════════════════════════════════════════
@dataclass
class PushState:
    """记录"哪些论文已推送过"。

    为什么必须持久化（而不是每次重推）：
        重推会在 Zotero 里产生重复条目 —— 一旦有几十条重复，
        手工清理比写这个状态文件麻烦得多。
    ⚠️ 状态丢失的后果只是"可能重复推一次"，所以这里用"尽力而为"的策略：
       写失败只告警，不中断推送（宁可不幂等，也不要整批失败）。
    """

    path: pathlib.Path
    library: str = ""
    collections: dict[str, str] = field(default_factory=dict)   # 目录路径 → key
    pushed: dict[str, dict[str, str]] = field(default_factory=dict)  # 指纹 → 记录
    version: int = 1

    @classmethod
    def load(cls, path: str | pathlib.Path, library: str = "") -> PushState:
        p = pathlib.Path(path)
        if not p.is_file():
            return cls(path=p, library=library)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # 状态损坏不该阻塞推送 —— 备份坏的、重新开始
            backup = p.with_suffix(p.suffix + ".corrupt")
            try:
                p.replace(backup)
            except OSError:
                pass
            log.warning("状态文件损坏，已备份到 %s 并重建：%s", backup, exc)
            return cls(path=p, library=library)
        return cls(
            path=p,
            library=str(raw.get("library", library)),
            collections=dict(raw.get("collections") or {}),
            pushed=dict(raw.get("pushed") or {}),
            version=int(raw.get("version", 1)),
        )

    # ── 查询/更新 ───────────────────────────────────────────────────
    def is_pushed(self, fingerprint: str) -> bool:
        return fingerprint in self.pushed

    def mark(self, fingerprint: str, item_key: str, collection_key: str = "") -> None:
        self.pushed[fingerprint] = {
            "k": item_key,
            "c": collection_key,
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }

    def collection_key(self, path: str) -> str | None:
        return self.collections.get(path)

    def set_collection(self, path: str, key: str) -> None:
        self.collections[path] = key

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "pushed_items": len(self.pushed),
            "known_collections": len(self.collections),
        }

    # ── 持久化（原子写） ────────────────────────────────────────────
    def save(self) -> None:
        """原子写：先写 .tmp，fsync 后 replace 覆盖。

        ⚠️ 为什么不能直接 open(path, "w")：
        写到一半进程被杀会留下**半截 JSON** —— 下次启动状态全丢，
        表现为"所有论文重新推一遍"，在 Zotero 里生成大批重复条目。
        """
        payload = {
            "version": self.version,
            "library": self.library,
            "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "collections": self.collections,
            "pushed": self.pushed,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            raise StateError(
                f"写入状态文件失败：{self.path}",
                hint=f"{exc}\n     检查目录是否可写；状态丢失只会导致下次重复推送",
            ) from exc


# ══════════════════════════════════════════════════════════════════════
#  三、业务编排：单向推送
# ══════════════════════════════════════════════════════════════════════
@dataclass
class PushReport:
    """一次推送的结果（结构化，便于 Agent/CLI 展示）。"""

    dry_run: bool = False
    candidates: int = 0          # 符合条件的论文数
    already: int = 0             # 已推过（跳过）
    pushed: int = 0              # 本次成功推送
    failed: int = 0
    collections_created: list[str] = field(default_factory=list)   # 真的建了
    collections_planned: list[str] = field(default_factory=list)   # 演练时：将会建
    by_category: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    state_saved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "candidates": self.candidates,
            "skipped_already_pushed": self.already,
            "pushed": self.pushed,
            "failed": self.failed,
            "collections_created": self.collections_created,
            "collections_planned": self.collections_planned,
            "by_category": self.by_category,
            "errors": self.errors[:10],
            "state_saved": self.state_saved,
        }

    def summary(self) -> str:
        mode = "（演练，未实际写入）" if self.dry_run else ""
        lines = [
            f"Zotero 推送{mode}",
            f"  候选 {self.candidates} ｜ 已存在跳过 {self.already} "
            f"｜ ✅ 新推 {self.pushed} ｜ ❌ 失败 {self.failed}",
        ]
        if self.collections_created:
            lines.append(f"  新建目录 {len(self.collections_created)} 个："
                         + "、".join(self.collections_created[:5])
                         + ("…" if len(self.collections_created) > 5 else ""))
        if self.dry_run and self.collections_planned:
            lines.append(f"  将会新建目录 {len(self.collections_planned)} 个："
                         + "、".join(self.collections_planned[:5])
                         + ("…" if len(self.collections_planned) > 5 else "")
                         + "（演练未创建）")
        if self.by_category:
            lines.append("  按分类："
                         + " ｜ ".join(f"{k} {v}" for k, v in
                                      sorted(self.by_category.items(),
                                             key=lambda x: -x[1])))
        if self.errors:
            lines.append(f"  错误（前 3 条）：")
            lines.extend(f"    · {e}" for e in self.errors[:3])
        return "\n".join(lines)


class ZoteroPushService:
    """把论文推送进 Zotero（单向）。

    典型用法：

        svc = ZoteroPushService.from_config(cfg)
        svc.status()                       # 先看连不连得上、推过多少
        svc.push(dry_run=True)             # 演练
        svc.push()                         # 真推
    """

    def __init__(
        self,
        catalog: PaperCatalog,
        config: ZoteroConfig,
        state: PushState,
        client: ZoteroWriter | None = None,
    ) -> None:
        self._catalog = catalog
        self._cfg = config
        self._state = state
        self._client = client      # 允许注入替身 → 可离线测试（见 ZoteroWriter）

    # ── 构造 ────────────────────────────────────────────────────────
    @classmethod
    def from_config(
        cls,
        cfg: Any,
        catalog: PaperCatalog | None = None,
        secrets: SecretStore | None = None,
    ) -> ZoteroPushService:
        """按配置装配 —— ⭐ 会**自动选择后端**（本地 API / Web API）。

        backend="auto"（默认）的处理：
            先探测 Zotero 本地 API 是否可用（只读探测，**不触发授权弹窗**）：
              可用   → 用 local（不用云端、不用账号、不用 API Key）
              不可用 → 回落到 web（需要 zotero.org 账号 + API Key）

        Args:
            cfg: AppConfig。
            catalog: 论文目录；不传则按 cfg.catalog 自动建一个。
            secrets: 密钥读取器（**仅 web 后端需要**）。
        """
        from zenav.infra.catalog import create_catalog
        from zenav.services.zotero_local import LocalKeyStore, ZoteroLocalClient

        zc: ZoteroConfig = cfg.zotero
        if not zc.enabled:
            raise ConfigError(
                "Zotero 推送未启用",
                hint="把 config/settings.toml 里 [zotero] enabled 设为 true",
            )

        backend = cls._resolve_backend(zc)

        if backend == "local":
            key_path = (pathlib.Path(zc.local_key_file)
                        if pathlib.Path(zc.local_key_file).is_absolute()
                        else pathlib.Path(cfg.root) / zc.local_key_file)
            client: Any = ZoteroLocalClient(
                base_url=zc.local_api_base,
                user_id=zc.local_user_id,
                app_name=zc.local_app_name,
                key_store=LocalKeyStore.load(key_path),
            )
            library_label = f"local:{zc.local_api_base}/users/{zc.local_user_id}"
            log.info("Zotero 后端：**本地 API**（%s）—— 不用云端/账号/API Key",
                     zc.local_api_base)
        else:
            store = secrets or _secret_store(cfg)
            api_key = store.require(
                zc.api_key_env, "Zotero 推送（web 后端）",
                hint=(
                    "⚠️ 若你**不想用 Zotero 云端**，不必配这些 Key ——\n"
                    "     改用本地 API 即可：\n"
                    "       ① 装 Zotero 10+ 并让它保持运行\n"
                    "       ② Zotero → 设置 → 高级 → 勾选 "
                    "「Allow other applications … communicate with Zotero」\n"
                    "       ③ config/settings.toml 里 [zotero] backend = \"local\"\n"
                    "\n"
                    "     若确实要用云端（web 后端）：\n"
                    "       ① 生成 Key：https://www.zotero.org/settings/keys/new\n"
                    "          （务必勾选 Allow write access）\n"
                    f"       ② 填入 config/secrets.env 的 {zc.api_key_env}\n"
                    f"       ③ 同时把数字 userID 填进 {zc.library_id_env}"
                ),
            )
            lib_id = store.require(
                zc.library_id_env, "Zotero 推送（web 后端）",
                hint="见 https://www.zotero.org/settings/keys 页面的 "
                     "「Your userID for use in API calls」",
            )
            client = ZoteroClient(
                api_base=zc.api_base, api_key=api_key, library_id=lib_id,
                library_type=zc.library_type, request_interval=zc.push_interval,
            )
            library_label = f"{zc.library_type}:{lib_id}"
            log.info("Zotero 后端：Web API（%s）—— 需要账号与云同步", zc.api_base)

        state_path = (pathlib.Path(zc.state_file)
                      if pathlib.Path(zc.state_file).is_absolute()
                      else pathlib.Path(cfg.root) / zc.state_file)
        state = PushState.load(state_path, library=library_label)
        return cls(
            catalog=catalog or create_catalog(cfg.catalog, cfg.root),
            config=zc,
            state=state,
            client=client,
        )

    @staticmethod
    def _resolve_backend(zc: ZoteroConfig) -> str:
        """决定用哪个后端。auto → 探测本地（只读，不弹授权框）。"""
        if zc.backend in ("local", "web"):
            return zc.backend
        # auto
        try:
            from zenav.services.zotero_local import probe_local

            probe = probe_local(zc.local_api_base)
        except Exception as exc:                        # noqa: BLE001
            log.debug("本地探测异常：%s", exc)
            probe = {"available": False, "error": str(exc)}
        if probe.get("available"):
            log.info("backend=auto：检测到本地 Zotero（server_id=%s…）→ 用 local",
                     str(probe.get("server_id", ""))[:8])
            return "local"
        log.info("backend=auto：本地 Zotero 不可用（%s）→ 回落 web",
                 probe.get("error", "未知原因"))
        return "web"

    # ── 状态 ────────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        """连通性 + 推送进度（不含密钥值）。"""
        client = self._client
        mode = "local" if type(client).__name__ == "ZoteroLocalClient" else "web"
        out: dict[str, Any] = {
            "enabled": self._cfg.enabled,
            "backend_configured": self._cfg.backend,
            "backend_in_use": mode,
            "cloud": mode == "web",
            "library": self._state.library,
            "collection_root": self._cfg.collection_root,
            "state_file": str(self._state.path),
            **self._state.stats,
        }
        if self._client is not None:
            try:
                out["connection"] = self._client.check_access()
            except Exception as exc:            # noqa: BLE001 - 状态检查不该抛
                out["connection"] = {"ok": False, "error": str(exc)[:300]}
        total = self._catalog.load().total
        out["catalog_total"] = total
        out["remaining"] = max(0, total - out["pushed_items"])
        return out

    # ── 推送 ────────────────────────────────────────────────────────
    def push(
        self,
        *,
        category: str = "",
        since_days: int = 0,
        limit: int = 0,
        dry_run: bool = False,
        sync_collections: bool = True,
    ) -> PushReport:
        """把尚未推送的论文推进 Zotero。

        Args:
            category: 只推某个分类（空 = 全部）。
            since_days: 只推最近 N 天发表的（0 = 不限）。
            limit: 本次最多推多少条（0 = 用配置里的 max_batch）。
            dry_run: 只报告将要做什么，不写入任何东西（联调用）。
            sync_collections: 是否确保 Zotero 端目录存在。
        """
        report = PushReport(dry_run=dry_run)
        data = self._catalog.load()

        # ① 选候选
        cutoff = ""
        if since_days > 0:
            cutoff = (_dt.date.today()
                      - _dt.timedelta(days=since_days)).isoformat()
        candidates = [
            p for p in data.papers
            if (not category or category.lower() in p.category.lower())
            and (not cutoff or (p.date and p.date[:10] >= cutoff))
        ]
        cap = limit or self._cfg.max_batch
        # 新→旧：先推最新的（万一中途失败，拿到的是最有价值的）
        candidates.sort(key=lambda p: (p.date or "0000-00-00"), reverse=True)

        todo: list[Paper] = []
        for p in candidates:
            if self._state.is_pushed(p.fingerprint()):
                report.already += 1
                continue
            todo.append(p)
            if cap and len(todo) >= cap:
                break
        report.candidates = len(todo)

        if not todo:
            log.info("没有需要推送的新论文")
            return report

        # ② ⭐ 演练必须**完全不写入** —— 包括不建目录。
        #
        #    ⚠️ 这里踩过一个真 bug：早先的实现在 dry_run 时也调了
        #       `_ensure_collections()`，理由是"否则演练会误报目录不存在"。
        #       后果有两个，都很糟：
        #         · 本地后端会**弹授权对话框**（用户没要求写入，却看到弹窗）
        #         · 真的在用户库里**建了目录** —— "演练"却产生了副作用
        #       ⇒ 正确做法：演练时只**读**（list_collections 是只读的），
        #         算出"将会建哪些目录"并报告，一个字节都不写。
        if dry_run:
            report.collections_planned = self._plan_collections(todo)
            report.by_category = _count_by_category(todo)
            report.pushed = 0
            return report

        # ③ 目录（仅真实推送时创建）
        if sync_collections and self._client is not None:
            self._ensure_collections(todo, report)

        if self._client is None:
            raise ConfigError(
                "未配置 Zotero 客户端（离线模式），无法执行真实推送",
                hint="用 from_config() 构造，或传 dry_run=True 只做演练",
            )

        # ③ 逐个分类推送（同一 collection 的条目一起写 → 请求数最少）
        by_cat: dict[str, list[Paper]] = {}
        for p in todo:
            by_cat.setdefault(p.category, []).append(p)

        for cat, papers in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
            coll_key = self._state.collection_key(cat) or ""
            items = [self.to_zotero_item(p, coll_key) for p in papers]
            try:
                created = self._client.create_items(items)
            except UpstreamError as exc:
                report.failed += len(papers)
                report.errors.append(f"{cat}: {exc.message}")
                continue

            report.pushed += len(created)
            report.by_category[cat] = len(created)
            report.failed += max(0, len(papers) - len(created))
            self._mark_pushed(papers, created, coll_key)

        # ④ 落盘状态
        try:
            self._state.save()
            report.state_saved = True
        except StateError as exc:
            report.errors.append(f"状态保存失败：{exc.message}")
            log.warning("状态未保存 —— 下次可能重复推送已成功的条目")

        return report

    # ── 内部 ────────────────────────────────────────────────────────
    def _plan_collections(self, papers: list[Paper]) -> list[str]:
        """演练用：算出"将会新建哪些目录" —— **只读，不写**。

        `list_collections()` 属于读请求（本地/Web API 的读都无需认证），
        所以演练阶段调它是安全的，不会弹授权框、不会产生副作用。
        """
        if self._client is None:
            return []
        try:
            existing = {c.get("data", {}).get("name")
                        for c in self._client.list_collections()}
        except Exception as exc:                        # noqa: BLE001
            log.debug("演练阶段读取目录失败（忽略）：%s", exc)
            return []
        planned: list[str] = []
        root = self._cfg.collection_root
        if root not in existing:
            planned.append(root)
        if self._cfg.create_subcollections:
            for cat in sorted({p.category for p in papers}):
                name = _subcollection_name(cat, root)
                if name not in existing:
                    planned.append(name)
        return planned

    def _ensure_collections(self, papers: list[Paper], report: PushReport) -> None:
        """确保顶层目录 + 各分类子目录都存在（幂等）。"""
        existing = {c.get("data", {}).get("name"): c.get("key")
                    for c in self._client.list_collections()}

        root_name = self._cfg.collection_root
        root_key = self._state.collection_key(root_name) or existing.get(root_name)
        if not root_key:
            root_key = self._client.create_collection(root_name, parent=None)
            report.collections_created.append(root_name)
            log.info("新建 Zotero 顶层目录：%s", root_name)
        self._state.set_collection(root_name, root_key)

        if not self._cfg.create_subcollections:
            for p in papers:
                self._state.set_collection(p.category, root_key)
            return

        for cat in sorted({p.category for p in papers}):
            if self._state.collection_key(cat):
                continue
            sub_name = _subcollection_name(cat, root_name)
            key = existing.get(sub_name)
            if not key:
                key = self._client.create_collection(sub_name, parent=root_key)
                report.collections_created.append(sub_name)
                log.info("新建 Zotero 子目录：%s", sub_name)
            self._state.set_collection(cat, key)

    def _mark_pushed(self, papers: list[Paper], created: list[dict], coll_key: str) -> None:
        """把写入成功的条目记进状态。

        ⚠️ 对齐方式：`create_items` 返回的 `index` 是**本批 items 列表内的下标**，
        而 `papers` 与 `items` 是同一个顺序构造的 ⇒ 直接按下标取即可。
        （早期版本用全局偏移量算，分类一变就错位 —— 已改成按批内下标。）
        """
        for rec in created:
            idx = rec["index"]
            if 0 <= idx < len(papers):
                self._state.mark(papers[idx].fingerprint(), rec["key"], coll_key)

    # ── 领域 → Zotero 条目 ──────────────────────────────────────────
    def to_zotero_item(self, paper: Paper, collection_key: str = "") -> dict[str, Any]:
        """把 Paper 转成 Zotero Web API 的 item 结构。

        字段对照（容易漏的几处已注明）：
            venue_kind → itemType      用映射表，别直接塞（Zotero 会拒收）
            authors    → creators[]    "He, Yu" → lastName=He, firstName=Yu
            category   → collections[] 决定它落在哪个子目录
            code/project → extra         Zotero 没有对应字段，放 extra（可搜到）
        """
        item_type = _ITEM_TYPE_BY_KIND.get(
            (paper.venue_kind or "").lower(), self._cfg.default_item_type)

        item: dict[str, Any] = {
            "itemType": item_type,
            "title": paper.title,
            "creators": [_creator(a) for a in paper.authors],
            "tags": [{"tag": t} for t in paper.topics],
            "collections": [collection_key] if collection_key else [],
            "extra": _extra_note(paper),
        }
        if paper.date:
            item["date"] = paper.date
        if paper.abstract:
            item["abstractNote"] = paper.abstract
        if paper.doi:
            item["DOI"] = paper.doi
        if paper.url:
            item["url"] = paper.url

        # 会议/期刊名：Zotero 对不同 itemType 用不同字段，塞错会被静默忽略
        venue_name = paper.venue_full or paper.venue_short
        if venue_name:
            if item_type == "journalArticle":
                item["publicationTitle"] = venue_name
            elif item_type == "conferencePaper":
                item["proceedingsTitle"] = venue_name
            elif item_type in ("preprint", "report"):
                item["institution"] = venue_name

        # arXiv 是 preprint 的必需/推荐字段
        if paper.arxiv_id and item_type == "preprint":
            item["repository"] = "arXiv"
            item["archiveID"] = f"arXiv:{paper.arxiv_id}"

        return {k: v for k, v in item.items() if v not in ("", [], None)}


# ══════════════════════════════════════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════════════════════════════════════
def _secret_store(cfg: Any) -> SecretStore:
    from zenav.config.loader import load_secret_store
    return load_secret_store(cfg.root)


def _creator(full_name: str) -> dict[str, str]:
    """作者名 → Zotero creator 结构。

    数据里的作者是 "He, Yu" 这种"姓, 名"格式（BibTeX 风格），
    而 Zotero 要 firstName/lastName 分开。含中间名/后缀时做保守处理：
    拆不开就整体塞 lastName（Zotero 会照原样显示，不会报错）。
    """
    name = (full_name or "").strip()
    if not name:
        return {"creatorType": "author", "lastName": ""}
    if "," in name:
        last, _, first = name.partition(",")
        return {
            "creatorType": "author",
            "lastName": last.strip(),
            "firstName": first.strip(),
        }
    parts = name.split()
    if len(parts) == 1:
        return {"creatorType": "author", "lastName": parts[0]}
    return {
        "creatorType": "author",
        "lastName": parts[-1],
        "firstName": " ".join(parts[:-1]),
    }


def _extra_note(paper: Paper) -> str:
    """Zotero 的 `extra` 字段（纯文本，可被搜索）。

    把 Zotero 没有结构化字段的信息放这里：分类、代码、项目页、来源。
    """
    lines = [f"Category: {paper.category}"]
    if paper.venue_display() not in ("—", "Preprint"):
        lines.append(f"Venue: {paper.venue_display()}")
    if paper.code_url:
        lines.append(f"Code: {paper.code_url}")
    if paper.project_url:
        lines.append(f"Project: {paper.project_url}")
    if paper.arxiv_id:
        lines.append(f"arXiv: {paper.arxiv_id}")
    if paper.sources:
        lines.append(f"Sources: {', '.join(paper.sources)}")
    lines.append("Imported by: awesome-zero-shot-navigation")
    return "\n".join(lines)


def _subcollection_name(category: str, root: str) -> str:
    """子目录名。

    ⚠️ 分类名里可能含 "/"（如 "LLM / VLM Navigation Agents"），
    若直接当路径用会造成歧义，统一换成中点。
    """
    return (category or "Other").replace("/", _SUBCOLLECTION_SEP)


def _count_by_category(papers: list[Paper]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in papers:
        out[p.category] = out.get(p.category, 0) + 1
    return out
