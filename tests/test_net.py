"""网络能力层：Ping 后端选择、系统 ping 解析、HTTP 响应、DNS、网卡、session 与配置。"""

from __future__ import annotations

import asyncio
import ctypes
import locale
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver
import psutil
import pytest
from icmplib import ICMPRequest
from icmplib import exceptions as icmp_errors

from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.models.enums import PingStatus
from hdu_netdebugger.models.interface import InterfaceInfo, InterfaceKind
from hdu_netdebugger.models.ping import MultiPingResult, PingReply
from hdu_netdebugger.net import interfaces as interfaces_module
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
from hdu_netdebugger.net.routes import RouteInfo
from hdu_netdebugger.session import RunSession
from tests.fakes import FakeHttp, FakeRouter

if TYPE_CHECKING:
    from collections.abc import Iterator


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

    def test_invalid_hostname_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不依赖真实 DNS。

        ``.invalid`` 在正常解析器上必然失败，但在**劫持型 DNS**（本工具要诊断的
        场景之一）上可能被解析成功 —— 那样这条用例就会假失败。
        """

        def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[object]:
            raise socket.gaierror("Name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

        with pytest.raises(OSError):
            resolve_target("this-host-does-not-exist.invalid")

    def test_ipv6_only_hostname_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``proto=IPPROTO_ICMP`` 只返回 IPv4 记录，纯 AAAA 的域名要能退回查询。"""
        protos: list[int] = []

        def fake_getaddrinfo(
            _host: str,
            _port: object,
            *,
            proto: int = 0,
            **_kwargs: object,
        ) -> list[tuple[int, int, int, str, tuple[str, int, int, int]]]:
            protos.append(proto)
            if proto == socket.IPPROTO_ICMP:
                raise socket.gaierror("no IPv4 record")
            return [(socket.AF_INET6, socket.SOCK_DGRAM, 58, "", ("2001:db8::1", 0, 0, 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

        assert resolve_target("ipv6-only.example") == "2001:db8::1"
        assert protos == [socket.IPPROTO_ICMP, 0]


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

    def test_linux_flags_take_timeout_in_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux/GNU 的 ``-W`` 是**秒**；``-t`` 是 TTL。"""
        monkeypatch.setattr(ping_module.sys, "platform", "linux")
        options = PingOptions(count=3, timeout_s=2.0, payload_size=32, ttl=64, interval_s=0.5)
        assert ping_module._system_ping_args("1.1.1.1", options) == [
            "-c",
            "3",
            "-W",
            "2",
            "-s",
            "32",
            "-t",
            "64",
            "-i",
            "0.5",
            "1.1.1.1",
        ]

    def test_darwin_flags_take_timeout_in_milliseconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BSD/macOS 的 ``-W`` 是**毫秒**；``-m`` 是 TTL。

        这是最容易抄错的一处（毫秒/秒写反会让超时行为差 1000 倍），
        而手上没有 Mac，所以用改 ``sys.platform`` 的方式把参数拼装钉死。
        """
        monkeypatch.setattr(ping_module.sys, "platform", "darwin")
        options = PingOptions(count=3, timeout_s=2.0, payload_size=32, ttl=64, interval_s=0.5)
        assert ping_module._system_ping_args("1.1.1.1", options) == [
            "-c",
            "3",
            "-W",
            "2000",
            "-s",
            "32",
            "-m",
            "64",
            "-i",
            "0.5",
            "1.1.1.1",
        ]


class TestSystemPingProcess:
    """系统 ``ping`` 后端的进程管道：找不到可执行文件 / 超时 / 正常返回。"""

    async def test_missing_executable_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_exec(*_args: object, **_kwargs: object) -> object:
            raise FileNotFoundError("ping")

        monkeypatch.setattr(ping_module.asyncio, "create_subprocess_exec", fake_exec)

        with pytest.raises(BackendUnavailable, match="没有可用的 ping 命令"):
            await ping_module._system_ping("1.1.1.1", PingOptions(count=1))

    async def test_success_decodes_and_parses_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[tuple[str, ...]] = []

        class _Process:
            async def communicate(self) -> tuple[bytes, None]:
                return WINDOWS_OUTPUT.encode(), None

        async def fake_exec(*args: str, **_kwargs: object) -> _Process:
            seen.append(args)
            return _Process()

        monkeypatch.setattr(ping_module.asyncio, "create_subprocess_exec", fake_exec)

        result = await ping_module._system_ping("127.0.0.1", PingOptions(count=2))

        assert result.success_count == 2
        assert seen
        assert seen[0][0] == "ping"
        assert "127.0.0.1" in seen[0]

    async def test_timeout_kills_the_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        killed: list[bool] = []

        class _HangingProcess:
            async def communicate(self) -> tuple[bytes, None]:
                await asyncio.sleep(60)
                return b"", None

            def kill(self) -> None:
                killed.append(True)

            async def wait(self) -> int:
                return 0

        async def fake_exec(*_args: object, **_kwargs: object) -> _HangingProcess:
            return _HangingProcess()

        real_wait_for = asyncio.wait_for

        async def capped(awaitable: Any, timeout: float) -> Any:  # noqa: ASYNC109 - 包装 wait_for
            # 真实预算是 timeout_s * count + 5s，对单测太长。这里只把等待时间压到 50ms，
            # 被测的仍然是"超时 → kill() → wait()"这条分支本身。
            del timeout
            return await real_wait_for(awaitable, 0.05)

        monkeypatch.setattr(ping_module.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(ping_module.asyncio, "wait_for", capped)

        with pytest.raises(BackendUnavailable, match="系统 ping 命令超时"):
            await ping_module._system_ping("1.1.1.1", PingOptions(count=1))

        assert killed == [True]


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
            ("192.0.2.1", 0x010200C0),
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
        """回归：旧实现用 ``int(IPv4Address(...))``，会把 192.0.2.1 实际打成 1.2.0.192。"""
        assert ping_module._ipaddr_network_order("192.0.2.1") != int(IPv4Address("192.0.2.1"))

    def test_ipv6_is_rejected_by_ipaddr_helper(self) -> None:
        with pytest.raises(ValueError):
            ping_module._ipaddr_network_order("2001:db8::1")

    @pytest.mark.skipif(sys.platform != "win32", reason="IcmpSendEcho 仅在 Windows 上可用")
    def test_ipv6_target_degrades_to_backend_unavailable(self) -> None:
        """IcmpSendEcho 只有 IPv4 版本；要抛 BackendUnavailable 才能让 AUTO 降级。"""
        with pytest.raises(BackendUnavailable, match="只支持 IPv4"):
            ping_module._windows_icmp_ping("2001:db8::1", PingOptions(count=1))


class TestConsoleOutputDecoding:
    """系统 ``ping`` 输出的解码。

    Windows 写的是 OEM 代码页（中文系统通常是 936），不是 UTF-8；即便开了
    ``PYTHONUTF8`` 也一样。这里用"按被测编码往返"的方式把选择钉住：如果实现改成
    硬用 UTF-8，中文就解不出来，用例会挂。
    """

    def test_round_trip_keeps_chinese_text(self) -> None:
        text = "来自 10.150.0.1 的回复: 字节=32 时间=2ms TTL=64"
        encoding = (
            "oem" if sys.platform == "win32" else locale.getpreferredencoding(do_setlocale=False)
        )

        assert ping_module._decode_console_output(text.encode(encoding)) == text

    def test_undecodable_bytes_do_not_raise(self) -> None:
        assert isinstance(ping_module._decode_console_output(b"\xff\xfe\x00abc"), str)


class _WindowsReplyStub:
    """替代 ``ICMP_ECHO_REPLY``：``_classify_windows_reply`` 只用到这两个字段。"""

    Status = 0
    RoundTripTime = 0

    @classmethod
    def from_buffer(cls, _buffer: object) -> type[_WindowsReplyStub]:
        return cls

    @classmethod
    def with_values(cls, status: int, rtt: int = 0) -> type[_WindowsReplyStub]:
        return type("_WindowsReplyStub", (cls,), {"Status": status, "RoundTripTime": rtt})


class TestWindowsReplyClassification:
    """``IcmpSendEcho`` 的返回值 → :class:`PingReply`。

    诊断结论（超时 / 不可达 / 出错）全靠这条映射，而它此前完全没有测试 ——
    H1 那个"地址字节序反了"的缺陷之所以能活下来，就是因为这里没闸门。
    """

    def test_success_carries_rtt_and_sequence(self) -> None:
        reply = ping_module._classify_windows_reply(1, b"", _WindowsReplyStub.with_values(0, 7), 3)
        assert reply.status is PingStatus.SUCCESS
        assert reply.rtt_ms == 7.0
        assert reply.sequence == 3
        assert reply.ok is True

    def test_status_timeout(self) -> None:
        reply = ping_module._classify_windows_reply(
            1, b"", _WindowsReplyStub.with_values(ping_module._IP_REQ_TIMED_OUT), 1
        )
        assert reply.status is PingStatus.TIMEOUT
        assert reply.rtt_ms is None

    @pytest.mark.parametrize("status", [11002, 11003, 11004, 11005, 11018, 11013, 11014])
    def test_unreachable_and_ttl_expired(self, status: int) -> None:
        reply = ping_module._classify_windows_reply(
            1, b"", _WindowsReplyStub.with_values(status), 1
        )
        assert reply.status is PingStatus.UNREACHABLE
        assert f"IP_STATUS={status}" in (reply.error or "")

    def test_unknown_status_is_error(self) -> None:
        reply = ping_module._classify_windows_reply(1, b"", _WindowsReplyStub.with_values(12345), 1)
        assert reply.status is PingStatus.ERROR

    def test_sent_zero_with_timeout_error_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ctypes, "get_last_error", lambda: ping_module._IP_REQ_TIMED_OUT)
        reply = ping_module._classify_windows_reply(0, b"", _WindowsReplyStub, 1)
        assert reply.status is PingStatus.TIMEOUT

    def test_sent_zero_with_other_error_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 87)
        reply = ping_module._classify_windows_reply(0, b"", _WindowsReplyStub, 1)
        assert reply.status is PingStatus.ERROR
        assert "87" in (reply.error or "")


class _IcmpRequestStub:
    def __init__(self, sent_at: float) -> None:
        self.time = sent_at


class _IcmpReplyStub:
    def __init__(self, replied_at: float, error: BaseException | None = None) -> None:
        self.time = replied_at
        self.error = error

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error


class _IcmpSocketStub:
    """只实现 icmplib 套接字协议里那几个方法的替身。"""

    def __init__(
        self,
        *,
        reply: object = None,
        receive_error: BaseException | None = None,
    ) -> None:
        self.reply = reply
        self.receive_error = receive_error
        self.sent = 0

    def send(self, request: ICMPRequest) -> None:
        # 参数名必须与 icmplib 的套接字协议一致（ty 会按协议校验关键字兼容性）
        del request
        self.sent += 1

    def receive(self, request: ICMPRequest, timeout: float) -> object:
        del request, timeout
        if self.receive_error is not None:
            raise self.receive_error
        return self.reply

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return


class _ErrorCodeStub:
    """``DestinationUnreachable`` / ``TimeExceeded`` 需要 ``reply.code``。"""

    code = 1


class TestIcmplibReplyClassification:
    """icmplib 的异常 → :class:`PingReply` 的映射。"""

    def _send(
        self,
        sock: _IcmpSocketStub,
        *,
        sent_at: float = 1.0,
        sequence: int = 1,
    ) -> PingReply:
        return ping_module._icmplib_send_one(
            sock,
            cast("ICMPRequest", _IcmpRequestStub(sent_at)),
            1.0,
            sequence,
        )

    def test_success_computes_rtt(self) -> None:
        sock = _IcmpSocketStub(reply=_IcmpReplyStub(replied_at=1.05))

        reply = self._send(sock, sequence=2)

        assert reply.status is PingStatus.SUCCESS
        assert reply.rtt_ms == pytest.approx(50.0)
        assert reply.sequence == 2
        assert sock.sent == 1

    def test_negative_elapsed_is_clamped_to_zero(self) -> None:
        sock = _IcmpSocketStub(reply=_IcmpReplyStub(replied_at=0.5))
        assert self._send(sock).rtt_ms == 0.0

    def test_timeout_maps_to_timeout(self) -> None:
        sock = _IcmpSocketStub(receive_error=icmp_errors.TimeoutExceeded(1.0))
        assert self._send(sock).status is PingStatus.TIMEOUT

    def test_destination_unreachable_maps_to_unreachable(self) -> None:
        sock = _IcmpSocketStub(
            reply=_IcmpReplyStub(2.0, icmp_errors.DestinationUnreachable(_ErrorCodeStub()))
        )

        reply = self._send(sock)

        assert reply.status is PingStatus.UNREACHABLE
        assert "unreachable" in (reply.error or "")

    def test_time_exceeded_maps_to_unreachable(self) -> None:
        sock = _IcmpSocketStub(
            reply=_IcmpReplyStub(2.0, icmp_errors.TimeExceeded(_ErrorCodeStub()))
        )
        assert self._send(sock).status is PingStatus.UNREACHABLE

    def test_other_icmp_error_maps_to_error(self) -> None:
        sock = _IcmpSocketStub(reply=_IcmpReplyStub(2.0, icmp_errors.ICMPLibError("boom")))

        reply = self._send(sock)

        assert reply.status is PingStatus.ERROR
        assert "boom" in (reply.error or "")


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


@pytest.fixture
def local_http_server() -> Iterator[str]:
    """只监听回环的极简 HTTP 服务，让 httpx 的成功路径真的跑一遍。"""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/no-content":
                self.send_response(204)
                self.end_headers()
                return
            if self.path == "/redirect":
                self.send_response(302)
                # Location 里刻意塞方括号：它会一路变成检查项警告，用来盯住渲染转义
                self.send_header("Location", "http://portal.example/[red]login")
                self.end_headers()
                return
            body = "hello 中文".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("X-Probe", "yes")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - 基类签名
            """静音访问日志。"""
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class TestHttpClient:
    async def test_network_error_is_captured(self) -> None:
        async with HttpClient(timeout_s=0.5) as client:
            response = await client.get("http://127.0.0.1:1/never")
        assert not response.ok
        assert response.status_code == 0
        assert response.error is not None
        assert "失败" in response.describe()

    async def test_success_returns_status_headers_and_text(
        self,
        local_http_server: str,
    ) -> None:
        """成功路径此前**从未被执行过**（所有检查项都注入 FakeHttp）。"""
        async with HttpClient(timeout_s=5.0, trust_env_proxy=False) as client:
            response = await client.get(f"{local_http_server}/")

        assert response.ok
        assert response.status_code == 200
        assert response.text == "hello 中文"
        assert response.headers["x-probe"] == "yes"
        assert response.elapsed_s >= 0.0
        assert response.describe() == f"请求 {local_http_server}/ 返回 HTTP 200"

    async def test_no_content_and_redirect_flags(self, local_http_server: str) -> None:
        async with HttpClient(timeout_s=5.0, trust_env_proxy=False) as client:
            no_content = await client.get(f"{local_http_server}/no-content")
            redirect = await client.get(f"{local_http_server}/redirect")

        assert no_content.is_no_content
        assert no_content.status_code == 204
        assert redirect.is_redirect
        assert redirect.headers["location"] == "http://portal.example/[red]login"


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
            ipv4=("10.150.0.100", "169.254.1.1"),
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

    def test_filters_loopback_and_sorts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """用固定样本验证过滤与排序。

        上面三条断言针对真实网卡，对空列表恒真 —— 这里补一条不依赖环境的。
        """
        sample = [
            InterfaceInfo(
                name="b-if",
                description="",
                kind=InterfaceKind.ETHERNET,
                is_up=True,
                ipv4=("10.0.0.2",),
            ),
            InterfaceInfo(
                name="a-lo",
                description="",
                kind=InterfaceKind.LOOPBACK,
                is_up=True,
                ipv4=("127.0.0.1",),
            ),
            InterfaceInfo(name="c-tun", description="", kind=InterfaceKind.TUNNEL, is_up=True),
        ]
        monkeypatch.setattr(interfaces_module, "_windows_interfaces", lambda: sample)
        monkeypatch.setattr(interfaces_module, "_psutil_interfaces", lambda: sample)

        assert [item.name for item in list_interfaces()] == ["b-if", "c-tun"]
        assert [item.name for item in list_interfaces(include_loopback=True)] == [
            "a-lo",
            "b-if",
            "c-tun",
        ]

    def test_psutil_fallback_maps_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``psutil`` 回退分支（非 Windows 平台的唯一路径）此前完全没有测试。"""

        def addr(family: int, address: str) -> object:
            return type("_Addr", (), {"family": family, "address": address})()

        def stat(*, isup: bool, mtu: int, speed: int) -> object:
            return type("_Stat", (), {"isup": isup, "mtu": mtu, "speed": speed})()

        monkeypatch.setattr(
            psutil,
            "net_if_addrs",
            lambda: {
                "enp3s0": [
                    addr(socket.AF_INET, "10.0.0.5"),
                    addr(socket.AF_INET6, "fe80::1%enp3s0"),
                ],
                "wlan0": [addr(socket.AF_INET, "10.0.0.9")],
            },
        )
        monkeypatch.setattr(
            psutil,
            "net_if_stats",
            lambda: {
                "enp3s0": stat(isup=True, mtu=1500, speed=1000),
                "wlan0": stat(isup=False, mtu=1500, speed=0),
            },
        )

        found = {item.name: item for item in interfaces_module._psutil_interfaces()}

        wired = found["enp3s0"]
        assert wired.kind is InterfaceKind.ETHERNET
        assert wired.ipv4 == ("10.0.0.5",)
        assert wired.ipv6 == ("fe80::1",), "IPv6 的 scope id 应当被剥掉"
        assert wired.mtu == 1500
        assert wired.speed_mbps == 1000
        assert wired.is_up is True

        wireless = found["wlan0"]
        assert wireless.kind is InterfaceKind.WIRELESS
        assert wireless.is_up is False
        assert wireless.speed_mbps is None

    @pytest.mark.parametrize(
        ("bits_per_second", "expected"),
        [
            (0, None),
            (-1, None),
            (100_000, 1),
            (1_000_000, 1),
            (1_000_000_000, 1000),
            (10_000_000_000, 10_000),
            # Windows 用哨兵值表示"速率未知"，换算出来会是 1.8e13 Mbps 这种垃圾数字
            (2**64 - 1, None),
            (1 << 40, None),
        ],
    )
    def test_link_speed_conversion(self, bits_per_second: int, expected: int | None) -> None:
        assert interfaces_module._link_speed_mbps(bits_per_second) == expected

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

    def test_egress_resolves_the_interface_by_index(self) -> None:
        session = RunSession(
            router=FakeRouter({"10.150.0.1": RouteInfo(interface_index=75, metric=0)})
        )
        session._interfaces = [
            InterfaceInfo(
                name="Mihomo",
                description="Meta Tunnel",
                kind=InterfaceKind.PROP_VIRTUAL,
                is_up=True,
                index=75,
            )
        ]

        egress = session.egress("10.150.0.1")

        assert egress.interface is not None
        assert egress.interface.name == "Mihomo"
        assert egress.is_tunnel_like is True
        assert "Mihomo" in egress.describe()

    def test_egress_is_unknown_without_a_route(self) -> None:
        session = RunSession(router=FakeRouter())

        egress = session.egress("10.150.0.1")

        assert egress.route is None
        assert egress.interface is None
        assert egress.describe() == "出站: (未知)"

    def test_egress_degrades_when_the_router_raises(self) -> None:
        """路由查询失败只是"这项证据拿不到"，不该让整个检查项崩掉。"""

        def boom(_target: str) -> RouteInfo | None:
            raise OSError("读不到路由表")

        session = RunSession(router=boom)

        assert session.egress("10.150.0.1").describe() == "出站: (未知)"


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
