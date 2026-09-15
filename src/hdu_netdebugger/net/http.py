"""HTTP 客户端封装.

对应 C# 原版的 ``Utils/HttpUtils.cs``：共享一个 ``HttpClient``，**不跟随重定向、
不保留 Cookie**。``httpx`` 的默认行为与之不同（默认跟随重定向），因此显式关掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["HttpClient", "HttpResponse"]


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """一次 HTTP 请求的结果快照。"""

    url: str
    status_code: int
    headers: Mapping[str, str]
    text: str
    elapsed_s: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        """请求是否成功完成（无论状态码）。"""
        return self.error is None

    @property
    def is_no_content(self) -> bool:
        """是否为 ``204 No Content`` —— generate_204 探针的成功标志。"""
        return self.status_code == 204

    @property
    def is_redirect(self) -> bool:
        """是否被重定向 —— 校园网未认证时门户劫持的典型表现。"""
        return self.status_code in {301, 302, 303, 307, 308}

    def describe(self) -> str:
        """渲染成简短的一行描述。"""
        if self.error is not None:
            return f"请求 {self.url} 失败: {self.error}"
        return f"请求 {self.url} 返回 HTTP {self.status_code}"


class HttpClient:
    """共享的异步 HTTP 客户端。

    作为异步上下文管理器使用，或在运行结束时显式 :meth:`aclose`::

        async with HttpClient() as client:
            response = await client.get("http://example.com")
    """

    __slots__ = ("_client",)

    def __init__(
        self,
        *,
        timeout_s: float = 5.0,
        verify_tls: bool = True,
        trust_env_proxy: bool = True,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            verify=verify_tls,
            trust_env=trust_env_proxy,
            follow_redirects=False,
            headers={"User-Agent": "hdu-netdebugger-uwu"},
        )

    async def get(self, url: str, *, timeout_s: float | None = None) -> HttpResponse:
        """发起 GET 请求。

        任何网络层异常都会被收敛成 :attr:`HttpResponse.error`，不向调用方抛出 ——
        检查项需要的是"能不能通"，而不是堆栈。
        """
        import time  # noqa: PLC0415 - 仅在计时分支需要

        started = time.monotonic()
        try:
            response = await self._client.get(
                url,
                timeout=httpx.Timeout(timeout_s)
                if timeout_s is not None
                else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            return HttpResponse(
                url=url,
                status_code=0,
                headers={},
                text="",
                elapsed_s=time.monotonic() - started,
                error=f"{type(exc).__name__}: {exc}",
            )
        return HttpResponse(
            url=url,
            status_code=response.status_code,
            headers=dict(response.headers),
            text=response.text,
            elapsed_s=time.monotonic() - started,
        )

    async def aclose(self) -> None:
        """关闭底层连接池。"""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
