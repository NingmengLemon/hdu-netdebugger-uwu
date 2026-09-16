"""查询"这次探测从哪个接口出去".

校园网诊断里"通不通"往往不够 —— 还得知道**流量是不是真的走了校园网**。
本机（很多同学也是）装着代理/VPN 的 TUN 接口，默认路由被它以 metric 0 抢走，
于是所有 ping 都从隧道出去：结论写"BAS 可达"，读者会以为验的是校园物理链路。

实测（本机，Mihomo TUN 在线）::

    Find-NetRoute 10.150.0.1 -> Mihomo (ifIndex 75)
    Find-NetRoute 223.5.5.5  -> Mihomo (ifIndex 75)
    Find-NetRoute 192.0.2.1 -> 以太网 (ifIndex 15)

更极端的是 ICMP 本身也被接管：``ping -i 1``（TTL=1）对 223.5.5.5 照样有回复，
回复 TTL 与正常请求一模一样（50）。也就是说这种环境下**跳数/路径类证据是假的**，
只有"出站接口"这一项还能说明问题 —— 所以先把它做出来。

Windows 用 ``iphlpapi`` 查最优路由（不需要管理员权限）：优先 ``GetBestRoute2``，
它在本机所有调用形态下都返回 ``ERROR_INVALID_PARAMETER``（87），因此退回同样导出的
旧接口 ``GetBestRoute``（仅 IPv4，正合本工具用途），实测结果与 ``Find-NetRoute`` 一致。
Linux 用 ``/proc/net/route`` 取默认路由做近似；其它平台返回 ``None``（宁缺勿猜）。
"""

from __future__ import annotations

import ctypes
import socket
import sys
from dataclasses import dataclass
from ipaddress import IPv4Address, ip_address
from pathlib import Path

from hdu_netdebugger.net.interfaces import InterfaceInfo, SockaddrIn, SockaddrIn6
from hdu_netdebugger.net.winaddr import network_order_ipaddr

__all__ = ["DefaultRoute", "Egress", "RouteInfo", "default_routes", "describe_route", "route_for"]

_AF_INET = 2
_AF_INET6 = 23


@dataclass(frozen=True, slots=True)
class RouteInfo:
    """一次探测的出站路由。

    ``interface_name`` 只有平台直接给了名字时才有值（Linux）；Windows 需要调用方
    拿 ``interface_index`` 去接口列表里反查名字与类型。
    """

    interface_index: int | None = None
    interface_name: str | None = None
    next_hop: str | None = None
    metric: int | None = None


@dataclass(frozen=True, slots=True)
class Egress:
    """ "这次探测从哪个接口出去"的完整答案（路由 + 反查到的接口）。"""

    target: str
    route: RouteInfo | None = None
    interface: InterfaceInfo | None = None

    @property
    def is_tunnel_like(self) -> bool:
        """出口是不是 VPN / 隧道类接口（TUN、Wintun、propVirtual…）。"""
        return self.interface is not None and self.interface.is_vpn_like

    def describe(self) -> str:
        """渲染成一行说明，供检查项的详细日志使用。"""
        label = self.interface.name if self.interface is not None else None
        return describe_route(self.route, interface_label=label)


@dataclass(frozen=True, slots=True)
class DefaultRoute:
    """一条 IPv4 默认路由（``0.0.0.0/0``）。"""

    interface_index: int | None = None
    next_hop: str | None = None
    metric: int | None = None


def default_routes() -> tuple[DefaultRoute, ...]:
    """列出本机**全部** IPv4 默认路由，按 metric 升序。

    为什么需要"全部"而不只是最优的那条：校园网里挂代理 / VPN 的机器常常同时存在
    多条 ``0.0.0.0/0``，由 metric 决定谁生效 —— 本机实测同时有 Mihomo（metric 0）、
    以太网（25）与 WLAN（45）三条。只看"最优那条"看不出这个竞争关系，
    而它正是"网线插着却从隧道出去"的原因。

    非 Windows 平台返回空元组（Linux 上 `/proc/net/route` 只能给出最优的一条，
    见 :func:`_linux_default_route`；宁缺勿猜）。
    """
    if sys.platform != "win32":
        return ()
    return _windows_default_routes()


def route_for(target: str | IPv4Address) -> RouteInfo | None:
    """查前往 ``target`` 的最优路由；查不到或平台不支持时返回 ``None``。"""
    try:
        address = ip_address(str(target))
    except ValueError:
        return None
    if address.version != 4:
        # 本工具只对 IPv4 目标做可达性判断；IPv6 交给未来单独的检查项
        return None
    if sys.platform == "win32":
        return _windows_route(address)
    if sys.platform.startswith("linux"):
        return _linux_default_route()
    return None


def describe_route(route: RouteInfo | None, *, interface_label: str | None = None) -> str:
    """把路由渲染成一行人类可读的说明。

    ``interface_label`` 是调用方用接口列表反查出来的名字（Windows 需要）；
    给了它就用它，同时把 ifIndex 一并带上便于对照 ``route print``。
    """
    if route is None:
        return "出站: (未知)"

    if interface_label:
        head = interface_label
    elif route.interface_name:
        head = route.interface_name
    elif route.interface_index is not None:
        head = f"ifIndex {route.interface_index}"
    else:
        head = "(未知接口)"

    extra: list[str] = []
    if interface_label and route.interface_index is not None:
        extra.append(f"ifIndex {route.interface_index}")
    if route.next_hop:
        extra.append(f"下一跳 {route.next_hop}")
    if route.metric is not None:
        extra.append(f"metric {route.metric}")

    return f"出站: {head}" + (f"（{'，'.join(extra)}）" if extra else "")


# ---------------------------------------------------------------------------
# Windows：GetBestRoute2
# ---------------------------------------------------------------------------


class SockaddrInet(ctypes.Union):
    """``SOCKADDR_INET``：IPv4 / IPv6 地址的联合体（28 字节）。"""

    _fields_ = (
        ("Ipv4", SockaddrIn),
        ("Ipv6", SockaddrIn6),
        ("si_family", ctypes.c_uint16),
    )


class IpAddressPrefix(ctypes.Structure):
    """``IP_ADDRESS_PREFIX``：地址前缀 + 前缀长度（结构体本身有 3 字节填充）。"""

    _fields_ = (
        ("Prefix", SockaddrInet),
        ("PrefixLength", ctypes.c_uint8),
    )


class MibIpForwardRow2(ctypes.Structure):
    """``MIB_IPFORWARD_ROW2``。

    只用到 ``InterfaceIndex`` / ``NextHop`` / ``Metric``，但**必须**把前面的字段
    （尤其是 :class:`IpAddressPrefix` 的填充）都声明出来，否则偏移全错。
    """

    _fields_ = (
        ("InterfaceLuid", ctypes.c_uint64),
        ("InterfaceIndex", ctypes.c_uint32),
        ("DestinationPrefix", IpAddressPrefix),
        ("NextHop", SockaddrInet),
        ("SitePrefixLength", ctypes.c_uint8),
        ("ValidLifetime", ctypes.c_uint32),
        ("PreferredLifetime", ctypes.c_uint32),
        ("Metric", ctypes.c_uint32),
        ("Protocol", ctypes.c_uint32),
        ("Loopback", ctypes.c_ubyte),
        ("AutoconfigureAddress", ctypes.c_ubyte),
        ("Publish", ctypes.c_ubyte),
        ("Immortal", ctypes.c_ubyte),
        ("Age", ctypes.c_uint32),
        ("Origin", ctypes.c_uint32),
    )


def _sockaddr_inet_text(value: SockaddrInet) -> str | None:
    """把 ``SOCKADDR_INET`` 转成字符串。"""
    if value.si_family == _AF_INET:
        return socket.inet_ntop(socket.AF_INET, bytes(value.Ipv4.sin_addr))
    if value.si_family == _AF_INET6:
        return socket.inet_ntop(socket.AF_INET6, bytes(value.Ipv6.sin6_addr))
    return None


class MibIpForwardRow(ctypes.Structure):
    """``MIB_IPFORWARDROW``：旧接口 ``GetBestRoute`` 的行结构（13 个 DWORD）。"""

    _fields_ = (
        ("dwForwardDest", ctypes.c_uint32),
        ("dwForwardMask", ctypes.c_uint32),
        ("dwForwardPolicy", ctypes.c_uint32),
        ("dwForwardNextHop", ctypes.c_uint32),
        ("dwForwardIfIndex", ctypes.c_uint32),
        ("dwForwardType", ctypes.c_uint32),
        ("dwForwardProto", ctypes.c_uint32),
        ("dwForwardAge", ctypes.c_uint32),
        ("dwForwardNextHopAS", ctypes.c_uint32),
        ("dwForwardMetric1", ctypes.c_uint32),
        ("dwForwardMetric2", ctypes.c_uint32),
        ("dwForwardMetric3", ctypes.c_uint32),
        ("dwForwardMetric4", ctypes.c_uint32),
        ("dwForwardMetric5", ctypes.c_uint32),
    )


class MibIpForwardTable2(ctypes.Structure):
    """``MIB_IPFORWARD_TABLE2``：``NumEntries`` + 内联变长行数组。

    数组只声明 1 个元素，实际按 ``NumEntries`` 重新映射（见 :func:`_windows_default_routes`）。
    """

    _fields_ = (
        ("NumEntries", ctypes.c_uint32),
        ("Table", MibIpForwardRow2 * 1),
    )


def _windows_default_routes() -> tuple[DefaultRoute, ...]:
    """``GetIpForwardTable2``：把所有 IPv4 默认路由列出来，按 metric 升序。"""
    iphlpapi = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)
    iphlpapi.GetIpForwardTable2.restype = ctypes.c_uint32
    iphlpapi.GetIpForwardTable2.argtypes = (
        ctypes.c_uint16,  # Family
        ctypes.POINTER(ctypes.POINTER(MibIpForwardTable2)),  # Table
    )
    iphlpapi.FreeMibTable.restype = None
    iphlpapi.FreeMibTable.argtypes = (ctypes.c_void_p,)

    table = ctypes.POINTER(MibIpForwardTable2)()
    if iphlpapi.GetIpForwardTable2(_AF_INET, ctypes.byref(table)) != 0:
        return ()

    try:
        count = int(table.contents.NumEntries)
        if count <= 0:
            return ()
        rows_type = MibIpForwardRow2 * count
        offset = MibIpForwardTable2.Table.offset
        rows = rows_type.from_address(ctypes.addressof(table.contents) + offset)
        routes = [route for row in rows if (route := _default_route_from_row(row)) is not None]
    finally:
        iphlpapi.FreeMibTable(table)

    return tuple(sorted(routes, key=lambda item: (item.metric or 0, item.interface_index or 0)))


def _default_route_from_row(row: MibIpForwardRow2) -> DefaultRoute | None:
    """把一行路由过滤成默认路由（``0.0.0.0/0``），其它前缀返回 ``None``。"""
    prefix = row.DestinationPrefix
    if prefix.Prefix.si_family != _AF_INET or int(prefix.PrefixLength) != 0:
        return None
    return DefaultRoute(
        interface_index=int(row.InterfaceIndex) or None,
        next_hop=_sockaddr_inet_text(row.NextHop),
        metric=int(row.Metric),
    )


def _windows_route(address: IPv4Address) -> RouteInfo | None:
    """查前往该地址的最优路由：先 ``GetBestRoute2``，失败再 ``GetBestRoute``。"""
    iphlpapi = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)
    return _windows_route_v2(iphlpapi, address) or _windows_route_legacy(iphlpapi, address)


def _windows_route_v2(iphlpapi: ctypes.WinDLL, address: IPv4Address) -> RouteInfo | None:
    """``GetBestRoute2``（Vista+ 推荐接口）。"""
    iphlpapi.GetBestRoute2.restype = ctypes.c_uint32
    iphlpapi.GetBestRoute2.argtypes = (
        ctypes.c_void_p,  # InterfaceLuid（NULL = 自动选）
        ctypes.c_uint32,  # InterfaceIndex（0 = 自动选）
        ctypes.c_void_p,  # SourceAddress（NULL = 自动选）
        ctypes.POINTER(SockaddrInet),  # DestinationAddress
        ctypes.c_uint32,  # AddressSortOptions
        ctypes.POINTER(MibIpForwardRow2),  # BestRoute
        ctypes.c_void_p,  # BestSourceAddress（可选）
    )

    destination = SockaddrInet()
    destination.Ipv4 = SockaddrIn(
        sin_family=_AF_INET,
        sin_port=0,
        sin_addr=(ctypes.c_uint8 * 4)(*address.packed),
        sin_zero=(ctypes.c_uint8 * 8)(),
    )
    row = MibIpForwardRow2()
    status = iphlpapi.GetBestRoute2(
        None,
        0,
        None,
        ctypes.byref(destination),
        0,
        ctypes.byref(row),
        None,
    )
    if status != 0:
        return None
    return RouteInfo(
        interface_index=int(row.InterfaceIndex) or None,
        next_hop=_sockaddr_inet_text(row.NextHop),
        metric=int(row.Metric),
    )


def _windows_route_legacy(iphlpapi: ctypes.WinDLL, address: IPv4Address) -> RouteInfo | None:
    """``GetBestRoute``：同样是导出的公开接口，仅支持 IPv4。

    参数是"网络序 ULONG"，所以必须走 :func:`network_order_ipaddr` —— 用
    ``int(IPv4Address(...))`` 会去查另一个地址的路由。
    """
    iphlpapi.GetBestRoute.restype = ctypes.c_uint32
    iphlpapi.GetBestRoute.argtypes = (
        ctypes.c_uint32,  # dwDestAddr（网络序）
        ctypes.c_uint32,  # dwSourceAddr（0 = 自动选）
        ctypes.POINTER(MibIpForwardRow),  # pBestRoute
    )

    row = MibIpForwardRow()
    status = iphlpapi.GetBestRoute(network_order_ipaddr(address), 0, ctypes.byref(row))
    if status != 0:
        return None
    return RouteInfo(
        interface_index=int(row.dwForwardIfIndex) or None,
        next_hop=socket.inet_ntoa(row.dwForwardNextHop.to_bytes(4, "little")),
        metric=int(row.dwForwardMetric1),
    )


# ---------------------------------------------------------------------------
# Linux：/proc/net/route（近似）
# ---------------------------------------------------------------------------


def _linux_default_route() -> RouteInfo | None:
    """取默认路由（``Destination`` 为全零）中 metric 最小的一条。

    这只是近似：``/proc/net/route`` 只有 IPv4 路由表，且不区分目标前缀的
    最长匹配。对"探测的公网/校园地址走哪张网卡"这个用途足够。

    注意：本函数刻意**不接参数**（目标地址不影响结果，默认路由说了算）。
    """
    try:
        lines = Path("/proc/net/route").read_text(encoding="ascii").splitlines()
    except OSError:
        return None

    best: tuple[int, str, str] | None = None
    for line in lines[1:]:  # 首行是表头
        fields = line.split()
        if len(fields) < 8 or fields[1] != "00000000":
            continue
        interface, _destination, gateway_hex, _flags, _ref, _use, metric_text = fields[:7]
        try:
            # 注意：Destination / Gateway / Mask 是十六进制，而 Metric 是**十进制**
            # （把 "100" 当十六进制读会得到 256，从而选错默认路由）
            metric = int(metric_text)
            gateway = socket.inet_ntoa(bytes.fromhex(gateway_hex)[::-1])
        except ValueError:
            continue
        if best is None or metric < best[0]:
            best = (metric, interface, gateway)

    if best is None:
        return None
    metric, interface, gateway = best
    try:
        index: int | None = socket.if_nametoindex(interface)
    except OSError:  # pragma: no cover - 接口刚好消失
        index = None
    return RouteInfo(
        interface_index=index,
        interface_name=interface,
        next_hop=gateway,
        metric=metric,
    )
