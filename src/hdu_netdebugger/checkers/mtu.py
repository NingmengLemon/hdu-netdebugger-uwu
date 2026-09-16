"""检查 9：路径 MTU.

覆盖"小包通、大包不通"这一类故障：``ping`` 默认只发 32 字节，所以"能 ping 通"完全
不代表大包能过。依据有两条：

* 钉钉群日志里唯一一条完整证据链就是这类问题（生活区 IPv6 PMTUD 黑洞：1420 通、
  **1428 全丢**、收不到 ICMPv6 Type 2），而当时 IPv4 一切正常、工具会全绿；
* 本机实测（宿舍路由器 + 寝室网上游，TUN 关闭）：到深澜认证服务器
  **996 字节以上全丢**，且从不返回 ICMP"需要分片" —— 而 BAS 与公网都正常 1500。

判定分三层：目标可达吗 → 1472 字节快筛 → 不通才二分找边界，并区分
"只是 MTU 偏小（收到过需要分片）"与"静默丢弃（黑洞形态）"。
"""

from __future__ import annotations

import asyncio
from typing import Final

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import ALI_DNS_IP, BAS_IP, SRUN_IP
from hdu_netdebugger.net.mtu import MAX_PROBE_PAYLOAD, MtuResult, probe_path_mtu
from hdu_netdebugger.net.ping import BackendUnavailable
from hdu_netdebugger.registry import checker

__all__ = ["MtuCheck"]

#: 探测目标：校园侧两个 + 公网一个做对照。
#:
#: 公网目标是"参照物"：只有对照才知道校园侧的边界是校园链路的问题还是本机的问题
#: （本机网卡 MTU 若异常，公网目标也一样会受限）。
_TARGETS: Final = (
    ("校园BAS", BAS_IP),
    ("深澜认证", SRUN_IP),
    ("公网对照", ALI_DNS_IP),
)


@checker("检查路径MTU", order=37)
class MtuCheck(Checker):
    """用 DF 探测校园与公网目标的路径 MTU。"""

    async def check(self) -> None:
        # 三个目标并发探测：失败尝试会等满超时，串行起来这一项能拖到十几秒，
        # 而它们互不依赖（各自独立的目标与探测）。
        outcomes = await asyncio.gather(*(self._probe(label, target) for label, target in _TARGETS))
        if any(outcome is None for outcome in outcomes):
            return  # 已经写成"跳过"，不必继续

        results: list[tuple[str, MtuResult]] = [
            (label, outcome)
            for (label, _target), outcome in zip(_TARGETS, outcomes, strict=True)
            if outcome is not None
        ]

        for label, result in results:
            self.log(f"[{label}] {result.describe()}")
            self.log_lines(probe.describe() for probe in result.probes)

        self._report(results)

    async def _probe(self, label: str, target: object) -> MtuResult | None:
        """探测单个目标；后端不支持 DF 时返回 ``None`` 并把检查标成跳过。"""
        egress = self.session.egress(target)
        self.log(f"[{label}] {egress.describe()}")
        if egress.is_tunnel_like:
            # 有 TUN 时这类测量会被隧道污染（实测校园目标 1472 全丢、996 通，那是隧道
            # 产物而不是校园真相），所以标注而不是沉默。
            self.add_warning(
                f"到 {target} 的探测经由隧道类接口出站：下面的路径 MTU 结果反映的是隧道侧，"
                "不代表校园链路"
            )

        try:
            return await probe_path_mtu(
                str(target),
                lambda size, item=target: self.session.ping_size(item, size, df=True),
            )
        except BackendUnavailable as exc:
            self.log(f"DF 探测不可用: {exc}")
            self.add_suggestion(
                "路径 MTU 探测需要能设置 DF 的后端（Windows 用 --ping-backend windows-icmp，"
                "其它平台用 system）"
            )
            self.warned(f"无法进行路径 MTU 探测（{exc}）")
            return None

    def _report(self, results: list[tuple[str, MtuResult]]) -> None:
        """汇总：区分"只是 MTU 偏小"与"静默丢弃"，并给出可行动的处置。"""
        unreachable = [label for label, item in results if not item.reachable]
        limited = [(label, item) for label, item in results if item.limited]
        silent = [(label, item) for label, item in limited if item.silent_drop]

        if not limited:
            if unreachable:
                self.warned(f"未发现路径 MTU 受限（{len(unreachable)} 个目标不可达，未参与判定）")
                return
            self.passed(
                f"未发现路径 MTU 问题（{len(results)} 个目标都能通过 {MAX_PROBE_PAYLOAD} 字节）"
            )
            return

        for label, item in limited:
            payload = item.max_payload
            if item.frag_needed_seen:
                self.add_warning(
                    f"{label} 的路径 MTU 偏小：{payload} 字节以上不通"
                    f"（≈ MTU {item.path_mtu}），中间设备有回 ICMP 需要分片"
                )
            else:
                self.add_warning(
                    f"{label} 的路径 MTU 偏小且**静默丢弃**：{payload} 字节以上不通"
                    f"（≈ MTU {item.path_mtu}），全程没有 ICMP 需要分片 —— MTU 黑洞形态"
                )

        if silent:
            self.add_suggestion(
                "大包被静默丢弃时，症状通常是「能上 QQ / ping 通，但网页、下载、视频卡住」；"
                "可先关掉 VPN / 代理再测一次，确认不是隧道或代理造成的"
            )
        self.add_suggestion(
            "若这是校园链路的问题（公网对照目标正常、校园目标受限），请把结论反馈给 ICU / 维护者："
            "本工具只能给出边界，改不了中间的设备"
        )
        self.warned(f"检测到路径 MTU 受限（{len(limited)} 个目标）")
