"""检查 11：逐跳路径.

回答"断点在第几跳"：校园网里"能到路由器、到不了 BAS"和"能到 BAS、到不了深澜"
是完全不同的两类故障，而只报告"可达 / 不可达"分不出来。

**前置闸门**：开着代理 / VPN 的 TUN 时，`tracert` 给出的跳数是伪造的（实测到公网目标
只显示 1 跳），所以本项先看出站归属，是隧道就直接放弃 —— 与其给出好看的假跳数，
不如说清"这一项在有 TUN 时不成立"。关掉 TUN 后实测：校园深澜 6 跳、公网 8 跳。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import ALI_DNS_IP, SRUN_IP
from hdu_netdebugger.net.ping import BackendUnavailable
from hdu_netdebugger.registry import checker

if TYPE_CHECKING:
    from hdu_netdebugger.net.trace import TraceResult

__all__ = ["PathCheck"]

#: 探测目标：校园侧一个 + 公网一个做对照
_TARGETS: Final = (
    ("校园深澜", SRUN_IP),
    ("公网对照", ALI_DNS_IP),
)


@checker("检查逐跳路径", order=39)
class PathCheck(Checker):
    """逐跳看路径，指出断点在第几跳。"""

    async def check(self) -> None:
        tunneled = [
            label for label, target in _TARGETS if self.session.egress(target).is_tunnel_like
        ]
        if tunneled:
            names = "、".join(tunneled)
            self.add_warning(
                f"到 {names} 的探测经由隧道类接口出站：有 TUN 时跳数是伪造的"
                "（实测会显示成 1 跳），本项不做逐跳探测"
            )
            self.add_suggestion("要看真实路径与断点，请关掉 VPN / 代理的 TUN 模式后重跑")
            self.warned("有 TUN，逐跳结果不可信")
            return

        outcomes = await asyncio.gather(*(self._trace(label, target) for label, target in _TARGETS))
        if any(outcome is None for outcome in outcomes):
            return

        results: list[tuple[str, TraceResult, bool]] = [
            (label, *outcome)
            for (label, _target), outcome in zip(_TARGETS, outcomes, strict=True)
            if outcome is not None
        ]
        for label, result, _reachable in results:
            self.log(f"[{label}] {result.describe()}")
            self.log_lines(hop.describe() for hop in result.hops)

        self._report(results)

    async def _trace(self, label: str, target: object) -> tuple[TraceResult, bool] | None:
        """跑一次逐跳探测，并顺手确认目标本身是否可达。

        "跳数没到目标"与"目标不可达"是两件事：沿途设备不回 ICMP TTL 超时非常常见，
        那种情况下目标照样可达 —— 只有**目标本身也不可达**时，"最后有应答的那一跳"
        才真的是断点。所以两个结果必须一起拿。
        """
        self.log(f"[{label}] {target}")
        reachable = (await self.session.ping(target)).success_count > 0
        try:
            return await self.session.trace(target), reachable
        except BackendUnavailable as exc:
            self.log(f"逐跳探测不可用: {exc}")
            self.add_suggestion(
                "Linux / macOS 上需要先装 traceroute（Debian/Ubuntu: apt install traceroute）"
            )
            self.warned(f"无法进行逐跳探测（{exc}）")
            return None

    def _report(self, results: list[tuple[str, TraceResult, bool]]) -> None:
        """汇总：只有"目标不可达"时，"最后有应答的那一跳"才算断点。"""
        reached = [item for item in results if item[1].reached]
        broken: list[str] = []
        unresolved: list[str] = []

        for label, result, reachable in results:
            if result.reached:
                continue
            last = result.last_answering
            if reachable:
                # 目标 ping 得通、但那一跳没被观测到：拿不到完整路径，不是故障。
                # 可能原因：目标不回 TTL 超时 ICMP，或跳数超出上限。
                answered = len(result.answered_hops)
                self.log(
                    f"[{label}] 目标本身可达，但逐跳里没有观测到它那一跳"
                    f"（沿途 {answered} 跳有应答）：拿不到完整路径，不是故障"
                )
                continue
            if last is None:
                self.log(f"[{label}] 目标不可达且沿途没有任何应答，无法定位断点")
                unresolved.append(label)
                continue
            self.add_warning(
                f"{label} 目标不可达，且**第 {last.ttl} 跳之后**没有响应"
                f"（最后应答的是 {last.address}）：问题就出在这之后"
            )
            broken.append(label)

        if broken:
            self.add_suggestion("请把「最后有应答的那一跳」反馈给 ICU / 维护者，它能直接定位故障段")
            self.warned(f"逐跳结果：{len(reached)} 个目标到达，{len(broken)} 个目标不可达")
            return

        if unresolved:
            self.add_suggestion("目标不可达且沿途无应答，建议先做可达性排查（检查 4 / 检查 8）")
            self.warned(f"逐跳结果：{len(unresolved)} 个目标不可达且无法定位断点")
            return

        hops = "、".join(f"{label} {item.hop_count} 跳" for label, item, _ in reached)
        detail = f"（{hops}）" if hops else "（目标本身可达，但未观测到它那一跳）"
        self.passed(f"路径未见异常{detail}")
