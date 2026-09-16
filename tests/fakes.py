"""测试替身与样本数据.

检查项只通过 :class:`~hdu_netdebugger.session.RunSession` 访问外部世界，
因此注入 :class:`FakePinger` / :class:`FakeHttp` / :class:`FakeRouter`
就能在毫秒内跑完全部检查项，不产生任何真实网络流量。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.context import CheckContext
from hdu_netdebugger.models.enums import PingStatus
from hdu_netdebugger.models.ping import MultiPingResult, PingReply
from hdu_netdebugger.net.http import HttpResponse
from hdu_netdebugger.session import RunSession

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from hdu_netdebugger.models.interface import InterfaceInfo
    from hdu_netdebugger.net.ping import Backend, PingOptions
    from hdu_netdebugger.net.routes import DefaultRoute, RouteInfo

__all__ = [
    "SRUN_DEVICE_DETAIL",
    "SRUN_OK_PAYLOAD",
    "FakeHttp",
    "FakePinger",
    "FakeRouter",
    "make_session",
    "srun_jsonp",
]


class FakePinger:
    """按目标地址给出预置结果的可编程 Pinger。

    默认按 ``reachable`` 集合决定"全成功 / 全超时"；需要**部分丢包**时用
    :meth:`set_pattern` 直接编排逐包结果 —— 少了它，"0 < 成功数 < 总数"
    那条分支（四个检查项里都有）在测试里根本不可达。

    ``ttl`` / ``source`` 用来构造"回复里带了路径证据"的场景（正常应当是
    ``source == target``；给个别的地址就能测出"回复来源异常"的警告）。
    """

    def __init__(
        self,
        *,
        reachable: Iterable[str] = (),
        count: int = 4,
        rtt_ms: float = 2.0,
        ttl: int | None = None,
        source: str | None = None,
    ) -> None:
        self.reachable = {str(item) for item in reachable}
        self.count = count
        self.rtt_ms = rtt_ms
        self.ttl = ttl
        self.source = source
        self.calls: list[tuple[str, PingOptions, Backend]] = []
        self.patterns: dict[str, tuple[bool, ...]] = {}

    def set_reachable(self, *targets: str, reachable: bool = True) -> None:
        """运行期调整一个或多个目标的可达性。"""
        for target in targets:
            if reachable:
                self.reachable.add(str(target))
            else:
                self.reachable.discard(str(target))

    def set_pattern(self, target: str, pattern: Sequence[bool]) -> None:
        """按逐包结果编排某个目标（``True`` 表示该包收到了回复）。"""
        self.patterns[str(target)] = tuple(pattern)

    async def __call__(
        self,
        target: str,
        options: PingOptions,
        backend: Backend,
    ) -> MultiPingResult:
        self.calls.append((target, options, backend))
        pattern = self.patterns.get(target)
        if pattern is None:
            pattern = (target in self.reachable,) * self.count
        replies = tuple(
            PingReply(
                sequence=index + 1,
                status=PingStatus.SUCCESS if alive else PingStatus.TIMEOUT,
                rtt_ms=self.rtt_ms if alive else None,
                ttl=self.ttl if alive else None,
                source=self.source if alive else None,
            )
            for index, alive in enumerate(pattern)
        )
        return MultiPingResult(target=target, replies=replies)


class FakeRouter:
    """按目标地址给出预置出站路由的替身。

    默认什么也不返回（等价于"查不到出站归属"）；需要构造"流量被隧道接管"的场景时，
    用 :meth:`set_route` 给某个目标指定 ``interface_index``，再把 session 的接口
    列表换成带该索引的样本即可。
    """

    def __init__(self, routes: Mapping[str, RouteInfo] | None = None) -> None:
        self.routes: dict[str, RouteInfo] = {
            str(target): route for target, route in (routes or {}).items()
        }
        self.calls: list[str] = []

    def set_route(self, target: str, route: RouteInfo) -> None:
        """给某个目标指定出站路由。"""
        self.routes[str(target)] = route

    def __call__(self, target: str) -> RouteInfo | None:
        self.calls.append(target)
        return self.routes.get(target)


class FakeHttp:
    """按 URL 精确匹配返回预置响应的 HTTP 替身。"""

    def __init__(self) -> None:
        self.routes: dict[str, HttpResponse] = {}
        self.calls: list[str] = []
        self.closed = False

    def add(
        self,
        url: str,
        *,
        status_code: int = 200,
        text: str = "",
        headers: Mapping[str, str] | None = None,
        error: str | None = None,
    ) -> None:
        """注册一条路由。"""
        self.routes[url] = HttpResponse(
            url=url,
            status_code=status_code,
            headers=dict(headers or {}),
            text=text,
            elapsed_s=0.01,
            error=error,
        )

    def add_no_content(self, url: str) -> None:
        """注册 ``204 No Content``（generate_204 探针的成功响应）。"""
        self.add(url, status_code=204)

    async def get(self, url: str, **_kwargs: Any) -> HttpResponse:
        """返回匹配的响应；未注册时返回连接错误。"""
        self.calls.append(url)
        if url in self.routes:
            return self.routes[url]
        return HttpResponse(
            url=url,
            status_code=0,
            headers={},
            text="",
            elapsed_s=0.0,
            error="ConnectError: 未注册的测试路由",
        )

    async def aclose(self) -> None:
        """标记已关闭（本替身不需要真正清理）。"""
        self.closed = True


def make_session(
    *,
    pinger: FakePinger | None = None,
    http: FakeHttp | None = None,
    router: FakeRouter | None = None,
    interfaces: Sequence[InterfaceInfo] = (),
    routes: Sequence[DefaultRoute] = (),
    **config_overrides: Any,
) -> RunSession:
    """构造注入了假网络的 session。

    默认把路由查询、默认路由表与接口列表都换成替身：否则每个检查项测试都会去调真实的
    ``GetIpForwardTable2`` / ``GetBestRoute`` / 枚举真实网卡，既慢又让测试依赖跑测机器的
    网络配置（在 Linux 上默认路由表本来就是空的，断言会平台相关）。
    """
    return RunSession(
        config=RunConfig(**config_overrides),
        context=CheckContext(),
        pinger=pinger or FakePinger(),
        http=http or FakeHttp(),  # ty: ignore[invalid-argument-type]
        router=router or FakeRouter(),
        interfaces=list(interfaces),
        route_lister=lambda: tuple(routes),
    )


def srun_jsonp(payload: Mapping[str, Any], *, wrapper: str = "jQuery") -> str:
    """把字典包成深澜的 JSONP 响应。"""
    return f"{wrapper}({json.dumps(payload, ensure_ascii=False)})"


#: 一份「认证正常」的响应体
#:
#: 注意：这里所有取值都是**编出来的**。样本最初来自一次真实抓包，但仓库文件里
#: 不该留真实账号/内网地址/设备 ID 这类环境指纹，所以账号、姓名、MAC、
#: rad_online_id 与 IP 全部换成了明显合成或保留值（``10.150.0.100`` 在生活区
#: 网段内，形状和真实响应一致；MAC 用本地管理地址前缀 ``02:``）。
SRUN_OK_PAYLOAD: dict[str, Any] = {
    "ServerFlag": 0,
    "add_time": 1700000000,
    "all_bytes": 100,
    "billing_name": "电信",
    "bytes_in": 10,
    "bytes_out": 20,
    "checkout_date": 0,
    "domain": "mac-auth",
    "error": "ok",
    "group_id": "1",
    "keepalive_time": 0,
    "online_device_total": "1",
    "online_ip": "10.150.0.100",
    "online_ip6": "::",
    "products_id": "4",
    "products_name": "中国电信",
    "real_name": "测试用户",
    "remain_bytes": 0,
    "remain_seconds": 0,
    "sum_bytes": 0,
    "sum_seconds": 0,
    "sysver": "1.0",
    "user_balance": 0,
    "user_charge": 0,
    "user_mac": "020000000001",
    "user_name": "200000000",
    "wallet_balance": 0,
}

#: 在线设备明细（含一台空 IP 的占位设备）
SRUN_DEVICE_DETAIL: dict[str, Any] = {
    "1001": {
        "class_name": "1",
        "ip": "10.150.0.100",
        "ip6": "::",
        "os_name": "MAC auth",
        "rad_online_id": "1001",
    },
    "0": {"class_name": "", "ip": "", "ip6": "", "os_name": "", "rad_online_id": "0"},
}
