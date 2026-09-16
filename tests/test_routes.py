"""路由查询与出站归属：这次探测到底经由哪张网卡出去.

判据来自实测（本机 Mihomo TUN 在线时）：

* 校园 BAS / 深澜 / 阿里 DNS 的出口都是 ``ifIndex 75 Mihomo``，只有局域网网关
  走物理网卡（``ifIndex 15``）—— 也就是那几项"可达"结论并不代表校园物理链路；
* ``ping -i 1``（TTL=1）对 223.5.5.5 照样有回复，说明这种环境下**跳数/路径证据是假的**，
  只有"出站接口"这一项还能说明问题。
"""

from __future__ import annotations

import sys
from ipaddress import IPv4Address

import pytest

from hdu_netdebugger.models.interface import InterfaceInfo, InterfaceKind
from hdu_netdebugger.net import routes as routes_module
from hdu_netdebugger.net.routes import Egress, RouteInfo, describe_route, route_for
from hdu_netdebugger.net.winaddr import network_order_ipaddr, text_from_network_order

FAKE_PROC_ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t0102A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
    "wlan0\t00000000\t0102A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
    "eth0\t0002A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
)


def _fake_path_class(text: str, *, error: bool = False) -> type:
    """只实现 ``read_text`` 的假 Path（用来喂 /proc/net/route 的内容）。"""

    class _FakePath:
        def __init__(self, path: str) -> None:
            del path

        def read_text(self, **_kwargs: object) -> str:
            if error:
                raise OSError("没有这个文件")
            return text

    return _FakePath


class TestNetworkOrderIpaddr:
    """Win32 要的是"网络序 ULONG"，不是 ``int(IPv4Address(...))``。"""

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("127.0.0.1", 0x0100007F),
            ("192.0.2.1", 0x010200C0),
            ("223.5.5.5", 0x050505DF),
        ],
    )
    def test_matches_wire_order(self, address: str, expected: int) -> None:
        assert network_order_ipaddr(address) == expected

    def test_accepts_address_object(self) -> None:
        assert network_order_ipaddr(IPv4Address("10.150.0.1")) == 0x0100960A

    def test_rejects_ipv6(self) -> None:
        with pytest.raises(ValueError):
            network_order_ipaddr("2001:db8::1")

    @pytest.mark.parametrize(
        ("text", "value"),
        [
            ("192.0.2.1", 0x010200C0),
            ("127.0.0.1", 0x0100007F),
        ],
    )
    def test_text_from_network_order_round_trips(self, text: str, value: int) -> None:
        """``ICMP_ECHO_REPLY.Address`` 的还原要与输入约定互逆（同一个坑的另一半）。"""
        assert network_order_ipaddr(text) == value
        assert text_from_network_order(value) == text

    def test_zero_means_no_address(self) -> None:
        assert text_from_network_order(0) is None

    def test_differs_from_host_order(self) -> None:
        assert network_order_ipaddr("192.0.2.1") != int(IPv4Address("192.0.2.1"))


class TestDescribeRoute:
    def test_unknown_route(self) -> None:
        assert describe_route(None) == "出站: (未知)"

    def test_uses_interface_label_and_extras(self) -> None:
        route = RouteInfo(interface_index=75, next_hop="198.18.0.2", metric=0)
        assert (
            describe_route(route, interface_label="Mihomo")
            == "出站: Mihomo（ifIndex 75，下一跳 198.18.0.2，metric 0）"
        )

    def test_falls_back_to_platform_name(self) -> None:
        route = RouteInfo(interface_index=2, interface_name="eth0", metric=100)
        assert describe_route(route) == "出站: eth0（metric 100）"

    def test_falls_back_to_index(self) -> None:
        assert describe_route(RouteInfo(interface_index=9)) == "出站: ifIndex 9"

    def test_no_fields_at_all(self) -> None:
        assert describe_route(RouteInfo()) == "出站: (未知接口)"


class TestEgress:
    def test_tunnel_like_interface(self) -> None:
        interface = InterfaceInfo(
            name="Mihomo",
            description="Meta Tunnel",
            kind=InterfaceKind.PROP_VIRTUAL,
            is_up=True,
            index=75,
        )
        egress = Egress(
            target="10.150.0.1",
            route=RouteInfo(interface_index=75, metric=0),
            interface=interface,
        )

        assert egress.is_tunnel_like is True
        assert "Mihomo" in egress.describe()

    def test_physical_interface_is_not_tunnel_like(self) -> None:
        interface = InterfaceInfo(
            name="以太网",
            description="Realtek",
            kind=InterfaceKind.ETHERNET,
            is_up=True,
            index=15,
        )
        assert Egress(target="10.150.0.1", interface=interface).is_tunnel_like is False

    def test_missing_interface_is_not_tunnel_like(self) -> None:
        egress = Egress(target="10.150.0.1")
        assert egress.is_tunnel_like is False
        assert egress.describe() == "出站: (未知)"


class TestRouteForTargetHandling:
    @pytest.mark.parametrize("target", ["not-an-ip", "2001:db8::1", ""])
    def test_unusable_targets_return_none(self, target: str) -> None:
        assert route_for(target) is None


class TestLinuxDefaultRoute:
    """``/proc/net/route`` 解析：取 metric 最小的默认路由。"""

    def test_picks_lowest_metric_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(routes_module, "Path", _fake_path_class(FAKE_PROC_ROUTE))

        route = routes_module._linux_default_route()

        assert route is not None
        assert route.interface_name == "eth0", "wlan0 的 metric 更大，不该被选中"
        assert route.next_hop == "192.168.2.1"
        assert route.metric == 100

    def test_no_default_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        only_subnet = (
            FAKE_PROC_ROUTE.splitlines()[0] + "\n" + FAKE_PROC_ROUTE.splitlines()[3] + "\n"
        )
        monkeypatch.setattr(routes_module, "Path", _fake_path_class(only_subnet))

        assert routes_module._linux_default_route() is None

    def test_unreadable_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(routes_module, "Path", _fake_path_class("", error=True))

        assert routes_module._linux_default_route() is None


@pytest.mark.skipif(sys.platform != "win32", reason="真机验证 ctypes 调用链（Linux 走另一条实现）")
class TestWindowsRouteIntegration:
    def test_loopback_has_a_route(self) -> None:
        """回环地址必然有路由，用它验证结构体布局 / 参数没接错。"""
        route = route_for("127.0.0.1")
        assert route is not None
        assert route.interface_index is not None

    def test_public_address_has_a_route(self) -> None:
        assert route_for("223.5.5.5") is not None


class TestSockaddrInetText:
    def test_unknown_family_returns_none(self) -> None:
        """``SOCKADDR_INET`` 的 family 不是 IPv4/IPv6 时给 ``None``，不抛异常。"""
        assert routes_module._sockaddr_inet_text(routes_module.SockaddrInet()) is None

    def test_ipv4_is_rendered(self) -> None:
        value = routes_module.SockaddrInet()
        value.Ipv4 = routes_module.SockaddrIn(
            sin_family=2,
            sin_port=0,
            sin_addr=(routes_module.ctypes.c_uint8 * 4)(192, 0, 2, 1),
            sin_zero=(routes_module.ctypes.c_uint8 * 8)(),
        )
        assert routes_module._sockaddr_inet_text(value) == "192.0.2.1"
