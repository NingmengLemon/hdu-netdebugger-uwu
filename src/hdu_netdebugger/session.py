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
from hdu_netdebugger.net.interfaces import list_interfaces
from hdu_netdebugger.net.ping import Backend, PingOptions, ping_multiple
from hdu_netdebugger.net.routes import Egress, RouteInfo, route_for

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from hdu_netdebugger.models.interface import InterfaceInfo
    from hdu_netdebugger.models.ping import MultiPingResult

    #: Ping 实现的签名：``(目标, 参数, *, backend) -> 汇总结果``。
    #:
    #: 用 ``Callable`` 而不是 ``Protocol``：这里既要接受 ``ping_multiple`` 这样的函数，
    #: 也要接受 tests.fakes.FakePinger 这类可调用对象。
    Pinger = Callable[..., Awaitable[MultiPingResult]]
    #: 路由查询的签名：``(目标) -> RouteInfo | None``
    Router = Callable[..., "RouteInfo | None"]

__all__ = ["RunSession"]


class RunSession:
    """检查项运行上下文。"""

    __slots__ = (
        "_http",
        "_http_owned",
        "_interfaces",
        "_pinger",
        "_router",
        "config",
        "context",
        "ping_backend",
        "verbose",
    )

    def __init__(
        self,
        *,
        config: RunConfig | None = None,
        context: CheckContext | None = None,
        verbose: bool = False,
        ping_backend: Backend = Backend.AUTO,
        http: HttpClient | None = None,
        pinger: Pinger | None = None,
        router: Router | None = None,
        interfaces: Sequence[InterfaceInfo] | None = None,
    ) -> None:
        self.config = config or RunConfig()
        self.context = context or CheckContext()
        self.verbose = verbose
        self.ping_backend = ping_backend
        self._http = http
        #: 外部传入的客户端由调用方负责关闭，我们只关自己建的
        self._http_owned = http is None
        self._pinger = pinger or ping_multiple
        self._router = router or route_for
        #: 接口快照：``None`` 表示"还没枚举过"；测试可以直接注入样本
        self._interfaces: list[InterfaceInfo] | None = (
            None if interfaces is None else list(interfaces)
        )

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

    def egress(self, target: object) -> Egress:
        """查这次探测从哪个接口出去（校园网里这条证据经常比"通不通"更关键）。

        本机（以及很多同学）装着代理/VPN 的 TUN，默认路由被它以 metric 0 抢走，
        于是所有探测都从隧道出去：结论写"BAS 可达"并不代表校园物理链路可达。

        路由查询失败只是"这项证据拿不到"，不该让检查项崩掉 —— 返回的
        :class:`Egress` 会渲染成"出站: (未知)"，降级是**可见**的。
        """
        text = str(target)
        try:
            route: RouteInfo | None = self._router(text)
        except Exception:  # 归属信息是附属证据，不能因此中断检查
            route = None
        if route is None:
            return Egress(target=text)
        return Egress(
            target=text, route=route, interface=self._interface_for(route.interface_index)
        )

    def interfaces(self) -> list[InterfaceInfo]:
        """本机接口快照（惰性枚举一次并缓存）。

        检查 1 会把结果写进 ``context.interfaces``；但 ``--only 检查DNS可用性``
        这种单跑场景下 context 是空的，所以这里自己也能拿到。
        """
        if self._interfaces is None:
            self._interfaces = list_interfaces(include_loopback=True)
        return list(self._interfaces)

    def _interface_for(self, index: int | None) -> InterfaceInfo | None:
        """按系统接口索引反查接口。"""
        if index is None:
            return None
        for item in self.interfaces():
            if item.index == index:
                return item
        return None

    async def aclose(self) -> None:
        """释放自建的连接池；外部注入的客户端不动。"""
        if self._http is not None and self._http_owned:
            await self._http.aclose()
            self._http = None
