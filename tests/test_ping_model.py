"""Ping 结果模型。"""

from __future__ import annotations

import pytest

from hdu_netdebugger.models.enums import PingStatus
from hdu_netdebugger.models.ping import MultiPingResult, PingReply


def make_result(*rtts: float | None, target: str = "10.0.0.1") -> MultiPingResult:
    """按给定的 RTT 序列（``None`` 表示超时）构造结果。"""
    replies = tuple(
        PingReply(
            sequence=index + 1,
            status=PingStatus.SUCCESS if rtt is not None else PingStatus.TIMEOUT,
            rtt_ms=rtt,
        )
        for index, rtt in enumerate(rtts)
    )
    return MultiPingResult(target=target, replies=replies)


class TestPingReply:
    def test_ok_only_for_success(self) -> None:
        assert PingReply(sequence=1, status=PingStatus.SUCCESS, rtt_ms=1.0).ok
        assert not PingReply(sequence=1, status=PingStatus.TIMEOUT).ok
        assert not PingReply(sequence=1, status=PingStatus.ERROR, error="x").ok

    def test_describe_formats_like_original(self) -> None:
        reply = PingReply(sequence=2, status=PingStatus.SUCCESS, rtt_ms=12.7)
        assert reply.describe("1.1.1.1") == "Ping 1.1.1.1 第 2 次: 状态:成功, 时间:13ms"

    def test_describe_includes_error_detail(self) -> None:
        reply = PingReply(sequence=1, status=PingStatus.ERROR, error="IP_STATUS=11050")
        assert "IP_STATUS=11050" in reply.describe("1.1.1.1")
        assert "时间:0ms" in reply.describe("1.1.1.1")


class TestMultiPingResult:
    def test_all_succeeded(self) -> None:
        assert make_result(1.0, 2.0).all_succeeded
        assert not make_result(1.0, None).all_succeeded

    def test_empty_result_is_not_success(self) -> None:
        empty = MultiPingResult(target="x", replies=())
        assert not empty.all_succeeded
        assert empty.success_count == 0
        assert empty.total_count == 0
        assert empty.packet_loss_rate == 0.0
        assert empty.avg_rtt_ms == 0.0

    def test_statistics(self) -> None:
        result = make_result(1.0, None, 3.0, 5.0)
        assert result.success_count == 3
        assert result.total_count == 4
        assert result.avg_rtt_ms == pytest.approx(3.0)
        assert result.min_rtt_ms == 1.0
        assert result.max_rtt_ms == 5.0
        assert result.packet_loss_rate == pytest.approx(0.25)
        assert result.packet_loss_percent == pytest.approx(25.0)

    def test_full_loss(self) -> None:
        result = make_result(None, None)
        assert result.success_count == 0
        assert result.packet_loss_rate == 1.0
        assert result.avg_rtt_ms == 0.0
        assert result.min_rtt_ms == 0.0
        assert result.max_rtt_ms == 0.0

    def test_detail_lines_match_original_shape(self) -> None:
        lines = make_result(3.0, None).detail_lines()
        assert lines[0] == "开始对 10.0.0.1 进行 2 次 Ping 测试..."
        assert lines[1].startswith("Ping 10.0.0.1 第 1 次: 状态:成功")
        assert lines[2].startswith("Ping 10.0.0.1 第 2 次: 状态:超时")
        assert lines[3] == ("Ping 测试完成: 成功次数:1/2, 平均时间:3ms, 最小时间:3ms, 最大时间:3ms")
