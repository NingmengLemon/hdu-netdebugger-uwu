"""路径 MTU 探测：小包通、大包不通那一类故障.

为什么需要它：``ping`` 默认只发 32 字节，所以"能 ping 通"完全不代表**大包**能过。
实测（本机，宿舍路由器 + 寝室网上游，TUN 关闭）::

    10.150.0.1        DF 与非 DF 都能过 1472 字节（≈ MTU 1500）
    223.5.5.5         DF 上限 1456、非 DF 1472（≈ 1484 / 1500）
    192.168.112.97    DF 与非 DF **都卡在 996 字节**（≈ MTU 1024），
                      且从不返回 ICMP "需要分片"

那个校园目标就是典型症状：小包一路正常，`ping` 全绿，但大包被静默丢弃 ——
表现为"能上 QQ 却打不开网页 / 下载卡住"。这类故障本工具此前完全没有覆盖。

判定思路（先证后断）：
1. 先用最小载荷确认目标本身可达，不可达就不谈 MTU；
2. 再用 1472 字节（1500 的经典路径）快筛，通了就直接结束 —— 健康网络只花两次探测；
3. 不通才二分找边界，并记录**是否出现过 ICMP 需要分片**：出现过说明只是 MTU 偏小，
   什么也不回则是"静默丢弃"（MTU 黑洞形态）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from hdu_netdebugger.models.ping import MultiPingResult

__all__ = [
    "MAX_PROBE_PAYLOAD",
    "MIN_PROBE_PAYLOAD",
    "MtuProbe",
    "MtuResult",
    "probe_path_mtu",
]

#: ICMP 载荷下界：32 字节是各平台的默认值，也用来确认目标可达
MIN_PROBE_PAYLOAD = 32
#: ICMP 载荷上界：1500 - 20(IP 头) - 8(ICMP 头)
MAX_PROBE_PAYLOAD = 1472
#: IP 头 + ICMP 头的开销，用来把载荷换回路径 MTU
_HEADER_OVERHEAD = 28
#: 二分探测的最多次数（防止目标反复抖动时无限探测）
_MAX_PROBES = 16


@dataclass(frozen=True, slots=True)
class MtuProbe:
    """一次载荷大小的探测结果。"""

    size: int
    ok: bool
    error: str | None = None

    def describe(self) -> str:
        """渲染成一行日志。"""
        mark = "通过" if self.ok else "失败"
        suffix = f"（{self.error}）" if self.error else ""
        return f"{self.size} 字节载荷: {mark}{suffix}"


@dataclass(frozen=True, slots=True)
class MtuResult:
    """一个目标的路径 MTU 探测结果。"""

    target: str
    df: bool
    probes: tuple[MtuProbe, ...] = field(default_factory=tuple)

    @property
    def reachable(self) -> bool:
        """最小载荷是否有应答（连它都不通就谈不上 MTU）。"""
        return any(probe.ok for probe in self.probes if probe.size == MIN_PROBE_PAYLOAD)

    @property
    def max_payload(self) -> int | None:
        """通过的最大载荷；一次都没通过时为 ``None``。"""
        passed = [probe.size for probe in self.probes if probe.ok]
        return max(passed) if passed else None

    @property
    def path_mtu(self) -> int | None:
        """由最大载荷推出的路径 MTU（载荷 + 28）。"""
        payload = self.max_payload
        return None if payload is None else payload + _HEADER_OVERHEAD

    @property
    def frag_needed_seen(self) -> bool:
        """是否收到过 ICMP"需要分片"。"""
        return any("需要分片" in (probe.error or "") for probe in self.probes)

    @property
    def limited(self) -> bool:
        """是否存在低于常见 MTU（1500）的上限。"""
        payload = self.max_payload
        return payload is not None and payload < MAX_PROBE_PAYLOAD

    @property
    def silent_drop(self) -> bool:
        """大包被**静默**丢弃：有上限、但没有任何"需要分片"的回执。"""
        return self.limited and not self.frag_needed_seen

    def describe(self) -> str:
        """渲染成一行结论。"""
        if not self.reachable:
            return f"{self.target}: 目标不可达，无法测量路径 MTU"
        payload = self.max_payload
        mode = "DF" if self.df else "非 DF"
        if payload is None:  # pragma: no cover - reachable 已保证非空
            return f"{self.target}: 未通过任何载荷"
        if payload >= MAX_PROBE_PAYLOAD:
            return f"{self.target}: {mode} 下 {MAX_PROBE_PAYLOAD} 字节可通（路径 MTU 未见受限）"
        suffix = "，收到过 ICMP 需要分片" if self.frag_needed_seen else "，全程没有 ICMP 需要分片"
        return (
            f"{self.target}: {mode} 下最大可通过载荷 {payload} 字节"
            f"（路径 MTU ≈ {payload + _HEADER_OVERHEAD}）{suffix}"
        )


async def probe_path_mtu(
    target: str,
    probe: Callable[[int], Awaitable[MultiPingResult]],
    *,
    df: bool = True,
) -> MtuResult:
    """二分探测 ``target`` 的路径 MTU。

    Args:
        target: 目标地址（只用于日志与结果标注）。
        probe: ``(载荷大小) -> 该尺寸的 Ping 结果``。是否设置 DF 由调用方决定。
        df: 本次探测是否为 DF 探测（只用于结果标注，不影响算法）。

    Returns:
        探测结果（含每次尝试的记录，便于解释结论是怎么来的）。
    """
    probes: list[MtuProbe] = []

    baseline = await probe(MIN_PROBE_PAYLOAD)
    probes.append(_record(MIN_PROBE_PAYLOAD, baseline))
    if not probes[-1].ok:
        # 公网目标的单个 ICMP 包本来就会偶发丢失（本机实测阿里/谷歌 DNS 都出现过 1/2），
        # 一次失败不足以判定"目标不可达" —— 否则整个检查会因为一个丢包而失去意义。
        retry = await probe(MIN_PROBE_PAYLOAD)
        probes.append(_record(MIN_PROBE_PAYLOAD, retry))
        if not probes[-1].ok:
            return MtuResult(target=target, df=df, probes=tuple(probes))

    largest = await probe(MAX_PROBE_PAYLOAD)
    probes.append(_record(MAX_PROBE_PAYLOAD, largest))
    if probes[-1].ok:
        return MtuResult(target=target, df=df, probes=tuple(probes))

    low, high = MIN_PROBE_PAYLOAD, MAX_PROBE_PAYLOAD
    while high - low > 1 and len(probes) < _MAX_PROBES:
        middle = (low + high) // 2
        outcome = await probe(middle)
        probes.append(_record(middle, outcome))
        if probes[-1].ok:
            low = middle
        else:
            high = middle

    return MtuResult(target=target, df=df, probes=tuple(probes))


def summarize(results: Iterable[MtuResult]) -> Sequence[str]:
    """把多个目标的结论整理成日志行。"""
    return [result.describe() for result in results]


def _record(size: int, result: MultiPingResult) -> MtuProbe:
    """把一次 Ping 汇总成探测记录。"""
    if result.success_count:
        return MtuProbe(size=size, ok=True)
    error = next((reply.error for reply in result.replies if reply.error), None)
    return MtuProbe(size=size, ok=False, error=error)
