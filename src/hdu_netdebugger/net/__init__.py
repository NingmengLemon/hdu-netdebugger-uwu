"""网络能力层：ICMP Ping、HTTP、DNS、网卡枚举."""

from __future__ import annotations

from hdu_netdebugger.net.dns import DnsResult, query_a
from hdu_netdebugger.net.http import HttpClient, HttpResponse
from hdu_netdebugger.net.ping import Backend, PingOptions, ping_multiple, resolve_target

__all__ = [
    "Backend",
    "DnsResult",
    "HttpClient",
    "HttpResponse",
    "PingOptions",
    "ping_multiple",
    "query_a",
    "resolve_target",
]
