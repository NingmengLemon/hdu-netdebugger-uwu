"""检查 4：生活区深澜认证服务器可达性.

对应原版 ``Checkers/SrunCheck.cs``。
"""

from __future__ import annotations

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import SRUN_IP
from hdu_netdebugger.registry import checker

__all__ = ["SrunReachabilityCheck"]


@checker("检查生活区深澜认证可达性", order=15)
class SrunReachabilityCheck(Checker):
    """Ping 深澜认证服务器。"""

    async def check(self) -> None:
        result = await self.session.ping(SRUN_IP)
        self.log(self.session.egress(SRUN_IP).describe())
        self.log_ping_evidence(SRUN_IP, result)

        if result.all_succeeded:
            self.passed("深澜认证服务器可达")
            return

        if result.success_count > 0:
            self.add_warning(
                f"深澜认证服务器可达，但是存在丢包，丢包率 {result.packet_loss_percent:.0f}%，"
                f"平均延迟 {result.avg_rtt_ms:.0f} ms"
            )
            self.add_suggestion("请检查网线等硬件，或联系ICU")
            self.warned("深澜认证服务器可达，但存在丢包")
            return

        self.add_warning("深澜认证服务器不可达。")
        self.failed("深澜认证服务器不可达")
