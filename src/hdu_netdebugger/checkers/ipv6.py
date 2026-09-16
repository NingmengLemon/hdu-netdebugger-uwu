"""检查 10：IPv6.

为什么单独做一项：钉钉群日志里**唯一一条有完整证据链**的故障就在 IPv6 上 ——
生活区 IPv6 存在 PMTUD 黑洞（终端收到的 RA MTU 是 1500，到某公共 v6 目标 1420 字节正常、
**1428 字节 100% 丢失**，且收不到 ICMPv6 Type 2）。当时 IPv4 一切正常，
所以没有 v6 探测的工具会**全绿**。

本项只做能站得住的三件事：

1. 本机到底有没有**可用**的 IPv6 地址（区分链路本地 / Teredo / ULA / 全局）；
   一个都没有时直接说明"IPv6 相关故障不适用"，而不是硬报一个失败；
2. 有地址就 Ping 一个公共 v6 目标，回答"有 v6 地址却出不去"这种可疑状态；
3. 通了就顺带测 v6 的路径 MTU（复用 IPv4 那套二分探测），这正是群日志里那次故障的判据。

地址分类依据 ICU 官方手册：校园 IPv6 用的是 ``FC01::/48``（属 ULA 段），
因此 ULA 也算"可能是校园内网 v6"，放在与全局地址同一档处理。
"""

from __future__ import annotations

from ipaddress import IPv6Address, IPv6Network, ip_address
from typing import TYPE_CHECKING, Final

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.net.mtu import MAX_PROBE_PAYLOAD, MtuResult, probe_path_mtu
from hdu_netdebugger.net.ping import BackendUnavailable
from hdu_netdebugger.registry import checker

if TYPE_CHECKING:
    from hdu_netdebugger.models.ping import MultiPingResult

__all__ = ["IPv6Check"]

#: 用来判断"v6 到底能不能出去"的公共目标（阿里公共 DNS 的 v6 地址）。
V6_PROBE_TARGET: Final = "2400:3200::1"

#: 校园 IPv6 网段（ICU 官方手册 ``dormitory.html:144``：``FC01::/48``）。
_CAMPUS_V6: Final = IPv6Network("fc01::/48")


@checker("检查IPv6", order=38)
class IPv6Check(Checker):
    """判断本机 IPv6 的可用性，并在可用时测 v6 路径 MTU。"""

    async def check(self) -> None:
        usable, ignored = self._classify_addresses()
        self.log(
            f"本机 IPv6 地址：可用 {len(usable)} 个，其它 {ignored}（链路本地 / Teredo / 环回）"
        )
        for text in usable:
            marker = "（属校园 IPv6 网段）" if ip_address(text) in _CAMPUS_V6 else ""
            self.log(f"- {text}{marker}")

        if not usable:
            self.passed("本机没有可用的 IPv6 地址，IPv6 相关故障不适用")
            return

        result = await self._ping_target()
        if result is None:
            return

        if not result.success_count:
            self.add_warning(f"本机有 IPv6 地址，但访问公共 v6 目标 {V6_PROBE_TARGET} 不通")
            self.add_suggestion(
                "校园 IPv6 通常走独立的出口 / 隧道，地址在但出不去是常见状态；"
                "若你需要 IPv6，请把这条证据反馈给 ICU / 维护者"
            )
            self.warned("有 IPv6 地址但 v6 不通")
            return

        await self._report_path_mtu()
        self.passed(f"IPv6 可用（{V6_PROBE_TARGET} 可达）")

    async def _ping_target(self) -> MultiPingResult | None:
        """Ping 公共 v6 目标；系统 ping 不可用时标成跳过（而不是骗一个结论）。"""
        self.log(self.session.egress(V6_PROBE_TARGET).describe())
        try:
            result = await self.session.ping_v6(V6_PROBE_TARGET)
        except BackendUnavailable as exc:
            self.log(f"IPv6 探测不可用: {exc}")
            self.add_suggestion(
                "本机缺少可用的 IPv6 ping 命令（Windows/Linux 的 ping -6、macOS 的 ping6）"
            )
            self.warned(f"无法进行 IPv6 探测（{exc}）")
            return None

        self.log_ping_evidence(V6_PROBE_TARGET, result)
        return result

    async def _report_path_mtu(self) -> None:
        """v6 路径 MTU：群日志里那次故障就是 1420 通、1428 全丢。

        v6 没有 DF 这个概念（PMTUD 是强制的），所以这里不做 DF 探测，
        只用不同载荷找出"能过多大"的边界。
        """
        result: MtuResult = await probe_path_mtu(
            V6_PROBE_TARGET,
            lambda size: self.session.ping_size(V6_PROBE_TARGET, size),
        )
        self.log(f"IPv6 路径 MTU：{result.describe()}")
        self.log_lines(probe.describe() for probe in result.probes)

        if not result.reachable or not result.limited:
            return

        self.add_warning(
            f"IPv6 路径 MTU 受限：{result.max_payload} 字节以上不通（≈ MTU {result.path_mtu}）"
        )
        if result.silent_drop:
            self.add_suggestion(
                "大包被静默丢弃（收不到 ICMPv6 Type 2 之类回执）时，症状是"
                "「小包能过、大文件/视频卡住」；可把结论连同 MTU 边界一起反馈给维护者"
            )
        self.add_suggestion(
            f"参考：{MAX_PROBE_PAYLOAD} 字节是 IPv4 的常见上限，而 v6 上 1420 左右"
            "通常是隧道 / NAT66 的 MTU 所致"
        )

    def _classify_addresses(self) -> tuple[list[str], int]:
        """把本机 IPv6 地址分成"可用"与"忽略"两拨。

        忽略链路本地（``fe80::/10``）、Teredo（``2001:0::/32``）与环回：
        它们不代表真的能用 IPv6 出网。ULA（``fc00::/7``，含校园的 ``FC01::/48``）
        与全局地址一样算可用。
        """
        usable: list[str] = []
        ignored = 0
        for interface in self.session.interfaces():
            for text in interface.ipv6:
                try:
                    address = ip_address(text)
                except ValueError:  # pragma: no cover - 枚举层已过滤
                    ignored += 1
                    continue
                if not isinstance(address, IPv6Address):
                    ignored += 1
                    continue
                if address.is_loopback or address.is_link_local or address.is_unspecified:
                    ignored += 1
                    continue
                if str(address).lower().startswith("2001:0:"):
                    ignored += 1  # Teredo 隧道，不是真的 v6 连通性
                    continue
                if text not in usable:
                    usable.append(text)
        return usable, ignored
