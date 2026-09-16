"""路径 MTU 探测的算法.

判据来自实测（本机，宿舍路由器 + 寝室网上游，TUN 关闭）：到深澜认证服务器
**996 字节以上全丢且从不回 ICMP 需要分片**，而 BAS 与公网都能过 1472。
"""

from __future__ import annotations

import pytest

from hdu_netdebugger.models.enums import PingStatus
from hdu_netdebugger.models.ping import MultiPingResult, PingReply
from hdu_netdebugger.net.mtu import (
    MAX_PROBE_PAYLOAD,
    MIN_PROBE_PAYLOAD,
    probe_path_mtu,
)


def result_for(size: int, *, max_payload: int, frag_needed: bool = False) -> MultiPingResult:
    """构造"边界为 ``max_payload``"的探测结果。"""
    alive = size <= max_payload
    if alive:
        status, error = PingStatus.SUCCESS, None
    elif frag_needed:
        status, error = PingStatus.UNREACHABLE, "ICMP 需要分片（包超过路径 MTU）"
    else:
        status, error = PingStatus.TIMEOUT, None
    return MultiPingResult(
        target="192.0.2.1",
        replies=(PingReply(sequence=1, status=status, rtt_ms=1.0 if alive else None, error=error),),
    )


class ProbeRecorder:
    """记录被探测过的尺寸，方便断言"快筛命中就不再二分"。"""

    def __init__(self, *, max_payload: int, frag_needed: bool = False) -> None:
        self.max_payload = max_payload
        self.frag_needed = frag_needed
        self.sizes: list[int] = []

    async def __call__(self, size: int) -> MultiPingResult:
        self.sizes.append(size)
        return result_for(size, max_payload=self.max_payload, frag_needed=self.frag_needed)


class TestProbePathMtu:
    async def test_healthy_path_stops_after_two_probes(self) -> None:
        """健康网络只花两次探测（最小载荷 + 1472 快筛），不该做二分。"""
        probe = ProbeRecorder(max_payload=MAX_PROBE_PAYLOAD)

        result = await probe_path_mtu("192.0.2.1", probe)

        assert probe.sizes == [MIN_PROBE_PAYLOAD, MAX_PROBE_PAYLOAD]
        assert result.max_payload == MAX_PROBE_PAYLOAD
        assert result.limited is False
        assert "路径 MTU 未见受限" in result.describe()

    async def test_unreachable_target_is_not_measured(self) -> None:
        """连最小载荷都不通时不该继续二分 —— 那不是 MTU 问题。"""
        probe = ProbeRecorder(max_payload=0)

        result = await probe_path_mtu("192.0.2.1", probe)

        # 基线探测会重试一次（单包丢包不足以判定不可达），之后不再二分
        assert probe.sizes == [MIN_PROBE_PAYLOAD, MIN_PROBE_PAYLOAD]
        assert result.reachable is False
        assert result.max_payload is None
        assert result.path_mtu is None
        assert result.limited is False
        assert "目标不可达" in result.describe()

    async def test_baseline_loss_is_retried_before_giving_up(self) -> None:
        """第一次基线探测丢包、第二次成功时，应当继续测量而不是报"不可达"。"""

        class FlakyProbe:
            def __init__(self) -> None:
                self.calls = 0
                self.sizes: list[int] = []

            async def __call__(self, size: int) -> MultiPingResult:
                self.sizes.append(size)
                self.calls += 1
                if self.calls == 1:
                    return result_for(MIN_PROBE_PAYLOAD, max_payload=0)  # 首个包丢失
                return result_for(size, max_payload=MAX_PROBE_PAYLOAD)

        probe = FlakyProbe()

        result = await probe_path_mtu("192.0.2.1", probe)

        assert result.reachable is True
        assert result.limited is False
        assert probe.sizes[0] == probe.sizes[1] == MIN_PROBE_PAYLOAD

    @pytest.mark.parametrize("edge", [996, 1200, 1400])
    async def test_binary_search_finds_the_edge(self, edge: int) -> None:
        probe = ProbeRecorder(max_payload=edge)

        result = await probe_path_mtu("192.0.2.1", probe)

        assert result.max_payload == edge
        assert result.path_mtu == edge + 28
        assert result.limited is True
        # 二分：32..1472 的宽度下 2 次起点 + 约 11 次收缩即收敛，远少于逐个试
        assert len(probe.sizes) <= 14

    async def test_silent_drop_is_reported_as_blackhole(self) -> None:
        """有上限但从不回"需要分片" —— 这是 MTU 黑洞形态，处置与"只是 MTU 偏小"不同。"""
        probe = ProbeRecorder(max_payload=996, frag_needed=False)

        result = await probe_path_mtu("192.0.2.1", probe)

        assert result.silent_drop is True
        assert result.frag_needed_seen is False
        assert "没有 ICMP 需要分片" in result.describe()

    async def test_frag_needed_is_recorded(self) -> None:
        """中间设备回了"需要分片"：说明只是 MTU 偏小，不是黑洞。"""
        probe = ProbeRecorder(max_payload=1450, frag_needed=True)

        result = await probe_path_mtu("192.0.2.1", probe)

        assert result.frag_needed_seen is True
        assert result.silent_drop is False
        assert "收到过 ICMP 需要分片" in result.describe()

    async def test_probe_log_records_every_attempt(self) -> None:
        probe = ProbeRecorder(max_payload=996)

        result = await probe_path_mtu("192.0.2.1", probe)

        assert [item.size for item in result.probes] == probe.sizes
        assert result.probes[0].ok is True
        assert result.probes[-1].ok is False
        assert result.probes[0].describe() == "32 字节载荷: 通过"
