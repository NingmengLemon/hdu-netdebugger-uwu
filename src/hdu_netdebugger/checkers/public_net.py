"""检查 8：公网可达性.

对应原版 ``Checkers/PublicNetCheck.cs``：先 Ping 阿里公共 DNS，
再用 MIUI 的 ``generate_204`` 探针确认 HTTP 层真的通了（能 Ping 通
不代表没被门户劫持）。

本复刻多加一步**锚点交叉验证**：再 Ping 一个异构的公共 DNS。理由是群里明确讨论过
"223.5.5.5 也会被劫持/被限速" —— 单个锚点既可能假通（回复其实来自中间层），
也可能假不通（只是该目标被限速），只有一个数字就下结论不稳。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import ALI_DNS_IP, MIUI_ROM_204_URL, PUBLIC_ANCHOR_IP
from hdu_netdebugger.registry import checker

if TYPE_CHECKING:
    from hdu_netdebugger.models.ping import MultiPingResult

__all__ = ["PublicNetCheck"]


@checker("检查公网可达性", order=35)
class PublicNetCheck(Checker):
    """Ping + HTTP 双重验证公网出口，并对 Ping 锚点做交叉验证。"""

    async def check(self) -> None:
        result = await self.session.ping(ALI_DNS_IP)
        self.log(self.session.egress(ALI_DNS_IP).describe())
        self.log_ping_evidence(ALI_DNS_IP, result)

        secondary = await self.session.ping(PUBLIC_ANCHOR_IP)
        self.log(self.session.egress(PUBLIC_ANCHOR_IP).describe())
        self.log_ping_evidence(PUBLIC_ANCHOR_IP, secondary)
        self._cross_check_anchors(result, secondary)

        if result.all_succeeded:
            self.log("连接到阿里主DNS成功，公网可达。")
        elif result.success_count > 0:
            self.add_warning(
                f"阿里主DNS可达，但是存在丢包，丢包率 {result.packet_loss_percent:.0f}%，"
                f"平均延迟 {result.avg_rtt_ms:.0f} ms"
            )
            self.add_suggestion("请检查网线等硬件，或联系ICU")
        elif secondary.success_count:
            self.add_warning(
                f"阿里主DNS不可达，但备用锚点 {PUBLIC_ANCHOR_IP} 可达（对外网来说公网是通的）"
            )
        else:
            self.add_warning(f"两个公网锚点都不可达（{ALI_DNS_IP} / {PUBLIC_ANCHOR_IP}）")

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

    def _cross_check_anchors(self, primary: MultiPingResult, secondary: MultiPingResult) -> None:
        """两个锚点结论不一致时说明清楚，而不是挑一个数字下结论。"""
        if primary.all_succeeded == secondary.all_succeeded:
            return
        if primary.all_succeeded:
            self.add_warning(
                f"公网锚点结果不一致：{ALI_DNS_IP} 可达但 {PUBLIC_ANCHOR_IP} 不可达 —— "
                "可能是该目标被限速 / 被劫持，也可能只是它自己不通；请以下面的 HTTP 探测为准"
            )
            return
        self.add_warning(
            f"公网锚点结果不一致：{PUBLIC_ANCHOR_IP} 可达但 {ALI_DNS_IP} 不可达 —— "
            "公网其实是通的，只是主锚点这个目标不通"
        )
