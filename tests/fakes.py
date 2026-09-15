"""测试替身与样本数据.

检查项只通过 :class:`~hdu_netdebugger.session.RunSession` 访问外部世界，
因此注入 :class:`FakePinger` 与 :class:`FakeHttp` 就能在毫秒内跑完全部检查项，
不产生任何真实网络流量。
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
    from collections.abc import Iterable, Mapping

    from hdu_netdebugger.net.ping import Backend, PingOptions

__all__ = [
    "SRUN_DEVICE_DETAIL",
    "SRUN_OK_PAYLOAD",
    "FakeHttp",
    "FakePinger",
    "make_session",
    "srun_jsonp",
]


class FakePinger:
    """按目标地址给出预置结果的可编程 Pinger。"""

    def __init__(
        self,
        *,
        reachable: Iterable[str] = (),
        count: int = 4,
        rtt_ms: float = 2.0,
    ) -> None:
        self.reachable = {str(item) for item in reachable}
        self.count = count
        self.rtt_ms = rtt_ms
        self.calls: list[tuple[str, PingOptions, Backend]] = []

    def set_reachable(self, target: str, *, reachable: bool = True) -> None:
        """运行期调整某个目标的可达性。"""
        if reachable:
            self.reachable.add(target)
        else:
            self.reachable.discard(target)

    async def __call__(
        self,
        target: str,
        options: PingOptions,
        backend: Backend,
    ) -> MultiPingResult:
        self.calls.append((target, options, backend))
        alive = target in self.reachable
        replies = tuple(
            PingReply(
                sequence=index + 1,
                status=PingStatus.SUCCESS if alive else PingStatus.TIMEOUT,
                rtt_ms=self.rtt_ms if alive else None,
            )
            for index in range(self.count)
        )
        return MultiPingResult(target=target, replies=replies)


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
    **config_overrides: Any,
) -> RunSession:
    """构造注入了假网络的 session。"""
    return RunSession(
        config=RunConfig(**config_overrides),
        context=CheckContext(),
        pinger=pinger or FakePinger(),
        http=http or FakeHttp(),  # ty: ignore[invalid-argument-type]
    )


def srun_jsonp(payload: Mapping[str, Any], *, wrapper: str = "jQuery") -> str:
    """把字典包成深澜的 JSONP 响应。"""
    return f"{wrapper}({json.dumps(payload, ensure_ascii=False)})"


#: 一份「认证正常」的响应体
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
    "online_ip": "10.150.184.118",
    "online_ip6": "::",
    "products_id": "4",
    "products_name": "中国电信",
    "real_name": "张三",
    "remain_bytes": 0,
    "remain_seconds": 0,
    "sum_bytes": 0,
    "sum_seconds": 0,
    "sysver": "1.0",
    "user_balance": 0,
    "user_charge": 0,
    "user_mac": "aabbccddeeff",
    "user_name": "2025001",
    "wallet_balance": 0,
}

#: 在线设备明细（含一台空 IP 的占位设备）
SRUN_DEVICE_DETAIL: dict[str, Any] = {
    "63568314": {
        "class_name": "1",
        "ip": "10.150.184.118",
        "ip6": "::",
        "os_name": "MAC auth",
        "rad_online_id": "63568314",
    },
    "0": {"class_name": "", "ip": "", "ip6": "", "os_name": "", "rad_online_id": "0"},
}
