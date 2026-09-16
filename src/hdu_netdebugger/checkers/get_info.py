"""检查 1：获取基本信息.

对应原版 ``Checkers/GetInfo.cs``：枚举活动网卡、识别所在的杭电网段，
并顺带嗅探 VPN / 隧道类接口。

本复刻在此之上多做了两件原版没有、但排查时最要紧的事：

* 每个接口额外输出 **IP 配置证据**（DHCP 开没开、网关、DNS、系统索引 / MTU）——
  ICU / 电专互联的排查流程第一步就是"是否已正确获取到 IP 地址"；
* 输出**探测出站归属**：本工具后续的 ping 到底从哪张网卡出去。校园里装代理 / VPN
  的同学不少，TUN 接口会以更低的 metric 抢走默认路由，于是所有探测都从隧道出去 ——
  那时"BAS 可达"并不代表校园物理链路可达，必须让读者看到这一点。
"""

from __future__ import annotations

import asyncio
from ipaddress import IPv4Address
from typing import Final

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import (
    ALI_DNS_IP,
    BAS_IP,
    CAMPUS_NETWORK,
    DORM_NETWORK,
    LINK_LOCAL_NETWORK,
    SRUN_IP,
    NetworkKind,
    abnormal_network_note,
)
from hdu_netdebugger.models.interface import InterfaceInfo, InterfaceKind
from hdu_netdebugger.net.interfaces import list_interfaces
from hdu_netdebugger.registry import checker

__all__ = ["GetInfoCheck"]

#: 后续检查项会探测的目标：这里把它们各自的出站接口一并查出来
_PROBE_TARGETS: Final = (
    ("BAS", BAS_IP),
    ("深澜认证", SRUN_IP),
    ("公网DNS", ALI_DNS_IP),
)


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
            usable_ethernet = (
                interface.kind is InterfaceKind.ETHERNET and interface.ipv4 and interface.is_up
            )
            if usable_ethernet:
                found_any = True
                self.log_lines(interface.describe())
                self.log_lines(interface.describe_config())
                self._check_random_mac(interface)
                network = interface.network
                if network is not None:
                    # 与原版一致：后匹配到的接口覆盖前面的结果
                    self.context.network_kind = network
            elif interface.kind is InterfaceKind.ETHERNET and not interface.is_up:
                # 链路没建立的有线网卡也要打出来：群里有过"网线明明插着、系统却说没插"，
                # 最后靠把协商速率手动降到 100M 才通的案例 —— 那种情况下
                # 唯一的证据就是这块网卡自己（是否链路 up、协商到多少）
                self.log(f"有线网卡 {interface.name} 链路未建立：")
                self.log_lines(interface.describe_config())
            elif interface.is_vpn_like and interface.is_up:
                found_any = True
                self._report_tunnel(interface)

        if not found_any:
            self.add_warning("未找到符合条件的网络接口（以太网且已启用且有IPv4地址）")
            self.failed("未找到符合条件的网络接口")
            return

        self._report_egress()
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

    def _log_default_routes(self) -> None:
        """列出全部 IPv4 默认路由。

        多条 ``0.0.0.0/0`` 同时存在（物理网卡 / 无线 / 隧道各一条）是"网线插着却从
        隧道出去"的直接原因，只报最优那条看不出这个竞争关系。

        ``DefaultRoute.metric`` 是**路由 metric**（Windows 转发表里的值，常常都是 0），
        真正排序用的是"接口 metric + 路由 metric"，所以这里如实标注字段含义，
        实际生效的那条由下面的"探测出站归属"给出。
        """
        routes = self.session.default_routes()
        if not routes:
            return
        names = {interface.index: interface.name for interface in self.session.interfaces()}
        self.log(f"IPv4 默认路由（共 {len(routes)} 条，Metric 为路由 metric）:")
        for route in routes:
            label = names.get(route.interface_index, "(未知接口)")
            self.log(
                f"- {label} (ifIndex {route.interface_index}) "
                f"下一跳 {route.next_hop or '(无)'} 路由 metric {route.metric}"
            )

    def _check_random_mac(self, interface: InterfaceInfo) -> None:
        """MAC 被随机化会让"无感知认证"失效（杭电按 MAC 绑定）。

        群体证据：管理员在群里两次把认证 / 联网异常指向"网卡 MAC 是不是设的随机"。
        判据是 IEEE 802 的本地管理位，不需要额外系统调用（MAC 在枚举网卡时就有了）。
        """
        if not interface.is_random_mac:
            return
        self.add_warning(f"网卡 {interface.name} 使用的是随机化 MAC（{interface.mac}）")
        self.add_suggestion(
            "校园网按 MAC 做无感知认证，随机 MAC 会导致频繁掉认证；"
            "建议在系统设置里关闭该网卡的「随机硬件地址」并改用固定 MAC"
        )

    def _report_egress(self) -> None:
        """记录探测流量实际从哪个接口出去，并在被隧道接管时明确警告。"""
        self._log_default_routes()
        self.log("探测出站归属:")
        egresses = [(label, self.session.egress(target)) for label, target in _PROBE_TARGETS]
        for label, egress in egresses:
            kind = egress.interface.kind.label if egress.interface is not None else "未知"
            self.log(f"- {label} {egress.target}: {egress.describe()}（类型 {kind}）")

        tunnels = [egress for _label, egress in egresses if egress.is_tunnel_like]
        if not tunnels:
            return

        names = "、".join(sorted({item.name for egress in tunnels if (item := egress.interface)}))
        self.add_warning(
            f"探测流量经由隧道类接口出站（{names}，metric 更低所以抢走了默认路由）："
            "以下各项「可达」结论反映的是隧道侧，不代表校园网物理链路"
        )
        self.add_suggestion(
            "只想看校园网本身的情况时，关掉 VPN / 代理的 TUN 模式后重跑；"
            "否则请忽略 BAS / 深澜 / 公网 这几项的可达性结论"
        )

    def _summary(self) -> str:
        """根据识别出的网段给出总览。"""
        network = self.context.network_kind
        if network is not None:
            if network is NetworkKind.EDUROAM:
                self.add_suggestion(
                    "eduroam 是漫游网络，与生活区/教学区的认证体系不同；"
                    "校园网问题请连着 i-HDU 或网线复现"
                )
            return f"检测到{network.label}"

        self._report_abnormal_addresses()

        known = _known_ranges_text()
        self.add_warning(f"未检测到校园网地址（工具已知网段：{known}）")
        self.add_suggestion(
            "若你确实在校园网内（例如在路由器后面），请忽略这一条；"
            f"若你的地址段属于校园网但不在「{known}」内，"
            "请把该网段反馈给 ICU / 维护者，工具会把它补进去"
        )
        return "未检测到校园网地址"

    def _report_abnormal_addresses(self) -> None:
        """地址落在异常网段时给出可行动的说明（官方手册最突出的那类故障）。

        手册把它们分得很清楚：``169.254/16`` 是 DHCP 没拿到地址、``192.168/16`` 是
        路由器自己发的、``10.230/16`` 另有异常 —— 而"未检测到校园网地址"这一句话
        把这三种完全不同的处境混成了一种。
        """
        seen: set[str] = set()
        # 检查 1 自己枚举的结果就在 context 里；`--only` 之外的路径不会为空
        interfaces = self.context.interfaces or self.session.interfaces()
        for interface in interfaces:
            for text in interface.ipv4:
                if text in seen:
                    continue
                seen.add(text)
                try:
                    address = IPv4Address(text)
                except ValueError:  # pragma: no cover - 枚举层已过滤
                    continue
                note = abnormal_network_note(address)
                if note is None:
                    continue
                self.add_warning(f"{interface.name}: {note}")

        if any(
            address in LINK_LOCAL_NETWORK
            for interface in interfaces
            for address in interface.ipv4_addresses
        ):
            self.add_suggestion(
                "拿到 169.254.x.x 说明 DHCP 失败：确认网线/无线真的连上了，"
                "路由器请把上网方式设为 DHCP 自动获取（不要用 PPPoE 拨号）"
            )


def _known_ranges_text() -> str:
    """工具内置的校园网网段，用来说明"我按什么判定的"。

    这两个网段来自 C# 原版（``Models/HDUConst.cs``），本复刻保持一致。
    校园网拓扑会变，而网上流传的说明文档经常是旧的 —— 所以判定失败时把标准
    连同实际地址一起打出来（每个接口的地址 / 网关 / DNS 在检查 1 的详细日志里），
    让人能自己判断，而不是只给一句"未检测到"。
    """
    return f"{NetworkKind.DORM.label} {DORM_NETWORK}、{NetworkKind.CAMPUS.label} {CAMPUS_NETWORK}"
