"""杭电校园网相关的硬编码常量.

对应 C# 原版的 ``Models/HDUConst.cs`` 与 ``Models/GenericConst.cs``。
所有数值集中在此处，方便校园网调整拓扑后统一修改。
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from ipaddress import IPv4Address, IPv4Network
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ALI_DNS_IP",
    "BAS_IP",
    "CAMPUS_NETWORK",
    "DEFAULT_PING_COUNT",
    "DNS_INTERCEPTION_PROBE_IP",
    "DNS_INTERCEPTION_TIMEOUT_S",
    "DORM_NETWORK",
    "EDUROAM_NETWORK",
    "HDU_DNS_IP",
    "HDU_PORTAL_DOMAIN",
    "HDU_PORTAL_ROUTE",
    "LINK_LOCAL_NETWORK",
    "MIUI_ROM_204_URL",
    "PING_INTERVAL_S",
    "PING_PAYLOAD_SIZE",
    "PING_TIMEOUT_S",
    "PING_TTL",
    "PUBLIC_ANCHOR_IP",
    "ROUTER_PRIVATE_NETWORK",
    "SRUN_IP",
    "SRUN_PRODUCT_GATEWAYS",
    "SRUN_PRODUCT_NAMES",
    "UNKNOWN_CAMPUS_NETWORK",
    "NetworkKind",
    "SrunErrCode",
    "SrunProductId",
    "abnormal_network_note",
    "dorm_or_campus",
]


class NetworkKind(StrEnum):
    """当前所处的杭电网络区域。"""

    DORM = "Dorm"
    CAMPUS = "Campus"
    EDUROAM = "Eduroam"

    @property
    def label(self) -> str:
        """中文显示名。"""
        return _NETWORK_LABELS[self]


_NETWORK_LABELS: dict[NetworkKind, str] = {
    NetworkKind.DORM: "生活区网络",
    NetworkKind.CAMPUS: "教学区网络",
    NetworkKind.EDUROAM: "eduroam 漫游网络",
}


# ---------------------------------------------------------------------------
# 校园网拓扑
# ---------------------------------------------------------------------------

#: 生活区网段
#:
#: 与 C# 原版 ``HDUConst.DormNetwork`` 保持一致。校园网拓扑会调整，而群里流传的
#: 说明文档常常是旧的 —— **以官方（ICU）口径为准**：判定不中时检查 1 会把实际
#: 地址 / 网关 / DNS 和这两个网段一起打出来，便于人工核对后再改这里。
DORM_NETWORK: Final = IPv4Network("10.150.0.0/16")
#: 教学区（校园网）网段
CAMPUS_NETWORK: Final = IPv4Network("10.249.0.0/16")
#: eduroam 漫游网段。
#:
#: 来源：ICU 官方手册 ``campus.html:31``（``references/icu_manual``，v0.0.17 / 2025-04）。
#: 它既不是生活区也不是教学区，原版会把它判成"未检测到校园网地址" —— 那是假阴性。
EDUROAM_NETWORK: Final = IPv4Network("10.252.0.0/16")
#: DHCP 没拿到地址时 Windows 给的自动私有地址段。
#:
#: 官方手册把它列为最突出的一类故障（``dormitory.html:163``），而原来的判定只会说一句
#: "未检测到校园网地址"，把"DHCP 根本没成"这个关键事实埋掉了。
LINK_LOCAL_NETWORK: Final = IPv4Network("169.254.0.0/16")
#: 家用路由器自己下发的私网段（手册 ``dormitory.html:198`` 列为异常）。
ROUTER_PRIVATE_NETWORK: Final = IPv4Network("192.168.0.0/16")
#: 手册 ``dormitory.html:199`` 列为异常的 ``10.230.x.x``。
UNKNOWN_CAMPUS_NETWORK: Final = IPv4Network("10.230.0.0/16")
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

#: 公网可达性交叉验证用的第二个锚点（腾讯 DNSPod，与阿里 DNS 异构）。
#:
#: 依据（钉钉"网络问题交流群"日志）：群里明确讨论过 `223.5.5.5` 本身也会被劫持 /
#: 限速，只用一个锚点既可能假通也可能假不通，所以要拿第二个异构目标对一下。
#:
#: 选它是**实测**的结果（2026-09-16 本机，ping 2 次）：`223.5.5.5` 2/2、
#: `119.29.29.29` 2/2、`180.76.76.76` 2/2、`1.1.1.1` 2/2，而
#: **`114.114.114.114` 0/2（ICMP 被过滤）** —— 一开始我选的正是它，
#: 那样每次运行都会误报"锚点不一致"，所以换成实测应答的腾讯 DNS。
PUBLIC_ANCHOR_IP: Final = IPv4Address("119.29.29.29")

#: DNS 接管检测用的"不可路由"地址（RFC 5737 TEST-NET-1）。
#:
#: 正常情况下向它发查询只会超时；如果它居然答出了 portal.hdu.edu.cn 的 A 记录，
#: 说明本机的 UDP/53 被本地接管了（TUN 的 fake-IP、dnsmasq、代理的 DNS 劫持…），
#: 此时"校园 DNS 可用"这个结论根本不成立 —— 实测本机就是这样：向 192.0.2.53
#: 查询同样返回了 192.168.112.97，且 TTL 与正规查询完全一致。
DNS_INTERCEPTION_PROBE_IP: Final = IPv4Address("192.0.2.53")

#: 接管检测的超时（秒）。这个数字是"每次 DNS 检查要多花多久"的直接成本，
#: 宁可短一点：被接管时会立刻答，超时即表示没有接管。
DNS_INTERCEPTION_TIMEOUT_S: Final = 1.0


def dorm_or_campus(address: IPv4Address) -> NetworkKind | None:
    """判断地址属于哪个杭电网段（生活区 / 教学区 / eduroam）。

    Returns:
        对应的 :class:`NetworkKind`；都不匹配时返回 ``None``。
    """
    if address in DORM_NETWORK:
        return NetworkKind.DORM
    if address in CAMPUS_NETWORK:
        return NetworkKind.CAMPUS
    if address in EDUROAM_NETWORK:
        return NetworkKind.EDUROAM
    return None


def abnormal_network_note(address: IPv4Address) -> str | None:
    """地址落在"异常网段"时给一句人能直接行动的说明。

    这几段来自 ICU 官方手册（``dormitory.html`` 的 :163 / :198 / :199）：它们都不是
    校园网地址，但各有明确含义 —— 光说"未检测到校园网地址"会把最关键的事实埋掉。
    """
    if address in LINK_LOCAL_NETWORK:
        return f"{address} 是自动私有地址（169.254/16）：DHCP 没有拿到地址"
    if address in ROUTER_PRIVATE_NETWORK:
        return f"{address} 是路由器下发的私网地址（192.168/16）：流量没进校园网"
    if address in UNKNOWN_CAMPUS_NETWORK:
        return f"{address} 落在 10.230/16：官方手册把这归为异常网段"
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

#: 运营商 ID → **候选**出口网关（按可信度排序，逐个探测，谁应答就用谁）
#:
#: 为什么不写死一个值：原版给的三个地址里有**两个与 ICU 官方手册冲突** ——
#: 手册（``HDU-ICU/manual-dist`` v0.0.17，2025-04）说电信 BRAS 是 ``183.157.176.1``、
#: 联通是 ``221.12.1.227``，而 ``60.176.40.1`` / ``172.20.64.1`` 在手册里属于
#: "出口公网网段"，看起来是把**网段**当成了网关。手册本身也停在 2025-04（比这些
#: 参考资料新一个版本周期，但同样不足以当现状依据），所以两边都不该被当成唯一真相：
#: 这里把候选都列出来挨个 ping，**谁应答就是谁**，并把实际应答的地址打进日志。
#:
#: 另外手册把联通 ``221.12.1.227`` 同时标成"城域网 DNS"（自相矛盾），
#: 这也是不能只信单一来源的理由。
SRUN_PRODUCT_GATEWAYS: Final[Mapping[SrunProductId, tuple[IPv4Address, ...]]] = MappingProxyType(
    {
        SrunProductId.CHINA_UNICOM: (
            IPv4Address("221.12.1.227"),
            IPv4Address("172.20.64.1"),
        ),
        SrunProductId.CHINA_TELECOM: (
            IPv4Address("183.157.176.1"),
            IPv4Address("60.176.40.1"),
        ),
        SrunProductId.CHINA_MOBILE: (IPv4Address("117.147.121.1"),),
    }
)
