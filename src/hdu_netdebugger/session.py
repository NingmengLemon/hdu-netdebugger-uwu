"""一次检查运行所共享的运行时资源.

把「配置」「跨检查项状态」「HTTP 连接池」「Ping 实现」收在一个对象里交给检查项，
避免每个检查项各建一个连接池，也避免用全局变量传资源。

``http`` / ``pinger`` 两个可注入的接缝让测试无需真发网络请求，
也让本工具能被别的程序当作库嵌进去用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.context import CheckContext
from hdu_netdebugger.net.http import HttpClient
from hdu_netdebugger.net.ping import Backend, PingOptions, ping_multiple

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hdu_netdebugger.models.ping import MultiPingResult

    #: Ping 实现的签名：``(目标, 参数, *, backend) -> 汇总结果``。
    #:
    #: 用 ``Callable`` 而不是 ``Protocol``：这里既要接受 ``ping_multiple`` 这样的函数，
    #: 也要接受 tests.fakes.FakePinger 这类可调用对象。
    Pinger = Callable[..., Awaitable[MultiPingResult]]

__all__ = ["RunSession"]


class RunSession:
    """检查项运行上下文。"""

    __slots__ = ("_http", "_http_owned", "_pinger", "config", "context", "ping_backend", "verbose")

    def __init__(
        self,
        *,
        config: RunConfig | None = None,
        context: CheckContext | None = None,
        verbose: bool = False,
        ping_backend: Backend = Backend.AUTO,
        http: HttpClient | None = None,
        pinger: Pinger | None = None,
    ) -> None:
        self.config = config or RunConfig()
        self.context = context or CheckContext()
        self.verbose = verbose
        self.ping_backend = ping_backend
        self._http = http
        #: 外部传入的客户端由调用方负责关闭，我们只关自己建的
        self._http_owned = http is None
        self._pinger = pinger or ping_multiple

    @property
    def http(self) -> HttpClient:
        """共享的 HTTP 客户端（惰性创建）。"""
        if self._http is None:
            self._http = HttpClient(
                timeout_s=self.config.http_timeout_s,
                verify_tls=self.config.verify_tls,
                trust_env_proxy=self.config.trust_env_proxy,
            )
            self._http_owned = True
        return self._http

    @property
    def ping_options(self) -> PingOptions:
        """由配置派生的 Ping 参数。"""
        return PingOptions(
            count=self.config.ping_count,
            timeout_s=self.config.ping_timeout_s,
            payload_size=self.config.ping_payload_size,
            ttl=self.config.ping_ttl,
            interval_s=self.config.ping_interval_s,
        )

    async def ping(self, target: object) -> MultiPingResult:
        """按当前配置 Ping 一个目标。"""
        return await self._pinger(str(target), self.ping_options, backend=self.ping_backend)

    async def aclose(self) -> None:
        """释放自建的连接池；外部注入的客户端不动。"""
        if self._http is not None and self._http_owned:
            await self._http.aclose()
            self._http = None
