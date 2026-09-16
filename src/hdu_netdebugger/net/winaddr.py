"""Win32 API 里 IPv4 地址的字节序转换（**只此一处**）.

``IcmpSendEcho`` 的 ``DestinationAddress``、``GetBestRoute`` 的 ``dwDestAddr``
都是"内存里就是网络序字节"的 ``ULONG``：小端机器上 192.0.2.1 对应的数值是
``0x010200C0``，而 ``int(IPv4Address(...))`` 给出的是字节反过来的 ``0xC0000201``。

这个坑已经踩过一次 —— Windows 保底 Ping 后端因此去 ping 了**另一个地址**，
把可达目标全报成超时。所以转换只留这一份，谁要用谁来调。
"""

from __future__ import annotations

from ipaddress import IPv4Address

__all__ = ["network_order_ipaddr", "text_from_network_order"]


def network_order_ipaddr(address: str | IPv4Address) -> int:
    """把 IPv4 地址转成 Win32 需要的"网络序 ``ULONG``"。

    Raises:
        ValueError: 不是合法的 IPv4 地址（IPv6 也会在这里失败）。
    """
    return int.from_bytes(IPv4Address(str(address)).packed, "little")


def text_from_network_order(value: int) -> str | None:
    """把 Win32 返回的网络序 ``ULONG`` 还原成点分十进制。

    ``ICMP_ECHO_REPLY.Address`` 用的是与 :func:`network_order_ipaddr` 相反的约定，
    所以还原时同样要按小端解释字节。``0`` 表示"没有地址"，返回 ``None``。
    """
    if not value:
        return None
    return str(IPv4Address(int(value).to_bytes(4, "little")))
