"""ICMP Ping 的结果模型。"""

from __future__ import annotations

from dataclasses import dataclass

from hdu_netdebugger.models.enums import PingStatus

__all__ = ["MultiPingResult", "PingReply"]


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

    @property
    def ok(self) -> bool:
        """本次请求是否成功收到回复。"""
        return self.status is PingStatus.SUCCESS

    def describe(self, target: str) -> str:
        """渲染成与原版一致的单行日志。"""
        rtt = 0.0 if self.rtt_ms is None else self.rtt_ms
        suffix = f" ({self.error})" if self.error else ""
        return (
            f"Ping {target} 第 {self.sequence} 次: "
            f"状态:{self.status.label}, 时间:{rtt:.0f}ms{suffix}"
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
        return lines
