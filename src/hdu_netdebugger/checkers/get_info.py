"""检查 1：获取基本信息.

对应原版 ``Checkers/GetInfo.cs``：枚举活动网卡、识别所在的杭电网段，
并顺带嗅探 VPN / 隧道类接口。
"""

from __future__ import annotations

import asyncio

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.models.interface import InterfaceInfo, InterfaceKind
from hdu_netdebugger.net.interfaces import list_interfaces
from hdu_netdebugger.registry import checker

__all__ = ["GetInfoCheck"]


@checker("获取基本信息", order=-1)
class GetInfoCheck(Checker):
    """枚举网络接口并判断所处网络区域。"""

    async def check(self) -> None:
        interfaces = await asyncio.to_thread(list_interfaces)
        if not interfaces:
            self.failed("未获取到网络接口信息")
            return

        self.context.interfaces = interfaces
        found_any = False

        for interface in interfaces:
            if not interface.is_up:
                continue
            if interface.kind is InterfaceKind.ETHERNET and interface.ipv4:
                found_any = True
                self.log_lines(interface.describe())
                network = interface.network
                if network is not None:
                    # 与原版一致：后匹配到的接口覆盖前面的结果
                    self.context.network_kind = network
            elif interface.is_vpn_like:
                found_any = True
                self._report_tunnel(interface)

        if not found_any:
            self.add_warning("未找到符合条件的网络接口（以太网且已启用且有IPv4地址）")
            self.failed("未找到符合条件的网络接口")
        else:
            self.passed(self._summary())

    def _report_tunnel(self, interface: InterfaceInfo) -> None:
        """记录 VPN / 隧道接口。"""
        self.add_warning(f"检测到 {interface.kind.label} 类型网络接口")
        self.add_suggestion("建议关闭 VPN 或其他隧道接口")
        self.log(
            f"检测到活动的 {interface.kind.label} 类型网络接口，可能为 VPN 或其他隧道接口，详情如下："
        )
        self.log_lines(interface.describe())
        self.context.has_tunnel_interface = True

    def _summary(self) -> str:
        """根据识别出的网段给出总览。"""
        network = self.context.network_kind
        if network is not None:
            return f"检测到{network.label}"
        self.add_warning("未检测到校园网地址")
        self.add_suggestion("请检查网络连接，确保已连接到校园网，如果在路由器下使用，请忽略")
        return "未检测到校园网地址"
