"""检查 3：BAS 可达性.

对应原版 ``Checkers/BASCheck.cs``。BAS 不可达时再做一次公网探测，
以区分「不在校园网」和「校园网内部链路故障」。
"""

from __future__ import annotations

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import BAS_IP, MIUI_ROM_204_URL, NetworkKind
from hdu_netdebugger.registry import checker

__all__ = ["BASCheck"]


@checker("检查BAS可达性", order=10)
class BASCheck(Checker):
    """Ping 生活区宽带接入服务器。"""

    async def check(self) -> None:
        result = await self.session.ping(BAS_IP)
        self.log(self.session.egress(BAS_IP).describe())
        self.log_ping_evidence(BAS_IP, result)

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

        if self.context.network_kind is NetworkKind.CAMPUS:
            # 10.150.0.1 是**生活区**的 BAS，官方手册也没给教学区的对应测试点
            # （campus.html:32 写着"各教学楼有线地址待补充"）。
            #
            # 一开始我据此把教学区的不通降级成"仅供参考"，但 2026-09 的教学区实测推翻了它：
            # 教学区无线可以稳定 ping 通 10.150.0.1（4/4，5~29ms），两个区域之间路由互通。
            # 既然实测可达，那"教学区不通"就更可能是真问题 —— 降级会掩盖故障，
            # 所以这里照常判失败，只补一句上下文，方便报障时说清位置。
            self.add_suggestion(
                "本项探测的是生活区 BAS（10.150.0.1）：实测教学区也可以到（2026-09），"
                "因此这里不通更可能是真问题；报障时请带上你所在的区域（生活区 / 教学区 / 具体楼栋）"
            )

        self.failed("BAS 不可达")
