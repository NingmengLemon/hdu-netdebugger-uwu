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


def make_path_result(
    *ttls: int | None,
    target: str = "223.5.5.5",
    source: str | None = None,
) -> MultiPingResult:
    """构造带 TTL / 来源的回复序列（用来测路径证据）。"""
    replies = tuple(
        PingReply(
            sequence=index + 1,
            status=PingStatus.SUCCESS if ttl is not None else PingStatus.TIMEOUT,
            rtt_ms=1.0 if ttl is not None else None,
            ttl=ttl,
            source=source or (target if ttl is not None else None),
        )
        for index, ttl in enumerate(ttls)
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

    def test_describe_appends_ttl_and_hop_estimate(self) -> None:
        """TTL 是本复刻额外补的路径证据（原版只打印状态与时间）。"""
        reply = PingReply(sequence=1, status=PingStatus.SUCCESS, rtt_ms=4.0, ttl=59)
        assert reply.describe("192.168.112.97").endswith("[TTL=59，约 5 跳]")
        assert reply.hops_estimate == 5

    def test_describe_mentions_an_unexpected_source(self) -> None:
        reply = PingReply(
            sequence=1,
            status=PingStatus.SUCCESS,
            rtt_ms=4.0,
            source="192.0.2.99",
        )
        assert "回复来自 192.0.2.99" in reply.describe("223.5.5.5")

    def test_describe_omits_the_source_when_it_matches(self) -> None:
        reply = PingReply(sequence=1, status=PingStatus.SUCCESS, rtt_ms=4.0, source="223.5.5.5")
        assert "回复来自" not in reply.describe("223.5.5.5")

    @pytest.mark.parametrize(
        ("ttl", "expected"),
        [
            (64, 0),
            (59, 5),
            (50, 14),
            (128, 0),
            (118, 10),
            (255, 0),
            (250, 5),
            (1, 63),
            (0, None),
            (256, None),
        ],
    )
    def test_hops_estimate(self, ttl: int, expected: int | None) -> None:
        reply = PingReply(sequence=1, status=PingStatus.SUCCESS, ttl=ttl)
        assert reply.hops_estimate == expected

    def test_hops_estimate_without_ttl(self) -> None:
        assert PingReply(sequence=1, status=PingStatus.SUCCESS).hops_estimate is None


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


class TestPathEvidence:
    """回复 TTL / 来源：判断"这次 ICMP 有没有被中间层接管"的少数可用证据。"""

    def test_reply_ttl_uses_the_most_common_value(self) -> None:
        assert make_path_result(59, 59, 58).reply_ttl == 59

    def test_reply_ttl_is_none_without_any_ttl(self) -> None:
        assert make_result(1.0, 2.0).reply_ttl is None
        assert make_result(1.0, 2.0).hops_estimate is None

    def test_hops_estimate_from_ttl(self) -> None:
        assert make_path_result(59, 59).hops_estimate == 5

    def test_no_unexpected_source_when_source_matches(self) -> None:
        assert make_path_result(59).unexpected_sources == ()

    def test_flags_a_reply_from_another_address(self) -> None:
        result = make_path_result(50, target="223.5.5.5", source="192.0.2.99")
        assert result.unexpected_sources == ("192.0.2.99",)

    def test_timeouts_do_not_contribute_a_source(self) -> None:
        assert make_path_result(None, 59).unexpected_sources == ()

    def test_detail_lines_add_a_path_evidence_line(self) -> None:
        lines = make_path_result(59).detail_lines()
        assert any("路径证据" in line and "约 5 跳" in line for line in lines)

    def test_detail_lines_omit_path_evidence_without_ttl(self) -> None:
        assert not any("路径证据" in line for line in make_result(1.0).detail_lines())
