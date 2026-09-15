"""检查 3：BAS 可达性.

对应原版 ``Checkers/BASCheck.cs``。BAS 不可达时再做一次公网探测，
以区分「不在校园网」和「校园网内部链路故障」。
"""

from __future__ import annotations

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import BAS_IP, MIUI_ROM_204_URL
from hdu_netdebugger.registry import checker

__all__ = ["BASCheck"]


@checker("检查BAS可达性", order=10)
class BASCheck(Checker):
    """Ping 生活区宽带接入服务器。"""

    async def check(self) -> None:
        result = await self.session.ping(BAS_IP)
        self.log_lines(result.detail_lines())

        if result.all_succeeded:
            self.passed("BAS 可达")
            return

        if result.success_count > 0:
            self.add_warning(
                f"BAS 可达，但是存在丢包，丢包率 {result.packet_loss_percent:.0f}%，"
                f"平均延迟 {result.avg_rtt_ms:.0f} ms"
            )
            self.add_suggestion("请检查网线等硬件，或联系ICU")
            self.warned("BAS 可达，但存在丢包")
            return

        self.add_warning("BAS 不可达。")
        response = await self.session.http.get(MIUI_ROM_204_URL)
        self.log(response.describe())
        if response.is_no_content:
            self.add_warning("您可能不是在使用校园网")
            self.add_suggestion("请切换到校园网环境后重试")
        else:
            self.add_suggestion("请检查IP配置或尝试重新进行物理连接")
        self.failed("BAS 不可达")
