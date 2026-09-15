"""网络接口枚举.

对应 C# 原版 ``GetInfo`` 里的 ``NetworkInterface.GetAllNetworkInterfaces()``，
需要拿到接口的**类型**（以太网 / Tunnel / propVirtual），因为"是否挂着 VPN"
正是靠它判断的。

* Windows：通过 ctypes 调用 ``iphlpapi!GetAdaptersAddresses``，可以拿到与 .NET 完全一致的
  ``IfType`` / ``Description`` / ``FriendlyName`` / ``OperStatus``；
* 其它平台：退回 ``psutil`` 并叠加接口名启发式判断类型。
"""

from __future__ import annotations

import socket
import sys
from typing import TYPE_CHECKING, Any

from hdu_netdebugger.models.interface import InterfaceInfo, InterfaceKind

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["list_interfaces"]

# ---------------------------------------------------------------------------
# Windows ``IFTYPE``（``ipifcons.h``）
# ---------------------------------------------------------------------------

_IF_TYPE_ETHERNET_CSMACD = 6
_IF_TYPE_PPP = 23
_IF_TYPE_SOFTWARE_LOOPBACK = 24
_IF_TYPE_PROP_VIRTUAL = 53
_IF_TYPE_IEEE80211 = 71
_IF_TYPE_TUNNEL = 131

_IF_TYPE_MAP: dict[int, InterfaceKind] = {
    _IF_TYPE_ETHERNET_CSMACD: InterfaceKind.ETHERNET,
    _IF_TYPE_PPP: InterfaceKind.PPP,
    _IF_TYPE_SOFTWARE_LOOPBACK: InterfaceKind.LOOPBACK,
    _IF_TYPE_PROP_VIRTUAL: InterfaceKind.PROP_VIRTUAL,
    _IF_TYPE_IEEE80211: InterfaceKind.WIRELESS,
    _IF_TYPE_TUNNEL: InterfaceKind.TUNNEL,
}

_IF_OPER_STATUS_UP = 1

#: ``GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER``
_GAA_FLAGS = 0x02 | 0x04 | 0x08
_ERROR_BUFFER_OVERFLOW = 111
_AF_UNSPEC = 0
_AF_INET = 2
_AF_INET6 = 23

# ---------------------------------------------------------------------------
# 非 Windows 平台的名称启发式
# ---------------------------------------------------------------------------

_VPN_NAME_HINTS = (
    "tun",
    "tap",
    "utun",
    "wg",
    "wireguard",
    "openvpn",
    "tailscale",
    "zerotier",
    "mihomo",
    "clash",
    "singbox",
    "sing-box",
    "wintun",
    "vpn",
    "ppp",
    "nordlynx",
    "proton",
    "softether",
    "anyconnect",
    "easyconnect",
    "sangfor",
    "v2ray",
    "xray",
)

_WIRELESS_NAME_HINTS = ("wlan", "wi-fi", "wifi", "wireless", "wlp", "en0", "ath", "ra")


def list_interfaces(*, include_loopback: bool = False) -> list[InterfaceInfo]:
    """枚举本机网络接口。

    Args:
        include_loopback: 是否包含环回接口，默认不包含。

    Returns:
        按名称排序的接口快照列表。枚举失败时返回空列表（由调用方判定为失败）。
    """
    interfaces = _windows_interfaces() if sys.platform == "win32" else None
    if interfaces is None:
        interfaces = _psutil_interfaces()
    if not include_loopback:
        interfaces = [item for item in interfaces if item.kind is not InterfaceKind.LOOPBACK]
    return sorted(interfaces, key=lambda item: item.name)


# ---------------------------------------------------------------------------
# Windows：GetAdaptersAddresses
# ---------------------------------------------------------------------------


def _windows_interfaces() -> list[InterfaceInfo] | None:
    """用 ``GetAdaptersAddresses`` 枚举接口；失败时返回 ``None`` 让调用方退回 psutil。"""
    import ctypes  # noqa: PLC0415 - 仅 Windows 分支需要
    from ctypes import wintypes  # noqa: PLC0415

    class SocketAddress(ctypes.Structure):
        _fields_ = (
            ("lpSockaddr", ctypes.c_void_p),
            ("iSockaddrLength", ctypes.c_int),
        )

    class IpAdapterUnicastAddress(ctypes.Structure):
        _fields_ = (
            ("Length", ctypes.c_uint32),
            ("Flags", ctypes.c_uint32),
            ("Next", ctypes.c_void_p),
            ("Address", SocketAddress),
            ("PrefixOrigin", ctypes.c_uint32),
            ("SuffixOrigin", ctypes.c_uint32),
            ("DadState", ctypes.c_uint32),
            ("ValidLifetime", ctypes.c_uint32),
            ("PreferredLifetime", ctypes.c_uint32),
            ("LeaseLifetime", ctypes.c_uint32),
            ("OnLinkPrefixLength", ctypes.c_uint8),
        )

    class IpAdapterAddresses(ctypes.Structure):
        _fields_ = (
            ("Length", ctypes.c_uint32),
            ("IfIndex", ctypes.c_uint32),
            ("Next", ctypes.c_void_p),
            ("AdapterName", ctypes.c_char_p),
            ("FirstUnicastAddress", ctypes.c_void_p),
            ("FirstAnycastAddress", ctypes.c_void_p),
            ("FirstMulticastAddress", ctypes.c_void_p),
            ("FirstDnsServerAddress", ctypes.c_void_p),
            ("DnsSuffix", ctypes.c_wchar_p),
            ("Description", ctypes.c_wchar_p),
            ("FriendlyName", ctypes.c_wchar_p),
            ("PhysicalAddress", ctypes.c_uint8 * 8),
            ("PhysicalAddressLength", ctypes.c_uint32),
            ("Flags", ctypes.c_uint32),
            ("Mtu", ctypes.c_uint32),
            ("IfType", ctypes.c_uint32),
            ("OperStatus", ctypes.c_uint32),
            # 后续字段（Ipv6IfIndex 等）本工具用不到，省略即可 —— 我们只读指针不按值拷贝
            ("Ipv6IfIndex", ctypes.c_uint32),
            ("ZoneIndices", ctypes.c_uint32 * 16),
            ("FirstPrefix", ctypes.c_void_p),
            ("TransmitLinkSpeed", ctypes.c_uint64),
            ("ReceiveLinkSpeed", ctypes.c_uint64),
        )

    try:
        iphlpapi = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)
    except OSError:  # pragma: no cover - 理论上不会发生
        return None

    iphlpapi.GetAdaptersAddresses.restype = wintypes.ULONG
    iphlpapi.GetAdaptersAddresses.argtypes = (
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.ULONG),
    )

    size = wintypes.ULONG(15 * 1024)
    buffer = ctypes.create_string_buffer(size.value)
    for _ in range(4):
        ret = iphlpapi.GetAdaptersAddresses(
            _AF_UNSPEC,
            _GAA_FLAGS,
            None,
            ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.byref(size),
        )
        if ret == 0:
            break
        if ret != _ERROR_BUFFER_OVERFLOW:
            return None
        buffer = ctypes.create_string_buffer(size.value)
    else:  # pragma: no cover - 缓冲区反复不足
        return None

    if ret != 0:  # pragma: no cover - 防御性分支
        return None

    interfaces: list[InterfaceInfo] = []
    cursor = ctypes.cast(buffer, ctypes.POINTER(IpAdapterAddresses))
    while cursor:
        adapter = cursor.contents
        ipv4, ipv6 = _windows_unicast_addresses(
            adapter.FirstUnicastAddress, IpAdapterUnicastAddress
        )
        interfaces.append(
            InterfaceInfo(
                name=adapter.FriendlyName or adapter.AdapterName or "",
                description=adapter.Description or "",
                kind=_IF_TYPE_MAP.get(int(adapter.IfType), InterfaceKind.OTHER),
                is_up=int(adapter.OperStatus) == _IF_OPER_STATUS_UP,
                ipv4=ipv4,
                ipv6=ipv6,
                mtu=int(adapter.Mtu) or None,
                speed_mbps=_link_speed_mbps(int(adapter.TransmitLinkSpeed)),
            )
        )
        cursor = (
            ctypes.cast(adapter.Next, ctypes.POINTER(IpAdapterAddresses)) if adapter.Next else None
        )

    return interfaces


def _windows_unicast_addresses(
    head: int | None,
    unicast_type: type[Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """遍历单播地址链表，解析出 IPv4 / IPv6 字符串。"""
    import ctypes  # noqa: PLC0415 - 与 _windows_interfaces 的延迟导入保持一致

    if not head:
        return (), ()

    class SockaddrIn(ctypes.Structure):
        _fields_ = (
            ("sin_family", ctypes.c_uint16),
            ("sin_port", ctypes.c_uint16),
            ("sin_addr", ctypes.c_uint8 * 4),
            ("sin_zero", ctypes.c_uint8 * 8),
        )

    class SockaddrIn6(ctypes.Structure):
        _fields_ = (
            ("sin6_family", ctypes.c_uint16),
            ("sin6_port", ctypes.c_uint16),
            ("sin6_flowinfo", ctypes.c_uint32),
            ("sin6_addr", ctypes.c_uint8 * 16),
            ("sin6_scope_id", ctypes.c_uint32),
        )

    ipv4: list[str] = []
    ipv6: list[str] = []
    cursor = ctypes.cast(head, ctypes.POINTER(unicast_type))
    while cursor:
        item = cursor.contents
        raw = item.Address.lpSockaddr
        if raw:
            family = ctypes.cast(raw, ctypes.POINTER(ctypes.c_uint16)).contents.value
            if family == _AF_INET:
                sockaddr = ctypes.cast(raw, ctypes.POINTER(SockaddrIn)).contents
                ipv4.append(socket.inet_ntop(socket.AF_INET, bytes(sockaddr.sin_addr)))
            elif family == _AF_INET6:
                sockaddr6 = ctypes.cast(raw, ctypes.POINTER(SockaddrIn6)).contents
                ipv6.append(socket.inet_ntop(socket.AF_INET6, bytes(sockaddr6.sin6_addr)))
        cursor = ctypes.cast(item.Next, ctypes.POINTER(unicast_type)) if item.Next else None

    return tuple(ipv4), tuple(ipv6)


def _link_speed_mbps(bits_per_second: int) -> int | None:
    """把链路速率（bit/s）换算成 Mbps；无意义的值返回 ``None``。"""
    if bits_per_second <= 0:
        return None
    return max(1, round(bits_per_second / 1_000_000))


# ---------------------------------------------------------------------------
# 其它平台：psutil + 名称启发式
# ---------------------------------------------------------------------------


def _psutil_interfaces() -> list[InterfaceInfo]:
    """用 ``psutil`` 枚举接口，并通过接口名猜测类型。"""
    import psutil  # noqa: PLC0415 - 仅回退分支需要

    try:
        addresses = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except (OSError, RuntimeError):  # pragma: no cover - 依赖平台
        return []

    interfaces: list[InterfaceInfo] = []
    for name, entries in addresses.items():
        ipv4 = tuple(item.address for item in entries if item.family == socket.AF_INET)
        ipv6 = tuple(
            item.address.split("%", 1)[0] for item in entries if item.family == socket.AF_INET6
        )
        stat = stats.get(name)
        interfaces.append(
            InterfaceInfo(
                name=name,
                description=name,
                kind=_guess_kind(name, ipv4),
                is_up=bool(stat.isup) if stat is not None else False,
                ipv4=ipv4,
                ipv6=ipv6,
                mtu=int(stat.mtu) if stat is not None and stat.mtu > 0 else None,
                speed_mbps=int(stat.speed) if stat is not None and stat.speed > 0 else None,
            )
        )
    return interfaces


def _guess_kind(name: str, ipv4: Iterable[str]) -> InterfaceKind:
    """根据接口名猜类型（仅非 Windows 平台的回退路径）。"""
    lowered = name.lower()
    if lowered.startswith("lo") or lowered == "loopback":
        return InterfaceKind.LOOPBACK
    if any(hint in lowered for hint in _VPN_NAME_HINTS):
        # ppp/tun 属于点对点隧道；其余按 propVirtual 处理（与 mihomo 的 TUN 一致）
        return InterfaceKind.PPP if "ppp" in lowered else InterfaceKind.TUNNEL
    if any(hint in lowered for hint in _WIRELESS_NAME_HINTS):
        return InterfaceKind.WIRELESS
    if lowered.startswith(("en", "eth", "eno", "ens", "enp", "em")) and any(ipv4):
        return InterfaceKind.ETHERNET
    return InterfaceKind.OTHER
