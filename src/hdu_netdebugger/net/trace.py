"""逐跳路径探测（traceroute）.

用途：回答"断点在第几跳"。校园网里"能到路由器、到不了 BAS"和"能到 BAS、到不了深澜"
是完全不同的两类故障，而只报告"可达 / 不可达"分不出来。

**有 TUN 时这一项必须直接放弃**：实测开着代理的 TUN 时，到公网目标的 `tracert` 只显示
1 跳（回复由隧道栈伪造），跳数是彻头彻尾的假象。所以调用方要先看出站归属
（:meth:`hdu_netdebugger.session.RunSession.egress`）再决定是否采信。

解析要同时吃下两种列序（都用"行里第一个像 IP 的 token"来定位地址）::

    Windows:  1    <1 ms    <1 ms    <1 ms  192.0.2.1
    Linux:    1  192.0.2.1  0.123 ms  0.100 ms  0.090 ms

以及"该跳无应答"（``*  *  *`` / ``Request timed out.`` / ``请求超时。``）。
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from ipaddress import ip_address
from typing import TYPE_CHECKING, Final

from hdu_netdebugger.net.console import decode_console_output
from hdu_netdebugger.net.ping import BackendUnavailable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from hdu_netdebugger.models.ping import MultiPingResult

    #: 单次 Ping 的签名（与 ``net.ping.ping_multiple`` 一致）
    Pinger = Callable[..., Awaitable[MultiPingResult]]

__all__ = ["TraceHop", "TraceResult", "parse_trace", "traceroute"]

#: 一次探测最多看几跳（超过就停，避免拖时间）
DEFAULT_MAX_HOPS: Final = 8
#: 每跳等待时间（毫秒）。
#:
#: 实测 RTT 只有 1-2ms，但 Windows 的 ``tracert`` **每跳发 3 个探针**，所以"无应答的跳"
#: 要等 3 倍超时：600ms 时每跳 1.8 秒、8 跳就要 10.8 秒（实测如此）。压到 200ms 后
#: 同样路径约 4-5 秒，仍远大于 RTT。
_HOP_TIMEOUT_MS: Final = 200
#: 自己实现逐 TTL 探测时，每跳的等待时间（秒）。
#:
#: 已应答的跳几乎不耗时，只有"无应答的跳"才会等满它 —— 所以这个值直接决定最坏耗时。
_HOP_TIMEOUT_S: Final = 0.4
#: 子进程的硬上限（秒）：禁止无限等待
_PROCESS_TIMEOUT_S: Final = 30.0

_HOP_LINE = re.compile(r"^\s*(\d+)\s+(?P<body>.+?)\s*$")
_RTT = re.compile(r"(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
_IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_IPV6 = re.compile(r"\b([0-9a-fA-F:]{3,}(?::[0-9a-fA-F]{1,4})+)\b")


@dataclass(frozen=True, slots=True)
class TraceHop:
    """一跳的探测结果。"""

    ttl: int
    address: str | None = None
    rtt_ms: float | None = None

    @property
    def answered(self) -> bool:
        """这一跳是否有设备应答。"""
        return self.address is not None

    def describe(self) -> str:
        """渲染成一行日志。"""
        if not self.answered:
            return f"第 {self.ttl} 跳: 无应答"
        rtt = f"，{self.rtt_ms:.0f} ms" if self.rtt_ms is not None else ""
        return f"第 {self.ttl} 跳: {self.address}{rtt}"


@dataclass(frozen=True, slots=True)
class TraceResult:
    """到某个目标的逐跳结果。"""

    target: str
    hops: tuple[TraceHop, ...] = ()

    @property
    def answered_hops(self) -> tuple[TraceHop, ...]:
        """有应答的跳。"""
        return tuple(hop for hop in self.hops if hop.answered)

    @property
    def last_answering(self) -> TraceHop | None:
        """最后一个有应答的跳 —— 出问题时它就是"断点前的最后一站"。"""
        answered = self.answered_hops
        return answered[-1] if answered else None

    @property
    def reached(self) -> bool:
        """目标本身是否出现在某一跳里。"""
        return any(hop.address == self.target for hop in self.hops)

    @property
    def hop_count(self) -> int | None:
        """到达目标所需的跳数；没到达时为 ``None``。"""
        for hop in self.hops:
            if hop.address == self.target:
                return hop.ttl
        return None

    def describe(self) -> str:
        """渲染成一行结论。"""
        if self.reached:
            return f"{self.target}: 第 {self.hop_count} 跳到达"
        last = self.last_answering
        if last is None:
            return f"{self.target}: 沿途没有任何设备回应 TTL 超时，跳数不可得"
        return f"{self.target}: 到第 {last.ttl} 跳（{last.address}）为止还有应答，之后没有响应"


def parse_trace(target: str, output: str) -> TraceResult:
    """把 ``tracert`` / ``traceroute`` 的文本解析成逐跳结果。

    只认"行首是跳数"的行；Windows 与 Linux 的列序不同，所以地址统一用
    "行里第一个像 IP 的 token" 定位，这样两种列序都能吃。
    """
    hops: list[TraceHop] = []
    for line in output.splitlines():
        match = _HOP_LINE.match(line)
        if match is None:
            continue
        ttl = int(match.group(1))
        body = match.group("body")
        address = _first_address(body)
        rtt_match = _RTT.search(body)
        hops.append(
            TraceHop(
                ttl=ttl,
                address=address,
                rtt_ms=float(rtt_match.group(1)) if rtt_match else None,
            )
        )
    return TraceResult(target=target, hops=tuple(hops))


def _first_address(text: str) -> str | None:
    """取行里第一个合法 IP（IPv4 优先，其次 IPv6）。"""
    for pattern in (_IPV4, _IPV6):
        for candidate in pattern.findall(text):
            try:
                ip_address(candidate)
            except ValueError:
                continue
            return candidate
    return None


def _trace_args(target: str, max_hops: int) -> tuple[str, list[str]]:
    """按平台拼命令与参数。"""
    if sys.platform == "win32":
        return "tracert", ["-d", "-h", str(max_hops), "-w", str(_HOP_TIMEOUT_MS), target]
    # Linux / macOS：traceroute 需要 -n（不解析名字）、-m（最大跳数）、-w（秒）
    # Linux / macOS：-n 不解析名字、-m 最大跳数、-q 每跳只发 1 个探针（Windows 没这个选项）、
    # -w 每跳超时（秒）
    return "traceroute", ["-n", "-m", str(max_hops), "-q", "1", "-w", "1", target]


async def traceroute(
    target: str,
    *,
    max_hops: int = DEFAULT_MAX_HOPS,
    pinger: Pinger | None = None,
) -> TraceResult:
    """跑一次逐跳探测。

    Windows 上优先用**自己的 ICMP 逐 TTL 探测**：``tracert`` 每跳发 3 个探针、且实测
    ``-w`` 被忽略（8 跳要 11 秒），而自己的实现每跳只发 1 个、已应答的跳几乎不耗时
    （实测同一路径 1-2 秒）。它也不用解析本地化文本，少一层假结论的风险。
    拿不到 ICMP 权限时退回系统 ``tracert`` / ``traceroute``。

    Raises:
        BackendUnavailable: 本机既没有可用的 ICMP 后端，也没有 traceroute 命令。
    """
    if sys.platform == "win32":
        try:
            return await _windows_trace(target, max_hops, pinger=pinger)
        except BackendUnavailable:
            pass  # 退回系统命令
    return await _system_trace(target, max_hops)


async def _windows_trace(
    target: str,
    max_hops: int,
    *,
    pinger: Pinger | None = None,
) -> TraceResult:
    """逐 TTL 发送 ICMP Echo，按回复的 Status 与 Address 还原路径。"""
    from hdu_netdebugger.net.ping import (  # noqa: PLC0415 - 避免模块级循环导入
        Backend,
        PingOptions,
        ping_multiple,
    )

    send = pinger or ping_multiple
    hops: list[TraceHop] = []
    for ttl in range(1, max_hops + 1):
        result = await send(
            target,
            PingOptions(count=1, timeout_s=_HOP_TIMEOUT_S, ttl=ttl),
            backend=Backend.WINDOWS_ICMP,
        )
        reply = result.replies[0]
        if reply.ok:
            hops.append(TraceHop(ttl=ttl, address=target, rtt_ms=reply.rtt_ms))
            break
        if reply.source is not None:
            # TTL 超时：Address 就是那台中间设备
            hops.append(TraceHop(ttl=ttl, address=reply.source, rtt_ms=reply.rtt_ms))
            continue
        hops.append(TraceHop(ttl=ttl))
    return TraceResult(target=target, hops=tuple(hops))


async def _system_trace(target: str, max_hops: int) -> TraceResult:
    """调用系统 ``tracert`` / ``traceroute`` 并解析文本（非 Windows 的路径）。"""
    command, args = _trace_args(target, max_hops)
    try:
        process = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, NotImplementedError) as exc:
        raise BackendUnavailable(f"本机没有 {command} 命令: {exc}") from exc

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=_PROCESS_TIMEOUT_S)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise BackendUnavailable(f"{command} 超过 {_PROCESS_TIMEOUT_S:.0f} 秒仍未结束") from exc

    return parse_trace(target, decode_console_output(stdout))


def summarize(results: Sequence[TraceResult]) -> list[str]:
    """把多个目标的结论整理成日志行。"""
    return [result.describe() for result in results]
