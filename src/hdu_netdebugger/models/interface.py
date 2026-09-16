"""网络接口模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address
from typing import TYPE_CHECKING

from hdu_netdebugger.constants import NetworkKind, dorm_or_campus

if TYPE_CHECKING:
    from collections.abc import Iterable
    from ipaddress import IPv4Network

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
    """一个网络接口的快照。

    除了原版 ``BuildIFInfo`` 用的那四项，还带上了真正能定位故障的配置信息：
    Windows 系统索引（用来把路由表里的出口映射回接口）、网关、DNS 服务器、
    以及 DHCP 是否启用。排查校园网时这几项往往比网卡名字有用得多 ——
    ICU/电专互联的排查流程第一步就是"是否已正确获取 IP 地址"。
    """

    name: str
    description: str
    kind: InterfaceKind
    is_up: bool
    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()
    mtu: int | None = None
    speed_mbps: int | None = None
    #: 系统接口索引（Windows ``IfIndex``）；未知时为 ``None``
    index: int | None = None
    #: 默认网关；没有默认路由时为 ``None``
    gateway: str | None = None
    #: 系统配置的 DNS 服务器
    dns_servers: tuple[str, ...] = ()
    #: 该接口是否启用 DHCP；非 Windows 或未知时为 ``None``
    dhcp_enabled: bool | None = None
    #: 网卡物理地址（``aa:bb:cc:dd:ee:ff`` 形式）；拿不到时为 ``None``
    mac: str | None = None

    @property
    def is_random_mac(self) -> bool:
        """MAC 是否为"本地管理地址"（即系统随机化 MAC）。

        判据是 IEEE 802 的 U/L 位：首字节的 bit 1 置位表示本地管理。Windows 的
        "随机硬件地址"、手机的"随机 MAC"都会置这一位 —— 而杭电是按 MAC 做无感知
        认证的，随机 MAC 会导致掉认证，管理员在群里两次把异常指向了这一点。
        """
        if not self.mac:
            return False
        first = self.mac.split(":", 1)[0]
        try:
            return bool(int(first, 16) & 0x02)
        except ValueError:  # pragma: no cover - MAC 由我们自己格式化，理论上不会失败
            return False

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
        """该接口所属的杭电网络区域（不含用户追加的网段，见 :meth:`classify`）。"""
        return dorm_or_campus(self.ipv4_addresses[0]) if self.ipv4_addresses else None

    def classify(self, extra: Iterable[IPv4Network] = ()) -> NetworkKind | None:
        """判断该接口属于哪个网段，可带上用户追加的"也算校园网"的网段。

        逐地址判断（一个接口可能同时有多个 IPv4），只要有一个落进已知网段就返回它。
        """
        for address in self.ipv4_addresses:
            kind = dorm_or_campus(address, extra=extra)
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

    def describe_config(self) -> list[str]:
        """接口的 IP 配置证据（原版没有，排查时最常用）。

        刻意与 :meth:`describe` 分开：前者要保持与原版日志逐字一致，
        这里是新增证据，不能混进去。
        """
        if self.dhcp_enabled is None:
            dhcp = "未知"
        else:
            dhcp = "已启用" if self.dhcp_enabled else "未启用（静态配置）"
        return [
            f"DHCP: {dhcp}",
            f"默认网关: {self.gateway or '(无)'}",
            f"DNS 服务器: {', '.join(self.dns_servers) or '(无)'}",
            f"链路: {'已建立' if self.is_up else '未建立'}"
            + (f"，协商速率 {self.speed_mbps} Mbps" if self.speed_mbps else ""),
            f"MAC: {self.mac or '(未知)'}"
            + ("（本地管理/随机化地址）" if self.is_random_mac else ""),
            (
                f"系统接口索引: {self.index if self.index is not None else '(未知)'}"
                f" / MTU: {self.mtu if self.mtu is not None else '(未知)'}"
            ),
        ]
