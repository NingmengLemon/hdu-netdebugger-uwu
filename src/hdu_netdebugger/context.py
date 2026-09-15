"""检查项之间的共享状态.

C# 原版用 ``ConcurrentDictionary<string, object> GlobalFlagList`` 传递状态，
键是魔法字符串、值需要运行时类型转换。这里换成强类型 dataclass：
拼错字段名会被类型检查器拦下，读取也不需要强转。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hdu_netdebugger.constants import NetworkKind, SrunProductId
    from hdu_netdebugger.models.interface import InterfaceInfo

__all__ = ["CheckContext"]


@dataclass(slots=True)
class CheckContext:
    """一次完整检查运行内的跨检查项状态。"""

    #: 「检查DNS可用性」写入：DNS 是否返回了正确的 A 记录
    dns_available: bool = False
    #: 「获取基本信息」写入：当前所处的网络区域
    network_kind: NetworkKind | None = None
    #: 「获取基本信息」写入：是否探测到 VPN / 隧道类接口
    has_tunnel_interface: bool = False
    #: 「获取基本信息」写入：本次枚举到的接口快照
    interfaces: list[InterfaceInfo] = field(default_factory=list)
    #: 「检查认证状态」写入：解析出的运营商 ID
    isp_id: SrunProductId | None = None
    #: 「检查认证状态」写入：原始响应中未经过滤的 products_id
    raw_products_id: str = ""

    def reset(self) -> None:
        """清空所有状态，供下一轮运行复用。"""
        self.dns_available = False
        self.network_kind = None
        self.has_tunnel_interface = False
        self.interfaces.clear()
        self.isp_id = None
        self.raw_products_id = ""
