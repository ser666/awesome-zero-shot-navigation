"""HTTP 客户端 —— 只用标准库 `urllib`。

刻意不用 requests / httpx 的理由：

    1. 与本项目"零依赖"的基调一致 —— 服务层虽然允许依赖，
       但能用标准库解决的网络请求没必要引入第三方（少一个腐坏面）。
    2. 需要的能力很有限：JSON 收发 + 重试 + 限流尊重。
    3. 换实现成本低 —— 上层只见 `HttpClient` 接口，将来换 httpx 不动调用方。

**它解决的实际痛点**（都是踩过的）：
    · Zotero 有速率限制 → 必须处理 429，且要尊重 `Retry-After`
    · 上游偶发 5xx → 立刻失败会让一次推送任务白跑，需要退避重试
    · **密钥不能进日志** → 打印请求时必须脱敏（否则密钥顺着 CI 日志泄露）
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from zenav.errors import AuthError, RateLimitError, UpstreamError
from zenav.log import get_logger

log = get_logger(__name__)

DEFAULT_UA = "awesome-zero-shot-navigation/1.0"
RETRY_STATUS = (408, 429, 500, 502, 503, 504)

# 打日志时这些 header 的值要打码（大小写不敏感）
_SENSITIVE_HEADERS = frozenset({
    "authorization", "zotero-api-key", "x-api-key", "api-key",
    "cookie", "proxy-authorization", "zenav-mcp-token",
})


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """把敏感 header 的值替换成掩码（日志/报错用）。"""
    if not headers:
        return {}
    out = {}
    for k, v in headers.items():
        if k.lower() in _SENSITIVE_HEADERS:
            out[k] = f"***(len={len(v)})"
        else:
            out[k] = v
    return out


@dataclass
class HttpResponse:
    """HTTP 响应（含便捷解码）。"""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    url: str = ""

    @property
    def text(self) -> str:
        # 容错：部分上游不声明 charset 却返回 UTF-8
        charset = "utf-8"
        ctype = self.headers.get("content-type", "").lower()
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        return self.body.decode(charset, errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise UpstreamError(
                f"响应不是合法 JSON（HTTP {self.status}）：{self.url}",
                hint=f"前 200 字符：{self.text[:200]!r}",
            ) from exc

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class HttpClient:
    """最小可用的 HTTP 客户端：JSON 收发 + 退避重试 + 限流尊重。

    Args:
        timeout: 单次请求超时（秒）。
        retries: 失败重试次数（不含首次）。
        backoff: 指数退避基数（秒）。第 n 次重试等待 backoff * 2**n。
        max_wait: 单次等待上限（秒），避免被 Retry-After 拖死。
    """

    def __init__(
        self,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 1.5,
        max_wait: float = 30.0,
        user_agent: str = DEFAULT_UA,
    ) -> None:
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self.max_wait = max_wait
        self.user_agent = user_agent

    # ── 主入口 ──────────────────────────────────────────────────────
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: bytes | None = None,
        raise_for_status: bool = False,
        accept: str = "application/json",
    ) -> HttpResponse:
        """发请求。默认**不**因 4xx/5xx 抛异常（除非 raise_for_status=True）。

        为什么默认不抛：Zotero 的"条目已存在"是 412，属于需要处理而非崩溃的情况，
        让调用方自己看 status 更灵活。
        """
        if params:
            # doseq=True 支持列表参数（如 Zotero 的 itemKey=a&itemKey=b）
            query = urllib.parse.urlencode(params, doseq=True)
            url = f"{url}{'&' if '?' in url else '?'}{query}"

        body = data
        hdrs = {"User-Agent": self.user_agent, "Accept": accept}
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        elif body is not None:
            hdrs.setdefault("Content-Type", "application/octet-stream")
        hdrs.update(headers or {})

        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                log.debug("%s %s (尝试 %d/%d) headers=%s",
                          method, url, attempt + 1, self.retries + 1,
                          redact_headers(hdrs))
                req = urllib.request.Request(url, data=body, headers=hdrs,
                                             method=method.upper())
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    out = HttpResponse(
                        status=resp.status,
                        headers={k.lower(): v for k, v in resp.headers.items()},
                        body=resp.read(),
                        url=url,
                    )
                if out.status in RETRY_STATUS and attempt < self.retries:
                    wait = self._wait_for(attempt, out.headers.get("retry-after"))
                    log.warning("HTTP %d，%.1fs 后重试（%s）",
                                out.status, wait, url)
                    time.sleep(wait)
                    continue
                if raise_for_status and not out.ok:
                    self._raise_for(out, method, url)
                return out

            except urllib.error.HTTPError as exc:
                # urllib 把 4xx/5xx 抛成异常，这里统一转成响应，逻辑只有一处
                hdrs_l = {k.lower(): v for k, v in (exc.headers or {}).items()}
                body_bytes = exc.read() if hasattr(exc, "read") else b""
                out = HttpResponse(status=exc.code, headers=hdrs_l,
                                   body=body_bytes, url=url)
                if exc.code in RETRY_STATUS and attempt < self.retries:
                    wait = self._wait_for(attempt, hdrs_l.get("retry-after"))
                    log.warning("HTTP %d，%.1fs 后重试（%s）", exc.code, wait, url)
                    time.sleep(wait)
                    continue
                if raise_for_status and not out.ok:
                    self._raise_for(out, method, url)
                return out

            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    wait = self._wait_for(attempt, None)
                    log.warning("网络错误（%s），%.1fs 后重试（%s）",
                                type(exc).__name__, wait, url)
                    time.sleep(wait)
                    continue
                raise UpstreamError(
                    f"请求失败（重试 {self.retries} 次后仍失败）：{url}",
                    hint=f"{type(exc).__name__}: {exc}",
                ) from exc

        raise UpstreamError(f"请求失败：{url}",
                            hint=f"最后一次异常：{last_exc}")

    # ── 便捷方法 ────────────────────────────────────────────────────
    def get(self, url: str, **kw: Any) -> HttpResponse:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> HttpResponse:
        return self.request("POST", url, **kw)

    def put(self, url: str, **kw: Any) -> HttpResponse:
        return self.request("PUT", url, **kw)

    def delete(self, url: str, **kw: Any) -> HttpResponse:
        return self.request("DELETE", url, **kw)

    # ── 内部 ────────────────────────────────────────────────────────
    def _wait_for(self, attempt: int, retry_after: str | None) -> float:
        """决定等待时长：优先听 Retry-After，否则指数退避，均封顶 max_wait。"""
        if retry_after:
            try:
                return min(float(retry_after), self.max_wait)
            except ValueError:
                pass
        return min(self.backoff * (2 ** attempt), self.max_wait)

    def _raise_for(self, resp: HttpResponse, method: str, url: str) -> None:
        """把非 2xx 响应翻译成有语义的异常（便于上层分别处理）。"""
        snippet = resp.text[:300]
        if resp.status in (401, 403):
            raise AuthError(
                f"认证失败（HTTP {resp.status}）：{method} {url}",
                hint=(
                    "常见原因：\n"
                    "     · API Key 无效 / 已撤销 → 重新生成并更新环境变量\n"
                    "     · Key 没有 **write access** → 在 Zotero 设置页勾选\n"
                    "     · library_id 与 Key 不属于同一账号\n"
                    f"   上游原话：{snippet}"
                ),
                status=resp.status,
            )
        if resp.status == 429:
            raise RateLimitError(
                f"被限流（HTTP 429）：{url}",
                hint="增大 push_interval / interval 配置，或稍后重试",
                status=resp.status,
            )
        raise UpstreamError(
            f"上游返回错误（HTTP {resp.status}）：{method} {url}",
            hint=f"上游原话：{snippet}",
            status=resp.status,
        )
