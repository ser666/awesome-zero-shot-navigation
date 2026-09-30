"""Zotero **本地** API 客户端 —— 不用 Zotero 云端（Boss 明确要求）。

════════════════════════════════════════════════════════════════════════
 为什么这条路更好（vs 官方 Web API + 云同步）
════════════════════════════════════════════════════════════════════════
              Web API（云端）              ⭐ 本地 API
  账号        需要 zotero.org 账号           不需要
  凭据        需要网站生成的 API Key          不需要（Zotero 弹窗授权一次）
  插件        不需要                        不需要
  网络        必须联网                       ⭐ 完全离线
  限速        有                            无
  延迟        500–1500ms                    ⭐ 8–60ms
  数据位置    先到 Zotero 云                 ⭐ 只在你本机

════════════════════════════════════════════════════════════════════════
 官方协议（已从文档 + 现成实现源码核实，非推测）
════════════════════════════════════════════════════════════════════════
  base URL     http://127.0.0.1:23119/api/
  库前缀        /users/0        （0 = 当前登录用户；群组用 /groups/<id>）
  版本         仅支持 API v3（仅此一版）

  ⚠️ 写入必须同时带**两个**头，缺一不可：
    Zotero-Server-ID   标识"哪一份 Zotero 实例" —— 缺失 428、不匹配 412
                        （防止客户端缓存的旧数据被写进切换后的另一个库）
    Zotero-API-Key     本地 API Key —— 每个写请求都要

  授权流程（写入的前提）：
    POST /api/local/authorize   body {"appName": "..."}
      → Zotero **弹出确认对话框**，三个选项：
          Allow        签发**一次性** key（写完即失效）
          Always Allow 签发**持久** key  ← 必须选这个
          Deny         拒绝（403）
      → 返回 {"key": "<32 字符>"}

  ⚠️ 选 "Allow" 的后果：**每次写入都会弹窗**（一次性 key 被消费后下次写返回 401，
     这是**预期行为**不是故障）。所以一定要选 Always Allow。

  错误码处理（客户端必须做，否则体验很差）：
    401  key 缺失或一次性 key 已消费 → 重新授权一次 + **重放请求**
    412  Zotero 重启/换了数据目录    → 刷新 Server-ID + **重试一次**
    428  写请求没带 Server-ID        → 同上
    429  授权弹窗过频                → 读 Retry-After 等待

  前置设置（一次性）：
    Zotero → 设置 → 高级 → 勾选
      "Allow other applications on this computer to communicate with Zotero"
    未勾选时所有请求返回 403。

  ⚠️ 版本硬门槛：**写入需要 Zotero 10+**。Zotero 7–9 的本地 API 是只读的
     （读能用、写必失败）。见 `check_access()` 的版本提示。
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any

from zenav.errors import AuthError, ConfigError, StateError, UpstreamError
from zenav.infra.http import HttpClient, HttpResponse
from zenav.log import get_logger

log = get_logger(__name__)

DEFAULT_LOCAL_BASE = "http://127.0.0.1:23119"
DEFAULT_APP_NAME = "awesome-zero-shot-navigation"
API_VERSION = "3"

# 一次性 key 被消费后 Zotero 返回 401 —— 属预期，要自动重授权并重放
RETRY_ON_401 = True
# Zotero 重启/切换数据目录 → 需刷新 Server-ID 后重试
REFRESH_CODES = (412, 428)
# 本地写入单次最多 50 个对象（与 Web API 一致）
LOCAL_BATCH_LIMIT = 50


# ══════════════════════════════════════════════════════════════════════
#  Key 存储：按 Server-ID 分区
# ══════════════════════════════════════════════════════════════════════
@dataclass
class LocalKeyStore:
    """本地 API Key 的持久化。

    ⚠️ 为什么要**按 Server-ID 分区**（而不是只存一个 key）：
    127.0.0.1:23119 是固定地址，指向"当前运行的那份 Zotero"。
    用户若切换数据目录/配置，同一地址会变成**另一个库**，
    而 key 只在签发它的那个实例上有效 —— 不分区就会拿着 A 库的 key 去写 B 库。

    文件权限 0600：它等同"改写你文献库的权力"。
    """

    path: pathlib.Path
    keys: dict[str, str] = field(default_factory=dict)
    version: int = 1

    @classmethod
    def load(cls, path: str | pathlib.Path) -> LocalKeyStore:
        p = pathlib.Path(path)
        if not p.is_file():
            return cls(path=p)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            keys = raw.get("keys")
            if not isinstance(keys, dict):
                keys = {}
            return cls(path=p, keys={str(k): str(v) for k, v in keys.items()},
                       version=int(raw.get("version", 1)))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("本地 key 库损坏，将重建（%s）：%s", p, exc)
            return cls(path=p)

    def get(self, server_id: str) -> str | None:
        return self.keys.get(server_id)

    def set(self, server_id: str, key: str) -> None:
        self.keys[server_id] = key
        self.save()

    def remove(self, server_id: str) -> None:
        self.keys.pop(server_id, None)
        self.save()

    def save(self) -> None:
        """原子写 + 0600 权限（写一半被杀会丢全部 key，进而每次都要弹窗）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": self.version, "keys": self.keys}
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except OSError as exc:
            raise StateError(f"写入本地 key 库失败：{self.path}", hint=str(exc)) from exc


# ══════════════════════════════════════════════════════════════════════
#  客户端
# ══════════════════════════════════════════════════════════════════════
class ZoteroLocalClient:
    """通过 Zotero 本地 API 读写（实现 `ZoteroWriter` 协议）。

    与 `ZoteroClient`（Web API）**接口完全一致** ⇒
    业务层（ZoteroPushService）不需要知道用的是哪个 —— 这就是当初把
    Zotero 能力定义成 Protocol 的价值。
    """

    def __init__(
        self,
        base_url: str = DEFAULT_LOCAL_BASE,
        *,
        user_id: str = "0",
        app_name: str = DEFAULT_APP_NAME,
        key_store: LocalKeyStore | None = None,
        http: HttpClient | None = None,
        auto_authorize: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.app_name = app_name
        # 默认把 key 存在仓库的 data/ 下；调用方通常会用配置覆盖
        self.keys = key_store or LocalKeyStore.load(
            pathlib.Path("data/zotero_local_keys.json"))
        # ⚠️ 超时故意设短（本地 API 响应 8–60ms）：
        #    实测在 WSL(mirrored) 下，**连不通的本地端口是"超时"而不是"拒绝"**
        #    ⇒ 用 20s 超时会让人以为程序卡死（一轮 status 能等 40 秒）。
        #    这里 8s 足够正常响应，失败也能很快反馈。
        self._http = http or HttpClient(timeout=8, retries=1, backoff=0.6)
        self._auto_authorize = auto_authorize
        self._server_id: str | None = None
        self._key: str | None = None

    # ── 协议实现 ────────────────────────────────────────────────────
    def check_access(self) -> dict[str, Any]:
        """连通性 + 版本 + 授权状态（读取无需认证，所以无 key 也能通）。"""
        server_id = self.get_server_id()
        info: dict[str, Any] = {
            "ok": True,
            "mode": "local",
            "base_url": self.base_url,
            "server_id": server_id,
            "library": f"local:users/{self.user_id}",
            "offline": True,
            "needs_zotero_org_account": False,
        }
        # 读一次 /api/ 拿到版本号，用来提示"能否写入"
        resp = self._http.get(f"{self.base_url}/api/",
                              headers={"Zotero-API-Version": API_VERSION})
        version = resp.headers.get("zotero-api-version") or ""
        info["api_version"] = version
        cached = self.keys.get(server_id)
        info["has_local_key"] = bool(cached)
        info["key_cached_for_server"] = bool(cached)
        if not cached:
            info["note"] = (
                "尚无本地 API Key：首次**写入**时 Zotero 会弹出确认框，"
                '请选择 "Always Allow"（选 "Allow" 会每次都弹）'
            )
        return info

    def list_collections(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start = 0
        while True:
            resp = self._request("GET", f"/users/{self.user_id}/collections",
                                 params={"limit": 100, "start": start})
            batch = resp.json() if resp.body else []
            if not isinstance(batch, list):
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            start += 100
        return out

    def create_collection(self, name: str, parent: str | None = None) -> str:
        item: dict[str, Any] = {
            "name": name,
            "parentCollection": parent if parent else False,
        }
        resp = self._request("POST", f"/users/{self.user_id}/collections",
                             json_body=[item], is_write=True)
        return self._extract_key(resp, name)

    def create_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for i in range(0, len(items), LOCAL_BATCH_LIMIT):
            chunk = items[i:i + LOCAL_BATCH_LIMIT]
            resp = self._request("POST", f"/users/{self.user_id}/items",
                                 json_body=chunk, is_write=True)
            payload = resp.json() if resp.body else {}
            successful = (payload or {}).get("successful") or {}
            failed = (payload or {}).get("failed") or {}
            for idx_s, obj in successful.items():
                li = i + int(idx_s)
                created.append({
                    "index": li,
                    "key": (obj or {}).get("key", ""),
                    "title": chunk[int(idx_s)].get("title", ""),
                })
            for idx_s, err in failed.items():
                li = i + int(idx_s)
                log.warning("本地写入失败 #%d %r：%s",
                            li, chunk[int(idx_s)].get("title", "")[:60], err)
        return created

    # ── Server-ID 与授权 ────────────────────────────────────────────
    def get_server_id(self, refresh: bool = False) -> str:
        """取 Zotero 实例 ID（写请求必须带）。

        来源：任意响应的 `Zotero-Server-ID` 头，或 GET /api/。
        缓存它；若 412/428 说明它过期了，需 `refresh=True` 重取。
        """
        if self._server_id and not refresh:
            return self._server_id
        resp = self._http.get(f"{self.base_url}/api/",
                              headers={"Zotero-API-Version": API_VERSION})
        sid = resp.headers.get("zotero-server-id")
        if not sid:
            raise UpstreamError(
                f"Zotero 在 {self.base_url} 有响应，但没有 Zotero-Server-ID 头",
                hint=(
                    "可能不是 Zotero、或版本过旧。请确认：\n"
                    "     · Zotero 桌面端**正在运行**\n"
                    "     · 设置 → 高级 → 勾选「Allow other applications on "
                    "this computer to communicate with Zotero」\n"
                    "     · 写入需要 **Zotero 10+**（7–9 的本地 API 只读）"
                ),
            )
        self._server_id = sid
        return sid

    def authorize(self, force: bool = False) -> str:
        """向 Zotero 申请本地 API Key —— **会弹出确认对话框**。

        ⚠️ 用户必须选 "Always Allow"，否则每次写入都会再次弹窗
           （选 "Allow" 签发的是一次性 key）。
        """
        sid = self.get_server_id()
        if not force:
            cached = self.keys.get(sid)
            if cached:
                self._key = cached
                return cached
        resp = self._http.post(
            f"{self.base_url}/api/local/authorize",
            json_body={"appName": self.app_name},
            headers={"Zotero-Server-ID": sid, "Zotero-API-Version": API_VERSION},
        )
        if resp.status == 403:
            raise AuthError(
                "Zotero 拒绝了写入授权",
                hint=(
                    "你在弹出的对话框里点了 Deny。\n"
                    "     重新运行时请点 **Always Allow**（不是 Allow）——\n"
                    "     选 Allow 只会签发一次性 key，下次写入还要再弹一次。"
                ),
                status=403,
            )
        if resp.status == 429:
            wait = resp.headers.get("retry-after", "约 60")
            raise AuthError(
                "授权弹窗过于频繁，Zotero 暂时拒绝",
                hint=f"等待约 {wait} 秒后重试（避免连续触发授权）",
                status=429,
            )
        if not resp.ok:
            snippet = resp.text[:200]
            raise AuthError(
                f"申请本地 API Key 失败（HTTP {resp.status}）",
                hint=(f"上游原话：{snippet}\n"
                      "     常见原因：Zotero 未开启本地 API（设置 → 高级）"
                      "或版本 < 10（写入需 10+）"),
                status=resp.status,
            )
        try:
            key = (resp.json() or {}).get("key")
        except Exception as exc:                       # noqa: BLE001
            raise UpstreamError(
                "授权响应无法解析", hint=f"{exc}；原文：{resp.text[:200]}") from exc
        if not key:
            raise UpstreamError(
                "授权成功但没有返回 key",
                hint=f"响应：{resp.text[:200]}",
            )
        self._key = key
        self.keys.set(sid, key)
        log.info("已获得本地 API Key（server=%s…），已按 0600 存入 %s",
                 sid[:8], self.keys.path)
        return key

    # ── 请求 ────────────────────────────────────────────────────────
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        is_write: bool = False,
        _retried_key: bool = False,
        _retried_sid: bool = False,
    ) -> HttpResponse:
        """发请求，并处理本地 API 特有的 401 / 412 / 428。

        ⚠️ 这两个自动处理是**必需的**（官方文档明确要求客户端这么做）：
          401 → 重新授权一次 + 重放（一次性 key 被消费属正常）
          412/428 → 刷新 Server-ID + 重试一次（Zotero 重启过）
        """
        url = f"{self.base_url}/api{path}"
        headers: dict[str, str] = {"Zotero-API-Version": API_VERSION}
        if is_write:
            headers["Zotero-Server-ID"] = self.get_server_id()
            key = self._key
            if not key:
                sid = self.get_server_id()
                key = self.keys.get(sid) or (
                    self.authorize() if self._auto_authorize else None)
            if key:
                headers["Zotero-API-Key"] = key

        resp = self._http.request(
            method, url, params=params, json_body=json_body, headers=headers,
        )

        # 把每次响应里的 Server-ID 缓存下来（便宜且能提前发现实例切换）
        adv = resp.headers.get("zotero-server-id")
        if adv:
            self._server_id = adv

        if resp.status == 401 and is_write and self._auto_authorize and not _retried_key:
            log.info("本地 key 失效或为一次性 key（401）→ 重新授权并重放")
            self._key = None
            self.authorize(force=True)
            return self._request(method, path, params=params, json_body=json_body,
                                 is_write=True, _retried_key=True,
                                 _retried_sid=_retried_sid)

        if resp.status in REFRESH_CODES and not _retried_sid:
            log.info("Server-ID 过期（HTTP %d）→ 刷新后重试一次", resp.status)
            self.get_server_id(refresh=True)
            return self._request(method, path, params=params, json_body=json_body,
                                 is_write=is_write, _retried_key=_retried_key,
                                 _retried_sid=True)

        if is_write and not resp.ok:
            self._raise_write_error(resp, method, path)
        return resp

    def _raise_write_error(self, resp: HttpResponse, method: str,
                           path: str) -> None:
        snippet = resp.text[:300]
        if resp.status == 403:
            raise AuthError(
                f"写入被拒绝（HTTP 403）：{method} {path}",
                hint=(
                    "本地 API 未启用，或你点了 Deny。请检查：\n"
                    "     · Zotero → 设置 → 高级 → 勾选「Allow other applications "
                    "on this computer to communicate with Zotero」\n"
                    "     · 或授权对话框里选了 Deny（需重新运行并选 Always Allow）\n"
                    f"   上游原话：{snippet}"
                ),
                status=403,
            )
        if resp.status in REFRESH_CODES:
            raise UpstreamError(
                f"Zotero 实例校验失败（HTTP {resp.status}）：{path}",
                hint=("Zotero 可能刚重启或切换了数据目录。通常会自动重试；"
                      "若持续失败请确认 Zotero 数据目录未被改动"),
                status=resp.status,
            )
        raise UpstreamError(
            f"本地写入失败（HTTP {resp.status}）：{method} {path}",
            hint=f"上游原话：{snippet}",
            status=resp.status,
        )

    @staticmethod
    def _extract_key(resp: HttpResponse, label: str) -> str:
        """从 Zotero 写入响应取 key（`successful` 的键是**字符串下标**）。"""
        payload = resp.json() if resp.body else {}
        ok = (payload or {}).get("successful") or {}
        if not ok:
            raise UpstreamError(
                f"创建失败（{label}）",
                hint=f"Zotero 返回：{json.dumps(payload, ensure_ascii=False)[:300]}",
                status=resp.status,
            )
        first = next(iter(ok.values())) or {}
        key = first.get("key")
        if not key:
            raise UpstreamError(f"创建成功但未返回 key（{label}）", hint=str(first)[:200])
        return str(key)


# ══════════════════════════════════════════════════════════════════════
#  可用性探测（供 backend="auto" 用）
# ══════════════════════════════════════════════════════════════════════
def probe_local(base_url: str = DEFAULT_LOCAL_BASE, timeout: float = 2.5) -> dict[str, Any]:
    """探测本地 Zotero 是否可用（**只读**，不触发授权弹窗）。

    Returns:
        {"available": bool, "server_id": str, "api_version": str, "error": str}
    """
    client = HttpClient(timeout=timeout, retries=0)
    try:
        resp = client.get(f"{base_url.rstrip('/')}/api/",
                          headers={"Zotero-API-Version": API_VERSION})
    except Exception as exc:                            # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    sid = resp.headers.get("zotero-server-id")
    if not sid:
        return {
            "available": False,
            "error": (f"HTTP {resp.status} 但无 Zotero-Server-ID 头"
                      "（可能不是 Zotero）"),
        }
    return {
        "available": True,
        "server_id": sid,
        "api_version": resp.headers.get("zotero-api-version", ""),
        "status": resp.status,
    }
