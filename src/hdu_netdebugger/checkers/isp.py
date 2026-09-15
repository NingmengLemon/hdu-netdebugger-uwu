"""检查 7：运营商出口网关可达性.

对应原版 ``Checkers/ISPCheck.cs``。依赖「检查认证状态」解析出的运营商 ID：
拿不到有效 ID 时本检查会被**跳过**（而不是失败），
这就是 :func:`hdu_netdebugger.registry.condition` 的用武之地。
"""

from __future__ import annotations

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import SRUN_PRODUCT_GATEWAYS, SRUN_PRODUCT_NAMES
from hdu_netdebugger.registry import checker, condition

__all__ = ["ISPCheck"]


@checker("检查运营商网关可达性", order=30)
class ISPCheck(Checker):
    """Ping 当前运营商的出口网关。"""

    @condition("运营商ID非空且有效")
    def isp_available(self) -> bool:
        """上下文里是否已有可识别的运营商 ID。"""
        return self.context.isp_id in SRUN_PRODUCT_GATEWAYS

    async def check(self) -> None:
        isp = self.context.isp_id
        if isp is None:  # pragma: no cover - 条件已保证非空，这里只是收窄类型
            self.failed("未获取到运营商信息")
            return

        gateway = SRUN_PRODUCT_GATEWAYS[isp]
        name = SRUN_PRODUCT_NAMES[isp]
        self.log(f"检测到当前使用的运营商为 {name}，网关IP为 {gateway}。开始检测网关可达性...")

        result = await self.session.ping(gateway)
        self.log_lines(result.detail_lines())

        if result.all_succeeded:
            self.passed(f"{name}网关可达")
            return

        if result.success_count > 0:
            self.add_warning(
                f"{name}网关可达，但是存在丢包，丢包率 {result.packet_loss_percent:.0f}%，"
                f"平均延迟 {result.avg_rtt_ms:.0f} ms"
            )
            self.add_suggestion("请检查网线等硬件，或联系ICU")
            self.warned(f"{name}网关可达，但存在丢包")
            return

        self.add_warning(f"{name}网关不可达。")
        self.add_suggestion("请联系ICU并提供错误信息")
        self.failed(f"{name}网关不可达")
