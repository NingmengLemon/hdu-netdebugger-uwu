"""DNS 查询封装。

对应 C# 原版 ``DNSCheck`` 里的 ``DnsClient.LookupClient(HDUDnsIp)``：
**显式指定**杭电 DNS 服务器，而不是走系统解析器 —— 这样才能区分
"校园网 DNS 坏了"和"本机 DNS 配置错了"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver

if TYPE_CHECKING:
    from ipaddress import IPv4Address

__all__ = ["DnsResult", "query_a"]


@dataclass(slots=True)
class DnsResult:
    """一次 A 记录查询的结果。"""

    domain: str
    server: str
    #: 查询到的全部 A 记录（字符串形式，按响应顺序）
    addresses: list[str] = field(default_factory=list)
    #: 完整应答文本，用于详细日志
    answer_lines: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """查询是否成功且有结果。"""
        return self.error is None and bool(self.addresses)

    def contains(self, address: IPv4Address | str) -> bool:
        """应答中是否包含指定地址。"""
        wanted = str(address)
        return any(item == wanted for item in self.addresses)

    def describe(self) -> str:
        """渲染成简短的一行描述。"""
        if self.error is not None:
            return f"DNS 查询 {self.domain} 失败: {self.error}"
        return f"DNS 查询 {self.domain} 返回 {len(self.addresses)} 条 A 记录"


async def query_a(
    domain: str,
    server: IPv4Address | str,
    *,
    timeout_s: float = 5.0,
) -> DnsResult:
    """向指定 DNS 服务器查询 ``domain`` 的 A 记录。

    所有 ``dnspython`` 异常都会被收敛成 :attr:`DnsResult.error`。
    """
    server_text = str(server)
    result = DnsResult(domain=domain, server=server_text)

    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = [server_text]
    resolver.lifetime = timeout_s
    resolver.timeout = min(timeout_s, 2.0)

    try:
        answer = await resolver.resolve(domain, dns.rdatatype.A)
    except dns.resolver.NXDOMAIN:
        result.error = f"域名 {domain} 不存在 (NXDOMAIN)"
        return result
    except dns.resolver.NoNameservers as exc:
        result.error = f"没有可用的 DNS 服务器: {exc}"
        return result
    except dns.resolver.NoAnswer:
        result.error = f"{domain} 没有 A 记录"
        return result
    except dns.exception.Timeout:
        result.error = f"查询超时（{timeout_s:g}s）"
        return result
    except dns.exception.DNSException as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    for record in answer:
        result.answer_lines.append(f"{record.name} {record.ttl} IN A {record.address}")
        result.addresses.append(str(record.address))
    return result
