"""检查 8：公网可达性.

对应原版 ``Checkers/PublicNetCheck.cs``：先 Ping 阿里公共 DNS，
再用 MIUI 的 ``generate_204`` 探针确认 HTTP 层真的通了（能 Ping 通
不代表没被门户劫持）。
"""

from __future__ import annotations

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import ALI_DNS_IP, MIUI_ROM_204_URL
from hdu_netdebugger.registry import checker

__all__ = ["PublicNetCheck"]


@checker("检查公网可达性", order=35)
class PublicNetCheck(Checker):
    """Ping + HTTP 双重验证公网出口。"""

    async def check(self) -> None:
        result = await self.session.ping(ALI_DNS_IP)
        self.log(self.session.egress(ALI_DNS_IP).describe())
        self.log_ping_evidence(ALI_DNS_IP, result)

        if result.all_succeeded:
            self.log("连接到阿里主DNS成功，公网可达。")
        elif result.success_count > 0:
            self.add_warning(
                f"阿里主DNS可达，但是存在丢包，丢包率 {result.packet_loss_percent:.0f}%，"
                f"平均延迟 {result.avg_rtt_ms:.0f} ms"
            )
            self.add_suggestion("请检查网线等硬件，或联系ICU")
        else:
            self.add_warning("阿里主DNS不可达。")

        response = await self.session.http.get(MIUI_ROM_204_URL)
        self.log(response.describe())

        if response.is_no_content:
            self.passed("公网可达")
            return

        if response.is_redirect:
            self.add_warning(
                f"HTTP 请求被重定向到 {response.headers.get('location', '未知地址')}，疑似门户劫持"
            )
            self.add_suggestion("请确认已完成校园网认证")
        else:
            self.add_warning("无法通过HTTP访问公网地址。")
            self.add_suggestion("请检查IP配置或尝试重新进行物理连接")
        self.failed("公网不可达")
