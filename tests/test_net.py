"""网络能力层：Ping 后端选择、系统 ping 解析、HTTP 响应、DNS、网卡、session 与配置。"""

from __future__ import annotations

import sys
from ipaddress import IPv4Address
from typing import Any, ClassVar

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver
import pytest

from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.models.enums import PingStatus
from hdu_netdebugger.models.interface import InterfaceInfo, InterfaceKind
from hdu_netdebugger.models.ping import MultiPingResult, PingReply
from hdu_netdebugger.net import ping as ping_module
from hdu_netdebugger.net.dns import DnsResult, query_a
from hdu_netdebugger.net.http import HttpClient, HttpResponse
from hdu_netdebugger.net.interfaces import _guess_kind, list_interfaces
from hdu_netdebugger.net.ping import (
    Backend,
    BackendUnavailable,
    PingOptions,
    ping_multiple,
    reset_backend_cache,
    resolve_target,
)
from hdu_netdebugger.session import RunSession
from tests.fakes import FakeHttp


def ok_result(address: str) -> MultiPingResult:
    """构造一个"单包成功"的 Ping 结果。"""
    return MultiPingResult(
        target=address,
        replies=(PingReply(sequence=1, status=PingStatus.SUCCESS, rtt_ms=1.0),),
    )


# ---------------------------------------------------------------------------
# DNS 解析器的假实现（替换 dns.asyncresolver.Resolver，让异常分支可确定性覆盖）
# ---------------------------------------------------------------------------


class _FakeRdata:
    def __init__(self, address: str) -> None:
        self.address = address


class _FakeRRset:
    rdtype = dns.rdatatype.A

    def __init__(self, addresses: list[str]) -> None:
        self.name = "portal.hdu.edu.cn."
        self.ttl = 60
        self._addresses = addresses

    def __iter__(self):
        return iter([_FakeRdata(address) for address in self._addresses])


class _FakeAnswer:
    def __init__(self, addresses: list[str]) -> None:
        answer = [_FakeRRset(addresses)] if addresses else []
        self.response = type("_FakeResponse", (), {"answer": answer})()


class _FakeResolver:
    """可编程的 Resolver 替身：改类属性即可让下一次解析抛错或返回空。"""

    error: ClassVar[BaseException | None] = None
    addresses: ClassVar[list[str]] = ["192.168.112.97", "10.0.0.9"]

    def __init__(self, **_kwargs: object) -> None:
        self.nameservers: list[str] = []
        self.lifetime = 5.0
        self.timeout = 2.0

    async def resolve(self, _domain: str, _rdtype: object) -> _FakeAnswer:
        if _FakeResolver.error is not None:
            raise _FakeResolver.error
        return _FakeAnswer(_FakeResolver.addresses)


@pytest.fixture
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> type[_FakeResolver]:
    """把 dnspython 的解析器换成替身，并在用例之间复位。"""
    _FakeResolver.error = None
    _FakeResolver.addresses = ["192.168.112.97", "10.0.0.9"]
    monkeypatch.setattr(dns.asyncresolver, "Resolver", _FakeResolver)
    return _FakeResolver


# ---------------------------------------------------------------------------
# Ping 参数与目标解析
# ---------------------------------------------------------------------------


class TestPingOptions:
    def test_defaults_match_original(self) -> None:
        options = PingOptions()
        assert options.count == 4
        assert options.timeout_s == 1.0
        assert options.payload_size == 32
        assert options.ttl == 64

    def test_is_frozen(self) -> None:
        options = PingOptions()
        with pytest.raises(AttributeError):
            options.count = 9  # ty: ignore[invalid-assignment]


class TestResolveTarget:
    def test_ipv4_passthrough(self) -> None:
        assert resolve_target("10.150.0.1") == "10.150.0.1"

    def test_ipv4_object(self) -> None:
        assert resolve_target(IPv4Address("192.168.112.97")) == "192.168.112.97"

    def test_ipv6_passthrough(self) -> None:
        assert resolve_target("::1") == "::1"

    def test_localhost_resolves(self) -> None:
        assert resolve_target("localhost") in {"127.0.0.1", "::1"}

    def test_invalid_hostname_raises(self) -> None:
        with pytest.raises(OSError):
            resolve_target("this-host-does-not-exist.invalid")


# ---------------------------------------------------------------------------
# 系统 ping 输出解析
# ---------------------------------------------------------------------------

WINDOWS_OUTPUT = (
    "Pinging 127.0.0.1 with 32 bytes of data:\n"
    "Reply from 127.0.0.1: bytes=32 time<1ms TTL=128\n"
    "Reply from 127.0.0.1: bytes=32 time<1ms TTL=128\n"
    "Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)"
)
LINUX_OUTPUT = (
    "PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.\n"
    "64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=12.3 ms\n"
    "64 bytes from 1.1.1.1: icmp_seq=2 ttl=57 time=13 ms\n"
)
CHINESE_OUTPUT = (
    "来自 10.150.0.1 的回复: 字节=32 时间=2ms TTL=64\n"
    "来自 10.150.0.1 的回复: 字节=32 时间=3ms TTL=64\n"
)
TIMEOUT_OUTPUT = "Request timed out.\nRequest timed out.\n"


class TestSystemPingParsing:
    @pytest.mark.parametrize(
        ("output", "expected_success"),
        [
            (WINDOWS_OUTPUT, 2),
            (LINUX_OUTPUT, 2),
            (CHINESE_OUTPUT, 2),
            (TIMEOUT_OUTPUT, 0),
            ("", 0),
        ],
    )
    def test_parses_various_platform_outputs(self, output: str, expected_success: int) -> None:
        result = ping_module._parse_system_ping("1.1.1.1", output, PingOptions(count=4))
        assert result.success_count == expected_success
        assert result.total_count == 4
        assert result.target == "1.1.1.1"

    def test_extracts_rtt_values(self) -> None:
        result = ping_module._parse_system_ping("1.1.1.1", LINUX_OUTPUT, PingOptions(count=2))
        assert result.replies[0].rtt_ms == pytest.approx(12.3)
        assert result.replies[1].rtt_ms == pytest.approx(13.0)

    def test_sub_millisecond_reply_is_parsed(self) -> None:
        """``time<1ms`` 会被解析成 1ms —— 上界近似，足以用于诊断。"""
        result = ping_module._parse_system_ping("127.0.0.1", WINDOWS_OUTPUT, PingOptions(count=2))
        assert result.success_count == 2
        assert all(reply.rtt_ms is not None for reply in result.replies)
        assert all(0.0 <= reply.rtt_ms <= 1.0 for reply in result.replies)  # ty: ignore[unsupported-operator]

    def test_missing_replies_become_timeouts(self) -> None:
        output = "64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=5 ms\n"
        result = ping_module._parse_system_ping("1.1.1.1", output, PingOptions(count=3))
        assert result.replies[0].status is PingStatus.SUCCESS
        assert result.replies[1].status is PingStatus.TIMEOUT
        assert result.replies[2].status is PingStatus.TIMEOUT

    def test_more_replies_than_count_are_truncated(self) -> None:
        output = "".join(f"x ttl=57 time={i} ms\n" for i in range(10))
        result = ping_module._parse_system_ping("1.1.1.1", output, PingOptions(count=2))
        assert result.total_count == 2


class TestSystemPingArgs:
    def test_target_is_last(self) -> None:
        args = ping_module._system_ping_args("1.1.1.1", PingOptions(count=3, timeout_s=2.0))
        assert args[-1] == "1.1.1.1"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows 专用参数")
    def test_windows_flags(self) -> None:
        args = ping_module._system_ping_args("1.1.1.1", PingOptions(count=3, timeout_s=2.0))
        assert args[:4] == ["-n", "3", "-w", "2000"]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX 专用参数")
    def test_posix_flags(self) -> None:
        args = ping_module._system_ping_args("1.1.1.1", PingOptions(count=3, timeout_s=2.0))
        assert args[:2] == ["-c", "3"]


# ---------------------------------------------------------------------------
# 后端选择与降级
# ---------------------------------------------------------------------------


class TestBackendSelection:
    def test_auto_order_prefers_raw_socket_then_system(self) -> None:
        order = ping_module._auto_order()
        assert order[0] is Backend.ICMPLIB
        assert order[-1] is Backend.SYSTEM
        if sys.platform == "win32":
            assert order == (Backend.ICMPLIB, Backend.WINDOWS_ICMP, Backend.SYSTEM)

    async def test_explicit_unavailable_backend_raises(self, monkeypatch: pytest.MonkeyPatch):
        def boom(_address: str, _options: PingOptions) -> Any:
            raise BackendUnavailable("模拟不可用")

        monkeypatch.setattr(ping_module, "_windows_icmp_ping", boom)
        with pytest.raises(BackendUnavailable):
            await ping_multiple("127.0.0.1", PingOptions(count=1), backend=Backend.WINDOWS_ICMP)

    async def test_auto_falls_back_to_next_backend(self, monkeypatch: pytest.MonkeyPatch):
        reset_backend_cache()
        calls: list[Backend] = []

        def unavailable(_address: str, _options: PingOptions) -> Any:
            calls.append(Backend.ICMPLIB)
            raise BackendUnavailable("模拟缺少权限")

        async def system_ping(address: str, _options: PingOptions) -> MultiPingResult:
            calls.append(Backend.SYSTEM)
            return ok_result(address)

        monkeypatch.setattr(ping_module, "_auto_order", lambda: (Backend.ICMPLIB, Backend.SYSTEM))
        monkeypatch.setattr(ping_module, "_icmplib_ping", unavailable)
        monkeypatch.setattr(ping_module, "_system_ping", system_ping)

        result = await ping_multiple("127.0.0.1", PingOptions(count=1))
        assert calls == [Backend.ICMPLIB, Backend.SYSTEM]
        assert result.success_count == 1
        reset_backend_cache()

    async def test_auto_all_backends_unavailable(self, monkeypatch: pytest.MonkeyPatch):
        reset_backend_cache()

        def unavailable(_address: str, _options: PingOptions) -> Any:
            raise BackendUnavailable("不可用")

        async def unavailable_async(_address: str, _options: PingOptions) -> MultiPingResult:
            raise BackendUnavailable("不可用")

        monkeypatch.setattr(ping_module, "_icmplib_ping", unavailable)
        monkeypatch.setattr(ping_module, "_system_ping", unavailable_async)
        monkeypatch.setattr(ping_module, "_windows_icmp_ping", unavailable)
        with pytest.raises(BackendUnavailable, match="没有可用的 Ping 后端"):
            await ping_multiple("127.0.0.1", PingOptions(count=1))
        reset_backend_cache()

    async def test_successful_backend_is_cached(self, monkeypatch: pytest.MonkeyPatch):
        reset_backend_cache()
        attempts: list[Backend] = []

        def icmplib(address: str, _options: PingOptions) -> MultiPingResult:
            attempts.append(Backend.ICMPLIB)
            return ok_result(address)

        monkeypatch.setattr(ping_module, "_icmplib_ping", icmplib)
        await ping_multiple("127.0.0.1", PingOptions(count=1))
        await ping_multiple("127.0.0.1", PingOptions(count=1))
        assert attempts == [Backend.ICMPLIB, Backend.ICMPLIB]
        reset_backend_cache()


class TestWindowsIcmpHelpers:
    def test_windows_backend_rejects_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ping_module.sys, "platform", "linux")
        with pytest.raises(BackendUnavailable, match="仅在 Windows 上可用"):
            ping_module._windows_icmp_ping("127.0.0.1", PingOptions(count=1))

    @pytest.mark.parametrize("size", [0, 8, 32, 56, 200])
    def test_payload_size_is_honoured(self, size: int) -> None:
        assert len(ping_module._build_payload(size)) == size

    def test_icmplib_rejects_invalid_address(self) -> None:
        with pytest.raises(BackendUnavailable, match="非法地址"):
            ping_module._icmplib_ping("not-an-ip", PingOptions(count=1))

    def test_ip_status_code_sets(self) -> None:
        assert ping_module._IP_REQ_TIMED_OUT == 11010
        assert 11002 in ping_module._IP_UNREACHABLE
        assert 11018 in ping_module._IP_UNREACHABLE
        assert 11013 in ping_module._IP_TTL_EXPIRED

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("127.0.0.1", 0x0100007F),
            ("192.0.2.1", 0x012810AC),
            ("10.150.0.1", 0x0100960A),
            ("223.5.5.5", 0x050505DF),
            ("0.0.0.0", 0x00000000),  # noqa: S104 - 只是地址编码测试向量, 不涉及绑定
            ("255.255.255.255", 0xFFFFFFFF),
        ],
    )
    def test_ipaddr_is_network_order(self, address: str, expected: int) -> None:
        """``IcmpSendEcho`` 的 ``DestinationAddress`` 要网络序 ``IPAddr``，不是主机序整数。"""
        assert ping_module._ipaddr_network_order(address) == expected

    def test_ipaddr_differs_from_host_order_int(self) -> None:
        """回归：旧实现用 ``int(IPv4Address(...))``，会把 192.0.2.1 实际打成 1.40.16.172。"""
        assert ping_module._ipaddr_network_order("192.0.2.1") != int(IPv4Address("192.0.2.1"))


class TestLoopbackReachable:
    async def test_loopback_always_reachable(self) -> None:
        """无论自动选中哪个后端，回环地址都必须可达。"""
        result = await ping_multiple("127.0.0.1", PingOptions(count=2, timeout_s=1.0))
        assert result.success_count == 2
        assert result.avg_rtt_ms >= 0


@pytest.mark.skipif(sys.platform != "win32", reason="IcmpSendEcho 仅在 Windows 上可用")
class TestWindowsIcmpBackend:
    """真机验证 ``IcmpSendEcho`` 路径。

    旧实现的地址字节序错误会让这两条用例失败：回环会打到 1.0.0.127（时延虚高）或直接超时，
    本机地址则稳定超时。
    """

    async def test_loopback_is_successful_and_fast(self) -> None:
        result = await ping_multiple(
            "127.0.0.1", PingOptions(count=2, timeout_s=1.0), backend=Backend.WINDOWS_ICMP
        )
        assert result.success_count == 2
        assert result.avg_rtt_ms < 5.0

    async def test_own_address_is_reachable(self) -> None:
        candidates = [
            address
            for item in list_interfaces()
            if item.is_up
            for address in item.ipv4
            if not address.startswith("169.254.")
        ]
        if not candidates:
            pytest.skip("本机没有可用的 IPv4 地址")
        result = await ping_multiple(
            candidates[0], PingOptions(count=1, timeout_s=1.0), backend=Backend.WINDOWS_ICMP
        )
        assert result.success_count == 1


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class TestHttpResponse:
    def test_ok_flag(self) -> None:
        assert HttpResponse("u", 200, {}, "", 0.1).ok
        assert not HttpResponse("u", 0, {}, "", 0.1, error="boom").ok

    def test_no_content(self) -> None:
        assert HttpResponse("u", 204, {}, "", 0.1).is_no_content
        assert not HttpResponse("u", 200, {}, "", 0.1).is_no_content

    @pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
    def test_redirect_codes(self, code: int) -> None:
        assert HttpResponse("u", code, {}, "", 0.1).is_redirect

    def test_describe(self) -> None:
        assert "HTTP 204" in HttpResponse("http://x", 204, {}, "", 0.1).describe()
        assert "失败" in HttpResponse("http://x", 0, {}, "", 0.1, error="e").describe()


class TestHttpClient:
    async def test_network_error_is_captured(self) -> None:
        async with HttpClient(timeout_s=0.5) as client:
            response = await client.get("http://127.0.0.1:1/never")
        assert not response.ok
        assert response.status_code == 0
        assert response.error is not None
        assert "失败" in response.describe()


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------


class TestDnsResult:
    def test_ok_flag(self) -> None:
        assert DnsResult(domain="a", server="b", addresses=["1.1.1.1"]).ok
        assert not DnsResult(domain="a", server="b").ok
        assert not DnsResult(domain="a", server="b", error="x").ok

    def test_contains(self) -> None:
        result = DnsResult(domain="a", server="b", addresses=["1.1.1.1"])
        assert result.contains("1.1.1.1")
        assert result.contains(IPv4Address("1.1.1.1"))
        assert not result.contains("2.2.2.2")

    def test_describe(self) -> None:
        assert "失败" in DnsResult(domain="a", server="b", error="x").describe()
        assert "1 条" in DnsResult(domain="a", server="b", addresses=["1.1.1.1"]).describe()


class TestQueryA:
    """用替换掉底层解析器的方式确定性地覆盖各条分支。"""

    async def test_successful_query(self, fake_dns: type[_FakeResolver]) -> None:
        result = await query_a("portal.hdu.edu.cn", "210.32.32.1")
        assert result.ok
        assert result.addresses == ["192.168.112.97", "10.0.0.9"]
        assert result.contains("192.168.112.97")
        assert result.answer_lines[0].startswith("portal.hdu.edu.cn. 60 IN A")

    async def test_nxdomain(self, fake_dns: type[_FakeResolver]) -> None:
        fake_dns.error = dns.resolver.NXDOMAIN()
        result = await query_a("portal.hdu.edu.cn", "210.32.32.1")
        assert "不存在" in (result.error or "")

    async def test_no_answer(self, fake_dns: type[_FakeResolver]) -> None:
        fake_dns.error = dns.resolver.NoAnswer()
        result = await query_a("portal.hdu.edu.cn", "210.32.32.1")
        assert "没有 A 记录" in (result.error or "")

    async def test_no_nameservers(self, fake_dns: type[_FakeResolver]) -> None:
        fake_dns.error = dns.resolver.NoNameservers()
        result = await query_a("portal.hdu.edu.cn", "210.32.32.1")
        assert "没有可用的 DNS 服务器" in (result.error or "")

    async def test_timeout(self, fake_dns: type[_FakeResolver]) -> None:
        fake_dns.error = dns.exception.Timeout()
        result = await query_a("portal.hdu.edu.cn", "210.32.32.1", timeout_s=1.0)
        assert "超时" in (result.error or "")

    async def test_generic_dns_error(self, fake_dns: type[_FakeResolver]) -> None:
        fake_dns.error = dns.exception.DNSException("其他问题")
        result = await query_a("portal.hdu.edu.cn", "210.32.32.1")
        assert result.error is not None
        assert not result.ok

    async def test_empty_answer_section_is_an_error(
        self,
        fake_dns: type[_FakeResolver],
    ) -> None:
        fake_dns.addresses = []
        result = await query_a("portal.hdu.edu.cn", "210.32.32.1")
        assert not result.ok
        assert "没有 A 记录" in (result.error or "")


# ---------------------------------------------------------------------------
# 网卡枚举与接口模型
# ---------------------------------------------------------------------------


class TestInterfaceInfo:
    def test_network_detection(self) -> None:
        info = InterfaceInfo(
            name="以太网",
            description="Realtek",
            kind=InterfaceKind.ETHERNET,
            is_up=True,
            ipv4=("10.150.31.97", "169.254.1.1"),
        )
        assert info.network is not None
        assert info.network.value == "Dorm"

    def test_network_none_outside_campus(self) -> None:
        info = InterfaceInfo(
            name="x",
            description="y",
            kind=InterfaceKind.ETHERNET,
            is_up=True,
            ipv4=("192.0.2.20",),
        )
        assert info.network is None

    def test_invalid_addresses_are_skipped(self) -> None:
        info = InterfaceInfo(
            name="x",
            description="y",
            kind=InterfaceKind.ETHERNET,
            is_up=True,
            ipv4=("not-an-ip", "10.249.0.5"),
        )
        assert len(info.ipv4_addresses) == 1
        assert info.network is not None

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            (InterfaceKind.TUNNEL, True),
            (InterfaceKind.PROP_VIRTUAL, True),
            (InterfaceKind.ETHERNET, False),
            (InterfaceKind.WIRELESS, False),
        ],
    )
    def test_is_vpn_like(self, kind: InterfaceKind, expected: bool) -> None:
        info = InterfaceInfo(name="x", description="y", kind=kind, is_up=True)
        assert info.is_vpn_like is expected

    def test_describe_matches_original_lines(self) -> None:
        info = InterfaceInfo(
            name="以太网",
            description="Realtek PCIe GbE Family Controller",
            kind=InterfaceKind.ETHERNET,
            is_up=True,
            ipv4=("192.0.2.20",),
            ipv6=("fe80::1",),
        )
        assert info.describe() == [
            "接口名称: 以太网",
            "描述: Realtek PCIe GbE Family Controller",
            "IPv4 地址: 192.0.2.20",
            "IPv6 地址: fe80::1",
        ]

    @pytest.mark.parametrize(
        ("kind", "label"),
        [
            (InterfaceKind.ETHERNET, "以太网"),
            (InterfaceKind.TUNNEL, "Tunnel"),
            (InterfaceKind.PROP_VIRTUAL, "propVirtual"),
        ],
    )
    def test_kind_labels(self, kind: InterfaceKind, label: str) -> None:
        assert kind.label == label


class TestListInterfaces:
    def test_returns_interface_snapshots(self) -> None:
        interfaces = list_interfaces()
        assert isinstance(interfaces, list)
        assert all(isinstance(item, InterfaceInfo) for item in interfaces)

    def test_loopback_excluded_by_default(self) -> None:
        assert all(item.kind is not InterfaceKind.LOOPBACK for item in list_interfaces())

    def test_sorted_by_name(self) -> None:
        names = [item.name for item in list_interfaces()]
        assert names == sorted(names)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("lo", InterfaceKind.LOOPBACK),
            ("loopback", InterfaceKind.LOOPBACK),
            ("utun3", InterfaceKind.TUNNEL),
            ("wg0", InterfaceKind.TUNNEL),
            ("Mihomo", InterfaceKind.TUNNEL),
            ("ppp0", InterfaceKind.PPP),
            ("wlan0", InterfaceKind.WIRELESS),
            ("eth0", InterfaceKind.ETHERNET),
            ("enp3s0", InterfaceKind.ETHERNET),
        ],
    )
    def test_guess_kind_heuristics(self, name: str, expected: InterfaceKind) -> None:
        assert _guess_kind(name, ["10.0.0.1"]) is expected


# ---------------------------------------------------------------------------
# Session 与配置
# ---------------------------------------------------------------------------


class TestRunSession:
    def test_ping_options_follow_config(self) -> None:
        session = RunSession(config=RunConfig(ping_count=3, ping_timeout_s=2.5))
        options = session.ping_options
        assert options.count == 3
        assert options.timeout_s == 2.5

    async def test_injected_http_is_not_closed(self) -> None:
        http = FakeHttp()
        session = RunSession(http=http)  # ty: ignore[invalid-argument-type]
        assert session.http is http
        await session.aclose()
        assert http.closed is False

    async def test_lazy_http_is_created_and_reused(self) -> None:
        session = RunSession()
        client = session.http
        assert isinstance(client, HttpClient)
        assert session.http is client
        await session.aclose()

    async def test_injected_pinger_is_used(self) -> None:
        calls: list[str] = []

        async def pinger(
            target: str,
            _options: PingOptions,
            *,
            backend: Backend,
        ) -> MultiPingResult:
            del backend
            calls.append(target)
            return ok_result(target)

        session = RunSession(pinger=pinger)
        result = await session.ping("10.0.0.1")
        assert calls == ["10.0.0.1"]
        assert result.success_count == 1


class TestRunConfig:
    def test_defaults(self) -> None:
        config = RunConfig()
        assert config.ping_count == 4
        assert config.verify_tls is True
        assert config.trust_env_proxy is True
        assert config.auth_host is None

    def test_zero_ping_count_rejected(self) -> None:
        with pytest.raises(ValueError, match="ping_count 必须"):
            RunConfig(ping_count=0)

    def test_non_positive_timeout_rejected(self) -> None:
        with pytest.raises(ValueError, match="ping_timeout_s 必须"):
            RunConfig(ping_timeout_s=0)
