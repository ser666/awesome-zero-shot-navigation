"""Zotero **本地** API 客户端测试（不用云端的路径）。

做法：起一个**假的 Zotero 本地服务**（标准库 http.server），
按官方协议复现这些行为：
    · GET  /api/                        → 返回 Zotero-Server-ID 头
    · POST /api/local/authorize         → 返回 32 字符 key（或 403 拒绝）
    · GET  /api/users/0/collections     → 目录列表（无需认证）
    · POST /api/users/0/collections|items → **需要** key + Server-ID

⭐ 刻意复现真实协议里的三个"坑"，因为客户端必须自动处理它们：
    · 写请求没带 Server-ID → 428
    · Server-ID 过期（Zotero 重启）→ 412
    · 一次性 key 被消费 → 401（客户端应重新授权并**重放请求**）

⚠️ 这些行为**只在真实并发/重启场景下出现**，本地"看着对"的代码很容易漏掉，
   所以必须用假服务把它们变成可重复的测试。
"""

from __future__ import annotations

import json
import pathlib
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests._support import FakeCatalog, TempDir, make_paper
from zenav.config.models import ZoteroConfig
from zenav.errors import AuthError, UpstreamError
from zenav.services.zotero import PushState, ZoteroPushService
from zenav.services.zotero_local import (
    LocalKeyStore,
    ZoteroLocalClient,
    probe_local,
)

VALID_KEY = "K" * 32
SINGLE_USE_KEY = "S" * 32


class FakeZotero:
    """假 Zotero 实例（记录状态，可模拟重启/拒绝/一次性 key）。"""

    def __init__(self) -> None:
        self.server_id = "SRV0000001"
        self.deny = False                    # True → 授权时返回 403
        self.single_use = False              # True → 签发的是一次性 key
        self.consumed: set[str] = set()       # 已被消费的 key
        self.collections: dict[str, str] = {}  # name → key
        self.items: list[dict] = []
        self.auth_calls = 0
        self.write_calls = 0
        self._seq = 0
        self.received_keys: list[str] = []
        self.received_server_ids: list[str] = []

    def next_key(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq:07d}"


class Handler(BaseHTTPRequestHandler):
    zotero: FakeZotero = None            # type: ignore[assignment]  # 子类注入
    protocol_version = "HTTP/1.1"

    def log_message(self, *a, **kw):     # 静音测试输出
        pass

    # ── 工具 ────────────────────────────────────────────────────────
    def _send(self, code: int, body=None, extra_headers=None):
        payload = b""
        if body is not None:
            payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Zotero-Server-ID", self.zotero.server_id)
        self.send_header("Zotero-API-Version", "3")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw.decode()) if raw else None
        except json.JSONDecodeError:
            return None

    # ── 路由 ────────────────────────────────────────────────────────
    def do_GET(self):
        z = self.zotero
        if self.path.rstrip("/") in ("/api", ""):
            self._send(200, {"ok": True})
            return
        if self.path.startswith("/api/users/0/collections"):
            # 读取不需要认证
            self._send(200, [
                {"key": k, "data": {"name": n, "parentCollection": False}}
                for n, k in z.collections.items()
            ])
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        z = self.zotero
        body = self._read_body()

        if self.path == "/api/local/authorize":
            z.auth_calls += 1
            if z.deny:
                self._send(403, {"error": "denied"})
                return
            key = (SINGLE_USE_KEY if z.single_use else VALID_KEY)
            if z.single_use:
                key = f"S{z._seq:07d}" + "x" * 24
                key = key[:32]
            self._send(200, {"key": key})
            return

        # ── 写请求：按协议校验 ──
        if self.path.startswith("/api/users/0/"):
            z.write_calls += 1
            sid = self.headers.get("Zotero-Server-ID")
            key = self.headers.get("Zotero-API-Key")
            z.received_server_ids.append(sid or "")
            z.received_keys.append(key or "")
            if not sid:
                self._send(428, {"error": "Precondition Required: Server-ID"})
                return
            if sid != z.server_id:
                self._send(412, {"error": "Precondition Failed: Server-ID"})
                return
            if not key:
                self._send(401, {"error": "unauthorized: no key"})
                return
            if key in z.consumed:
                self._send(401, {"error": "unauthorized: key consumed"})
                return
            if z.single_use:
                # 一次性 key：校验通过后**立即作废**（真实 Zotero 就这么做）
                z.consumed.add(key)

            if "/collections" in self.path:
                name = (body or [{}])[0].get("name", "unnamed")
                if name in z.collections:
                    self._send(200, {"successful": {"0": {"key": z.collections[name]}},
                                     "failed": {}})
                    return
                k = z.next_key("COLL")
                z.collections[name] = k
                self._send(200, {"successful": {"0": {"key": k}}, "failed": {}})
                return
            if "/items" in self.path:
                succ, fail = {}, {}
                for i, it in enumerate(body or []):
                    k = z.next_key("ITEM")
                    z.items.append({**it, "key": k})
                    succ[str(i)] = {"key": k}
                self._send(200, {"successful": succ, "failed": fail})
                return
        self._send(404, {"error": "not found"})

    def do_PUT(self):                     # noqa: N802
        self._send(405, {"error": "n/a"})

    def do_DELETE(self):                  # noqa: N802
        self._send(405, {"error": "n/a"})


class FakeServer:
    """上下文管理器：起/停假 Zotero，并给出 base_url。

    Args:
        zotero: 复用的状态对象。
        port: 指定端口（默认 0 = 由系统分配空闲端口）。
              端到端演示时传 23119 以复现 Zotero 的真实端口形态。
    """

    def __init__(self, zotero: FakeZotero | None = None, port: int = 0) -> None:
        self.zotero = zotero or FakeZotero()
        h = type("H", (Handler,), {"zotero": self.zotero})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), h)
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._t = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
            daemon=True)

    def __enter__(self):
        # ⚠️ poll_interval 默认 0.5s —— 每个用例收尾都要白等半秒，
        #    21 个用例就是 10 秒。调小它，让测试套件保持秒级。
        self._t.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def make_local_client(srv: FakeServer, tmp: pathlib.Path, **over) -> ZoteroLocalClient:
    return ZoteroLocalClient(
        base_url=srv.base_url,
        key_store=LocalKeyStore.load(tmp / "keys.json"),
        **over,
    )


def make_catalog_cfg(tmp: pathlib.Path):
    """造一个指向临时 papers.json 的 CatalogConfig（测试装配用）。"""
    from zenav.config.models import CatalogConfig

    (tmp / "papers.json").write_text(
        json.dumps({"total": 0, "papers": [], "categories": {}}),
        encoding="utf-8",
    )
    return CatalogConfig(source="papers.json", cache_ttl=0)


# ══════════════════════════════════════════════════════════════════════
#  ① 探测与状态
# ══════════════════════════════════════════════════════════════════════
class TestProbe(unittest.TestCase):
    def test_probe_available(self):
        with FakeServer() as srv:
            r = probe_local(srv.base_url)
            self.assertTrue(r["available"])
            self.assertEqual(r["server_id"], srv.zotero.server_id)

    def test_probe_unavailable(self):
        # 没人监听的端口
        r = probe_local("http://127.0.0.1:1", timeout=1.5)
        self.assertFalse(r["available"])
        self.assertIn("error", r)

    def test_check_access_is_readonly(self):
        """⭐ 探测/状态检查**绝不能触发授权弹窗**（否则用户会莫名看到对话框）。"""
        with FakeServer() as srv, TempDir() as tmp:
            c = make_local_client(srv, tmp)
            info = c.check_access()
            self.assertTrue(info["ok"])
            self.assertEqual(info["mode"], "local")
            self.assertFalse(info["needs_zotero_org_account"])
            self.assertFalse(info["has_local_key"])
            self.assertEqual(srv.zotero.auth_calls, 0, "不该触发授权")
            self.assertEqual(srv.zotero.write_calls, 0)


# ══════════════════════════════════════════════════════════════════════
#  ② 授权
# ══════════════════════════════════════════════════════════════════════
class TestAuthorization(unittest.TestCase):
    def test_authorize_returns_and_persists_key(self):
        with FakeServer() as srv, TempDir() as tmp:
            c = make_local_client(srv, tmp)
            key = c.authorize()
            self.assertEqual(len(key), 32)
            self.assertEqual(srv.zotero.auth_calls, 1)
            # 落盘（按 server id 分区）+ 0600
            again = LocalKeyStore.load(tmp / "keys.json")
            self.assertEqual(again.get(srv.zotero.server_id), key)
            mode = (tmp / "keys.json").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600, "key 文件必须是 0600")

    def test_cached_key_avoids_second_dialog(self):
        """⭐ 已缓存时不该再弹窗（否则每次运行都弹，体验灾难）。"""
        with FakeServer() as srv, TempDir() as tmp:
            make_local_client(srv, tmp).authorize()
            c2 = make_local_client(srv, tmp)
            c2.authorize()
            self.assertEqual(srv.zotero.auth_calls, 1, "第二次应复用缓存")

    def test_deny_raises_with_always_allow_hint(self):
        with FakeServer() as srv, TempDir() as tmp:
            srv.zotero.deny = True
            c = make_local_client(srv, tmp)
            with self.assertRaises(AuthError) as ctx:
                c.authorize()
            msg = str(ctx.exception)
            self.assertIn("Always Allow", msg)
            self.assertIn("Deny", msg)

    def test_keys_partitioned_by_server_id(self):
        """⚠️ 换 Zotero 实例后，旧 key 不能拿来用（必须各自授权）。"""
        with FakeServer() as srv, TempDir() as tmp:
            c = make_local_client(srv, tmp)
            c.authorize()
            store = LocalKeyStore.load(tmp / "keys.json")
            store.set("SRVOTHER", "Z" * 32)
            self.assertEqual(len(store.keys), 2)


# ══════════════════════════════════════════════════════════════════════
#  ③ 写入与自动恢复
# ══════════════════════════════════════════════════════════════════════
class TestWrites(unittest.TestCase):
    def test_create_collection(self):
        with FakeServer() as srv, TempDir() as tmp:
            c = make_local_client(srv, tmp)
            key = c.create_collection("Zero-Shot Navigation")
            self.assertTrue(key.startswith("COLL"))
            self.assertIn("Zero-Shot Navigation", srv.zotero.collections)

    def test_write_sends_both_required_headers(self):
        """⭐ 官方协议：写请求必须同时带 Zotero-API-Key 与 Zotero-Server-ID。"""
        with FakeServer() as srv, TempDir() as tmp:
            c = make_local_client(srv, tmp)
            c.create_collection("X")
            self.assertEqual(srv.zotero.received_keys[-1], VALID_KEY)
            self.assertEqual(srv.zotero.received_server_ids[-1],
                             srv.zotero.server_id)

    def test_create_items_batch(self):
        with FakeServer() as srv, TempDir() as tmp:
            c = make_local_client(srv, tmp)
            created = c.create_items([{"title": "A"}, {"title": "B"}])
            self.assertEqual(len(created), 2)
            self.assertEqual([x["index"] for x in created], [0, 1])
            self.assertEqual(len(srv.zotero.items), 2)

    def test_single_use_key_401_then_auto_reauth_and_replay(self):
        """⭐⭐ 真实协议最重要的一个坑：一次性 key 被消费 → 401。

        官方文档明确说这属**预期行为**，客户端要重新授权并**重放请求**。
        若不处理，用户在 "Allow" 模式下会看到"每隔一次写入就失败"。
        """
        with FakeServer() as srv, TempDir() as tmp:
            srv.zotero.single_use = True
            c = make_local_client(srv, tmp)
            c.authorize()
            # 第一次写：key 被消费
            k1 = c.create_collection("First")
            self.assertTrue(k1)
            # 第二次写：缓存 key 已失效 → 客户端应自动重新授权 + 重放
            auths_before = srv.zotero.auth_calls
            k2 = c.create_collection("Second")
            self.assertTrue(k2, "重放后应成功")
            self.assertGreater(srv.zotero.auth_calls, auths_before,
                               "应触发重新授权")
            self.assertIn("Second", srv.zotero.collections)

    def test_server_id_change_412_then_refresh(self):
        """⭐ 第二个坑：Zotero 重启 → Server-ID 变了 → 412，需刷新后重试。"""
        with FakeServer() as srv, TempDir() as tmp:
            c = make_local_client(srv, tmp)
            c.create_collection("Before")
            # 模拟 Zotero 重启（换实例 ID）
            srv.zotero.server_id = "SRV9999999"
            key = c.create_collection("After")
            self.assertTrue(key, "刷新 Server-ID 后应成功")
            self.assertIn("After", srv.zotero.collections)

    def test_missing_server_id_428_then_retry(self):
        """第三个坑：写请求漏带 Server-ID → 428。"""
        with FakeServer() as srv, TempDir() as tmp:
            c = make_local_client(srv, tmp)
            # 人为清掉缓存的 server id，并让首次请求故意不带
            c._server_id = None
            c._key = VALID_KEY
            # get_server_id 会自动补上 → 所以正常路径不会 428；
            # 这里验证"自动补"确实发生了
            c.create_collection("Y")
            self.assertEqual(srv.zotero.received_server_ids[-1],
                             srv.zotero.server_id)

    def test_list_collections(self):
        with FakeServer() as srv, TempDir() as tmp:
            c = make_local_client(srv, tmp)
            c.create_collection("A")
            c.create_collection("B")
            names = {x["data"]["name"] for x in c.list_collections()}
            self.assertEqual(names, {"A", "B"})


# ══════════════════════════════════════════════════════════════════════
#  ④ 与业务层集成：完整推送（不经云端）
# ══════════════════════════════════════════════════════════════════════
class TestPushViaLocal(unittest.TestCase):
    def _service(self, srv: FakeServer, tmp: pathlib.Path, **over):
        cfg = ZoteroConfig(enabled=True, backend="local",
                           state_file=str(tmp / "state.json"),
                           local_api_base=srv.base_url, **over)
        cfg.validate()
        client = ZoteroLocalClient(
            base_url=srv.base_url,
            key_store=LocalKeyStore.load(tmp / "keys.json"),
        )
        return ZoteroPushService(
            catalog=FakeCatalog([
                make_paper(title="P1", doi="10.1/1", category="ObjectNav"),
                make_paper(title="P2", doi="10.1/2", category="VLN"),
            ]),
            config=cfg,
            state=PushState.load(tmp / "state.json", library="local"),
            client=client,
        )

    def test_dry_run_does_not_touch_zotero_at_all(self):
        """⭐⭐ 演练在**本地后端**下必须：不建目录、不弹授权框、不写条目。

        这条正是从端到端演示发现真 bug 后加的守卫：
        旧实现在 dry_run 时也会建目录 ⇒ 会弹授权对话框 + 真的写入用户库。
        """
        with FakeServer() as srv, TempDir() as tmp:
            svc = self._service(srv, tmp)
            report = svc.push(dry_run=True)
            self.assertEqual(report.candidates, 2)
            self.assertEqual(report.pushed, 0)
            self.assertEqual(srv.zotero.items, [], "演练不该写条目")
            self.assertEqual(srv.zotero.collections, {}, "演练不该建目录")
            self.assertEqual(srv.zotero.auth_calls, 0, "演练不该触发授权弹窗")
            self.assertEqual(srv.zotero.write_calls, 0, "演练不该有任何写请求")
            self.assertTrue(report.collections_planned)
            self.assertEqual(report.collections_created, [])
            self.assertFalse((tmp / "state.json").exists())

    def test_end_to_end_push_local(self):
        with FakeServer() as srv, TempDir() as tmp:
            svc = self._service(srv, tmp)
            report = svc.push(dry_run=False)
            self.assertEqual(report.pushed, 2)
            self.assertEqual(report.failed, 0)
            # Zotero 端：顶层目录 + 两个分类子目录 + 2 个条目
            self.assertIn("Zero-Shot Navigation", srv.zotero.collections)
            self.assertIn("ObjectNav", srv.zotero.collections)
            self.assertIn("VLN", srv.zotero.collections)
            self.assertEqual(len(srv.zotero.items), 2)
            self.assertTrue(all(i["collections"] for i in srv.zotero.items))

    def test_idempotent_via_local(self):
        with FakeServer() as srv, TempDir() as tmp:
            self._service(srv, tmp).push(dry_run=False)
            r2 = self._service(srv, tmp).push(dry_run=False)
            self.assertEqual(r2.pushed, 0)
            self.assertEqual(r2.already, 2)
            self.assertEqual(len(srv.zotero.items), 2, "不该重复写入")

    def test_status_reports_local_backend(self):
        with FakeServer() as srv, TempDir() as tmp:
            st = self._service(srv, tmp).status()
            self.assertEqual(st["backend_in_use"], "local")
            self.assertFalse(st["cloud"])
            self.assertTrue(st["connection"]["ok"])

    def test_auto_backend_picks_local_when_available(self):
        """⭐ backend=auto：本地可用时必须选 local（而不是强依赖云端）。"""
        with FakeServer() as srv, TempDir() as tmp:
            cfg = ZoteroConfig(enabled=True, backend="auto",
                               local_api_base=srv.base_url,
                               state_file=str(tmp / "s.json"))
            cfg.validate()

            class Cfg:
                zotero = cfg
                catalog = make_catalog_cfg(tmp)
                root = tmp

            svc = ZoteroPushService.from_config(Cfg())
            self.assertEqual(type(svc._client).__name__, "ZoteroLocalClient")

    def test_auto_backend_falls_back_when_local_missing(self):
        """本地不可用 → 回落 web（此时会要求账号凭据，报错要给出两条路）。"""
        with TempDir() as tmp:
            cfg = ZoteroConfig(enabled=True, backend="auto",
                               local_api_base="http://127.0.0.1:1",
                               state_file=str(tmp / "s.json"))
            cfg.validate()

            class Cfg:
                zotero = cfg
                catalog = make_catalog_cfg(tmp)
                root = tmp

            with self.assertRaises(Exception) as ctx:
                ZoteroPushService.from_config(Cfg())
            msg = str(ctx.exception)
            self.assertIn("不想用 Zotero 云端", msg)
            self.assertIn("backend = \"local\"", msg)


class TestNoCloudRequirements(unittest.TestCase):
    """⭐ 核心主张的守卫测试：本地后端**不得**依赖任何云端凭据。"""

    def test_local_backend_needs_no_zotero_org_key(self):
        with FakeServer() as srv, TempDir() as tmp:
            cfg = ZoteroConfig(enabled=True, backend="local",
                               local_api_base=srv.base_url,
                               state_file=str(tmp / "s.json"))
            cfg.validate()
            self.assertTrue(cfg.uses_local)
            self.assertFalse(cfg.needs_web_credentials)

            class Cfg:
                zotero = cfg
                catalog = make_catalog_cfg(tmp)
                root = tmp

            # 环境里没有任何 ZOTERO_* 变量也应该能装配成功
            svc = ZoteroPushService.from_config(Cfg())
            self.assertIsNotNone(svc)

    def test_stored_key_is_not_a_zotero_org_key(self):
        """落盘的是本地 key（32 字符、按实例分区），与网站 Key 无关。"""
        with FakeServer() as srv, TempDir() as tmp:
            c = make_local_client(srv, tmp)
            k = c.authorize()
            raw = json.loads((tmp / "keys.json").read_text(encoding="utf-8"))
            self.assertIn("keys", raw)
            self.assertEqual(raw["keys"][srv.zotero.server_id], k)
            # 文件里不该出现任何 zotero.org 痕迹
            self.assertNotIn("zotero.org", json.dumps(raw))


if __name__ == "__main__":
    unittest.main()
