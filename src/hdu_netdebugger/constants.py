"""杭电校园网相关的硬编码常量.

对应 C# 原版的 ``Models/HDUConst.cs`` 与 ``Models/GenericConst.cs``。
所有数值集中在此处，方便校园网调整拓扑后统一修改。
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from ipaddress import IPv4Address, IPv4Network
from types import MappingProxyType
from typing import Final

__all__ = [
    "ALI_DNS_IP",
    "BAS_IP",
    "CAMPUS_NETWORK",
    "DEFAULT_PING_COUNT",
    "DORM_NETWORK",
    "HDU_DNS_IP",
    "HDU_PORTAL_DOMAIN",
    "HDU_PORTAL_ROUTE",
    "MIUI_ROM_204_URL",
    "PING_INTERVAL_S",
    "PING_PAYLOAD_SIZE",
    "PING_TIMEOUT_S",
    "PING_TTL",
    "SRUN_IP",
    "SRUN_PRODUCT_GATEWAYS",
    "SRUN_PRODUCT_NAMES",
    "NetworkKind",
    "SrunErrCode",
    "SrunProductId",
    "dorm_or_campus",
]


class NetworkKind(StrEnum):
    """当前所处的杭电网络区域。"""

    DORM = "Dorm"
    CAMPUS = "Campus"

    @property
    def label(self) -> str:
        """中文显示名。"""
        return "生活区网络" if self is NetworkKind.DORM else "教学区网络"


# ---------------------------------------------------------------------------
# 校园网拓扑
# ---------------------------------------------------------------------------

#: 生活区网段
DORM_NETWORK: Final = IPv4Network("10.150.0.0/16")
#: 教学区（校园网）网段
CAMPUS_NETWORK: Final = IPv4Network("10.249.0.0/16")
#: 生活区宽带接入服务器
BAS_IP: Final = IPv4Address("10.150.0.1")
#: 深澜认证服务器
SRUN_IP: Final = IPv4Address("192.168.112.97")
#: 杭电生活区认证门户域名
HDU_PORTAL_DOMAIN: Final = "portal.hdu.edu.cn"
#: 认证状态查询路由
HDU_PORTAL_ROUTE: Final = "/cgi-bin/rad_user_info?callback=jQuery"
#: 杭电 DNS
HDU_DNS_IP: Final = IPv4Address("210.32.32.1")


def dorm_or_campus(address: IPv4Address) -> NetworkKind | None:
    """判断地址属于生活区还是教学区网段。

    Returns:
        :attr:`NetworkKind.DORM` / :attr:`NetworkKind.CAMPUS`；都不匹配时返回 ``None``。
    """
    if address in DORM_NETWORK:
        return NetworkKind.DORM
    if address in CAMPUS_NETWORK:
        return NetworkKind.CAMPUS
    return None


# ---------------------------------------------------------------------------
# Ping 参数（对应 GenericConst.cs）
# ---------------------------------------------------------------------------

#: 单次 Ping 超时时间（秒）
PING_TIMEOUT_S: Final = 1.0
#: ICMP 载荷大小（字节）
PING_PAYLOAD_SIZE: Final = 32
#: 默认 Ping 次数
DEFAULT_PING_COUNT: Final = 4
#: 生存时间
PING_TTL: Final = 64
#: 两次 Ping 之间的间隔（秒）
PING_INTERVAL_S: Final = 0.2


# ---------------------------------------------------------------------------
# 公网常用地址
# ---------------------------------------------------------------------------

#: MIUI 的 generate_204 探针，正常返回 204 No Content
MIUI_ROM_204_URL: Final = "http://connect.rom.miui.com/generate_204"
#: 阿里公共 DNS
ALI_DNS_IP: Final = IPv4Address("223.5.5.5")


# ---------------------------------------------------------------------------
# 深澜认证
# ---------------------------------------------------------------------------


class SrunErrCode(StrEnum):
    """深澜 ``rad_user_info`` 返回的 ``error`` 字段（注意：并非全集）。"""

    OK = "ok"
    NOT_ONLINE = "not_online_error"


class SrunProductId(IntEnum):
    """深澜的运营商 ID。"""

    CHINA_UNICOM = 3
    CHINA_TELECOM = 4
    CHINA_MOBILE = 5


#: 运营商 ID → 中文名
SRUN_PRODUCT_NAMES: Final = MappingProxyType(
    {
        SrunProductId.CHINA_UNICOM: "中国联通",
        SrunProductId.CHINA_TELECOM: "中国电信",
        SrunProductId.CHINA_MOBILE: "中国移动",
    }
)

#: 运营商 ID → 出口网关 IP
#:
#: 移动网关本身 ping 不通，因此改用 BRAS 上的固定 IP。
SRUN_PRODUCT_GATEWAYS: Final = MappingProxyType(
    {
        SrunProductId.CHINA_UNICOM: IPv4Address("172.20.64.1"),
        SrunProductId.CHINA_TELECOM: IPv4Address("60.176.40.1"),
        SrunProductId.CHINA_MOBILE: IPv4Address("117.147.121.1"),
    }
)
