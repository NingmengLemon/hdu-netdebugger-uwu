"""一次检查运行的配置."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hdu_netdebugger.constants import (
    DEFAULT_PING_COUNT,
    HDU_DNS_IP,
    PING_INTERVAL_S,
    PING_PAYLOAD_SIZE,
    PING_TIMEOUT_S,
    PING_TTL,
)

if TYPE_CHECKING:
    from ipaddress import IPv4Address

__all__ = ["RunConfig"]


@dataclass(slots=True)
class RunConfig:
    """可调的运行参数，由 CLI 参数或 GUI 设置构造。"""

    #: 每个目标发送的 ICMP Echo 次数
    ping_count: int = DEFAULT_PING_COUNT
    #: 单次 ICMP Echo 超时（秒）
    ping_timeout_s: float = PING_TIMEOUT_S
    #: ICMP 载荷大小（字节）
    ping_payload_size: int = PING_PAYLOAD_SIZE
    #: ICMP 生存时间
    ping_ttl: int = PING_TTL
    #: 两次 ICMP Echo 之间的间隔（秒）
    ping_interval_s: float = PING_INTERVAL_S
    #: HTTP 请求超时（秒）
    http_timeout_s: float = 5.0
    #: DNS 查询超时（秒）
    dns_timeout_s: float = 5.0
    #: 查询 ``portal.hdu.edu.cn`` 使用的 DNS 服务器
    dns_server: IPv4Address = HDU_DNS_IP
    #: 是否校验 TLS 证书（校园网自签证书场景可关闭）
    verify_tls: bool = True
    #: 是否沿用 ``HTTP_PROXY`` / ``HTTPS_PROXY`` 环境变量
    trust_env_proxy: bool = True
    #: 强制指定认证接口的 host（留空表示按 DNS 结果自动选择）
    auth_host: str | None = None

    def __post_init__(self) -> None:
        if self.ping_count < 1:
            raise ValueError("ping_count 必须 >= 1")
        if self.ping_timeout_s <= 0:
            raise ValueError("ping_timeout_s 必须 > 0")
