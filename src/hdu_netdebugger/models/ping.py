"""ICMP Ping 的结果模型。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Final

from hdu_netdebugger.models.enums import PingStatus

__all__ = ["MultiPingResult", "PingReply"]

#: 互联网上常见的初始 TTL 取值，用来从回复 TTL 反推跳数
_INITIAL_TTLS: Final = (64, 128, 255)


@dataclass(frozen=True, slots=True)
class PingReply:
    """单次 ICMP Echo 请求的结果。"""

    #: 序号，从 1 开始
    sequence: int
    status: PingStatus
    #: 往返时延（毫秒）；未成功时为 ``None``
    rtt_ms: float | None = None
    #: 失败原因（仅用于详细日志）
    error: str | None = None
    #: 回复报文的 TTL；后端拿不到时为 ``None``
    #:
    #: 实测意义：本机开着 TUN 时，``ping -i 1``（TTL=1）照样能收到回复，
    #: 但回复 TTL 与正常请求完全一致 —— TTL 是判断"这次 ICMP 有没有被本地
    #: 中间层接管"的少数可用证据之一。注意 **icmplib 后端拿不到 TTL**
    #: （它的 ``ICMPReply`` 不暴露该字段），只有 Windows ``IcmpSendEcho``
    #: 与系统 ``ping`` 命令的输出里有。
    ttl: int | None = None
    #: 回复报文的来源地址；未知时为 ``None``
    source: str | None = None

    @property
    def ok(self) -> bool:
        """本次请求是否成功收到回复。"""
        return self.status is PingStatus.SUCCESS

    @property
    def hops_estimate(self) -> int | None:
        """由回复 TTL 估算的跳数（估算，不是测量）。

        取"不小于 TTL 的最小常见初始值"减去 TTL：TTL=59 → 约 5 跳，TTL=50 → 约 14 跳。
        这只是个粗略下界 —— 中间设备可以改 TTL，也可以伪造回复。
        """
        if self.ttl is None or self.ttl <= 0:
            # TTL=0 的回复在链路上不可能存在（路由器会丢弃），当成"未知"
            return None
        for initial in _INITIAL_TTLS:
            if self.ttl <= initial:
                return initial - self.ttl
        return None

    def describe(self, target: str) -> str:
        """渲染成与原版一致的单行日志（TTL / 跳数是本复刻额外补的证据）。"""
        rtt = 0.0 if self.rtt_ms is None else self.rtt_ms
        suffix = f" ({self.error})" if self.error else ""
        extras: list[str] = []
        if self.ttl is not None:
            hops = self.hops_estimate
            extras.append(f"TTL={self.ttl}" + (f"，约 {hops} 跳" if hops is not None else ""))
        if self.source is not None and self.source != target:
            extras.append(f"回复来自 {self.source}")
        tail = f" [{'；'.join(extras)}]" if extras else ""
        return (
            f"Ping {target} 第 {self.sequence} 次: "
            f"状态:{self.status.label}, 时间:{rtt:.0f}ms{suffix}{tail}"
        )


@dataclass(frozen=True, slots=True)
class MultiPingResult:
    """对同一目标的多次 Ping 汇总。"""

    target: str
    replies: tuple[PingReply, ...]

    @property
    def total_count(self) -> int:
        """发出的请求总数。"""
        return len(self.replies)

    @property
    def success_count(self) -> int:
        """收到回复的请求数。"""
        return sum(1 for reply in self.replies if reply.ok)

    @property
    def all_succeeded(self) -> bool:
        """是否全部成功。"""
        return self.total_count > 0 and self.success_count == self.total_count

    @property
    def packet_loss_rate(self) -> float:
        """丢包率，取值 ``0.0`` ~ ``1.0``。"""
        if not self.replies:
            return 0.0
        return (self.total_count - self.success_count) / self.total_count

    @property
    def packet_loss_percent(self) -> float:
        """丢包率百分比（``0`` ~ ``100``）。"""
        return self.packet_loss_rate * 100

    @property
    def _rtts(self) -> list[float]:
        return [reply.rtt_ms for reply in self.replies if reply.rtt_ms is not None]

    @property
    def avg_rtt_ms(self) -> float:
        """平均往返时延（毫秒）；无成功回复时为 ``0``。"""
        rtts = self._rtts
        return sum(rtts) / len(rtts) if rtts else 0.0

    @property
    def min_rtt_ms(self) -> float:
        """最小往返时延（毫秒）；无成功回复时为 ``0``。"""
        rtts = self._rtts
        return min(rtts) if rtts else 0.0

    @property
    def max_rtt_ms(self) -> float:
        """最大往返时延（毫秒）；无成功回复时为 ``0``。"""
        rtts = self._rtts
        return max(rtts) if rtts else 0.0

    def detail_lines(self) -> list[str]:
        """渲染成与原版一致的详细日志行。"""
        lines = [f"开始对 {self.target} 进行 {self.total_count} 次 Ping 测试..."]
        lines.extend(reply.describe(self.target) for reply in self.replies)
        lines.append(
            f"Ping 测试完成: 成功次数:{self.success_count}/{self.total_count}, "
            f"平均时间:{self.avg_rtt_ms:.0f}ms, "
            f"最小时间:{self.min_rtt_ms:.0f}ms, "
            f"最大时间:{self.max_rtt_ms:.0f}ms"
        )
        hops = self.hops_estimate
        if hops is not None:
            ttl = self.reply_ttl
            lines.append(f"路径证据: 回复 TTL={ttl}，估计约 {hops} 跳（由 TTL 估算，非实际测量）")
        return lines

    @property
    def reply_ttl(self) -> int | None:
        """成功回复里出现最多的 TTL（多个包可能因路径变化而不同）。"""
        ttls = [reply.ttl for reply in self.replies if reply.ok and reply.ttl is not None]
        if not ttls:
            return None
        return Counter(ttls).most_common(1)[0][0]

    @property
    def hops_estimate(self) -> int | None:
        """由回复 TTL 估算的跳数（估算值，见 :attr:`PingReply.hops_estimate`）。"""
        ttl = self.reply_ttl
        if ttl is None:
            return None
        return PingReply(sequence=0, status=PingStatus.SUCCESS, ttl=ttl).hops_estimate

    @property
    def unexpected_sources(self) -> tuple[str, ...]:
        """成功回复中"不是目标地址"的来源。

        ICMP 回复理应来自被探测的主机；出现别的来源说明中间有设备在替它应答
        （本机的 TUN 就会造成这种现象），这类"可达"不该被当成校园链路的证据。
        """
        return tuple(
            reply.source
            for reply in self.replies
            if reply.ok and reply.source is not None and reply.source != self.target
        )
