"""网络接口模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address

from hdu_netdebugger.constants import NetworkKind, dorm_or_campus

__all__ = ["InterfaceInfo", "InterfaceKind"]


class InterfaceKind(StrEnum):
    """接口类型。

    取值来自 Windows ``IFTYPE``（``ipifcons.h``），在其它平台上由
    :mod:`hdu_netdebugger.net.interfaces` 做名称启发式推断。
    """

    ETHERNET = "ethernet"
    WIRELESS = "wireless"
    TUNNEL = "tunnel"
    PROP_VIRTUAL = "prop_virtual"
    LOOPBACK = "loopback"
    PPP = "ppp"
    OTHER = "other"

    @property
    def label(self) -> str:
        """中文显示名（与原版日志措辞一致）。"""
        return _KIND_LABELS[self]


_KIND_LABELS: dict[InterfaceKind, str] = {
    InterfaceKind.ETHERNET: "以太网",
    InterfaceKind.WIRELESS: "无线",
    InterfaceKind.TUNNEL: "Tunnel",
    InterfaceKind.PROP_VIRTUAL: "propVirtual",
    InterfaceKind.LOOPBACK: "环回",
    InterfaceKind.PPP: "PPP",
    InterfaceKind.OTHER: "其他",
}


@dataclass(frozen=True, slots=True)
class InterfaceInfo:
    """一个网络接口的快照。"""

    name: str
    description: str
    kind: InterfaceKind
    is_up: bool
    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()
    mtu: int | None = None
    speed_mbps: int | None = None

    @property
    def ipv4_addresses(self) -> tuple[IPv4Address, ...]:
        """IPv4 地址（已解析为 :class:`~ipaddress.IPv4Address`，解析失败者被丢弃）。"""
        parsed: list[IPv4Address] = []
        for text in self.ipv4:
            try:
                parsed.append(IPv4Address(text))
            except ValueError:
                continue
        return tuple(parsed)

    @property
    def network(self) -> NetworkKind | None:
        """该接口所属的杭电网络区域；不在校园网网段时返回 ``None``。"""
        for address in self.ipv4_addresses:
            kind = dorm_or_campus(address)
            if kind is not None:
                return kind
        return None

    @property
    def is_vpn_like(self) -> bool:
        """是否为 VPN / 隧道类接口。"""
        return self.kind in {InterfaceKind.TUNNEL, InterfaceKind.PROP_VIRTUAL}

    def describe(self) -> list[str]:
        """渲染成与原版 ``BuildIFInfo`` 一致的详细日志行。"""
        return [
            f"接口名称: {self.name}",
            f"描述: {self.description}",
            f"IPv4 地址: {', '.join(self.ipv4)}",
            f"IPv6 地址: {', '.join(self.ipv6)}",
        ]
