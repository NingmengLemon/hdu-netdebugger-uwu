"""逐跳路径解析与探测.

解析样本取自**真实输出**（本机 ``tracert``），但地址已替换成文档段
（RFC 5737 / RFC 3849），不把任何本机地址写进测试。
"""

from __future__ import annotations

import sys

import pytest

from hdu_netdebugger.net import trace as trace_module
from hdu_netdebugger.net.trace import TraceHop, parse_trace

#: Windows 英文输出（把真实输出里的地址换成了文档段）
WINDOWS_TRACE = (
    "\n"
    "Tracing route to 203.0.113.5 over a maximum of 8 hops\n"
    "\n"
    "  1    <1 ms    <1 ms    <1 ms  192.0.2.1 \n"
    "  2     1 ms     1 ms     1 ms  198.51.100.1 \n"
    "  3     *        *        *     Request timed out.\n"
    "  4     *        *        *     Request timed out.\n"
    "  5     2 ms     2 ms     2 ms  203.0.113.5 \n"
    "\n"
    "Trace complete.\n"
)

#: Windows 中文输出（超时那一行换了措辞，地址列序相同）
CHINESE_TRACE = (
    "  1    <1 ms    <1 ms    <1 ms  192.0.2.1 \n"
    "  2     *        *        *     请求超时。\n"
    "  3     3 ms     3 ms     3 ms  203.0.113.5 \n"
)

#: Linux / macOS 输出：**地址在最前面**，RTT 在后
LINUX_TRACE = (
    "traceroute to 203.0.113.5 (203.0.113.5), 8 hops max, 60 byte packets\n"
    " 1  192.0.2.1  0.123 ms  0.100 ms  0.090 ms\n"
    " 2  * * *\n"
    " 3  203.0.113.5  3.210 ms  3.100 ms  3.050 ms\n"
)

#: 沿途谁都不回 TTL 超时
SILENT_TRACE = (
    "  1     *        *        *     Request timed out.\n"
    "  2     *        *        *     Request timed out.\n"
)


class TestParseTrace:
    def test_windows_output(self) -> None:
        result = parse_trace("203.0.113.5", WINDOWS_TRACE)

        assert [hop.ttl for hop in result.hops] == [1, 2, 3, 4, 5]
        assert result.hops[0].address == "192.0.2.1"
        assert result.hops[0].rtt_ms == 1.0  # `<1 ms` 按 1ms 计
        assert result.hops[2].answered is False
        assert result.reached is True
        assert result.hop_count == 5

    def test_chinese_output(self) -> None:
        result = parse_trace("203.0.113.5", CHINESE_TRACE)

        assert result.hops[1].address is None
        assert result.reached is True
        assert result.hop_count == 3

    def test_linux_output_has_address_first(self) -> None:
        """Linux 的列序与 Windows 相反，所以地址不能按列号取。"""
        result = parse_trace("203.0.113.5", LINUX_TRACE)

        assert result.hops[0].address == "192.0.2.1"
        assert result.hops[0].rtt_ms == pytest.approx(0.123)
        assert result.hops[1].address is None
        assert result.reached is True

    def test_silent_path_has_no_evidence(self) -> None:
        result = parse_trace("203.0.113.5", SILENT_TRACE)

        assert result.answered_hops == ()
        assert result.last_answering is None
        assert result.reached is False
        assert "跳数不可得" in result.describe()

    def test_unreached_target_reports_last_answering_hop(self) -> None:
        output = (
            "  1    <1 ms    <1 ms    <1 ms  192.0.2.1 \n"
            "  2     *        *        *     Request timed out.\n"
        )
        result = parse_trace("203.0.113.5", output)

        assert result.last_answering == TraceHop(ttl=1, address="192.0.2.1", rtt_ms=1.0)
        assert "第 1 跳（192.0.2.1）为止还有应答" in result.describe()

    def test_headers_are_not_hops(self) -> None:
        """表头 / 统计行里也有数字，不能当成跳。"""
        result = parse_trace("203.0.113.5", WINDOWS_TRACE)
        assert len(result.hops) == 5

    def test_ipv6_hop_is_parsed(self) -> None:
        output = "  1  2001:db8::1  1.000 ms\n  2  2001:db8::2  2.000 ms\n"
        result = parse_trace("2001:db8::2", output)

        assert result.hops[0].address == "2001:db8::1"
        assert result.reached is True


class TestTraceArgs:
    def test_windows_uses_tracert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        command, args = trace_module._trace_args("203.0.113.5", 8)

        assert command == "tracert"
        assert "-d" in args
        assert args[-1] == "203.0.113.5"

    def test_posix_sends_one_probe_per_hop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux 的 traceroute 默认每跳发 3 个探针，太慢；显式压到 1 个。"""
        monkeypatch.setattr(sys, "platform", "linux")
        command, args = trace_module._trace_args("203.0.113.5", 8)

        assert command == "traceroute"
        assert args[args.index("-q") + 1] == "1"
