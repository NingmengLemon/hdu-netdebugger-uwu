"""检查 7：运营商出口网关可达性.

对应原版 ``Checkers/ISPCheck.cs``。依赖「检查认证状态」解析出的运营商 ID：
拿不到有效 ID 时本检查会被**跳过**（而不是失败），
这就是 :func:`hdu_netdebugger.registry.condition` 的用武之地。

与原版不同的是**候选网关逐个探测**：原版三个地址里有两个与 ICU 官方手册冲突
（很可能是把"出口公网网段"当成了网关），而手册也停在 2025-04，两边都不该被当成
唯一真相。这里谁应答就算谁通，并把实际应答的地址打进日志 —— 那本身就是可回报的证据。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import SRUN_PRODUCT_GATEWAYS, SRUN_PRODUCT_NAMES
from hdu_netdebugger.registry import checker, condition

if TYPE_CHECKING:
    from ipaddress import IPv4Address

    from hdu_netdebugger.models.ping import MultiPingResult

__all__ = ["ISPCheck"]


@checker("检查运营商网关可达性", order=30)
class ISPCheck(Checker):
    """Ping 当前运营商的出口网关（候选逐个试）。"""

    @condition("运营商ID非空且有效")
    def isp_available(self) -> bool:
        """上下文里是否已有可识别的运营商 ID。"""
        return self.context.isp_id in SRUN_PRODUCT_GATEWAYS

    async def check(self) -> None:
        isp = self.context.isp_id
        if isp is None:  # pragma: no cover - 条件已保证非空，这里只是收窄类型
            self.failed("未获取到运营商信息")
            return

        name = SRUN_PRODUCT_NAMES[isp]
        candidates = SRUN_PRODUCT_GATEWAYS[isp]
        listed = "、".join(str(item) for item in candidates)
        self.log(f"检测到当前使用的运营商为 {name}，候选网关: {listed}。开始检测网关可达性...")

        answers: list[tuple[IPv4Address, MultiPingResult]] = []
        for gateway in candidates:
            result = await self.session.ping(gateway)
            self.log(self.session.egress(gateway).describe())
            self.log_ping_evidence(gateway, result)
            if result.success_count:
                answers.append((gateway, result))
                if result.all_succeeded:
                    break  # 已经全通，后面的候选不必再试

        if not answers:
            self.add_warning(f"{name}网关不可达（候选: {listed}）")
            self.add_suggestion("请联系ICU并提供错误信息")
            self.failed(f"{name}网关不可达")
            return

        gateway, result = answers[0]
        if gateway != candidates[0]:
            self.log(f"注意: 首选候选 {candidates[0]} 无应答，实际应答的是 {gateway}")
            self.add_suggestion(
                f"{name}的网关地址与手册/原版不一致（实际应答 {gateway}），"
                "可把这条证据反馈给 ICU / 维护者"
            )

        if result.all_succeeded:
            self.passed(f"{name}网关可达")
            return

        self.add_warning(
            f"{name}网关可达，但是存在丢包，丢包率 {result.packet_loss_percent:.0f}%，"
            f"平均延迟 {result.avg_rtt_ms:.0f} ms"
        )
        self.add_suggestion("请检查网线等硬件，或联系ICU")
        self.warned(f"{name}网关可达，但存在丢包")
