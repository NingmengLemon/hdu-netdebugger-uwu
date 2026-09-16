"""检查 5：DNS 可用性.

对应原版 ``Checkers/DNSCheck.cs``：向杭电 DNS 查询 ``portal.hdu.edu.cn``，
并校验返回的 A 记录里确实包含深澜认证服务器的地址 —— 只"能解析"不够，
解析结果正确才算通过。结果会写进 :attr:`CheckContext.dns_available`，
供「检查认证状态」决定用域名还是 IP 直连。

本复刻在此之上补了两件原版没有的事，因为校园网里这两类问题都很常见：

* **本机解析器与校园 DNS 不一致**：能连上认证服务器的 IP、却打不开 portal 域名，
  通常就是本机 DNS 的问题（官方排查说明给的标准处方就是"把 DNS 改成校园 DNS"）；
  因此这里除了问校园 DNS，也问一次**系统配置的**解析器，并列出系统到底配了哪些 DNS。
* **本机 DNS 被接管**：装了代理 / TUN 的机器会把 UDP/53 拦下来自己应答，
  于是"校园 DNS 可用"这个结论**不可能失败** —— 是假阳性。向一个不可路由的地址
  （RFC 5737 TEST-NET-1）发同样的查询就能测出来：它居然答得出来，就说明被接管了。
  实测本机正是如此（向 192.0.2.53 查询同样返回 192.168.112.97，TTL 与正规查询一致）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import (
    DNS_INTERCEPTION_PROBE_IP,
    DNS_INTERCEPTION_TIMEOUT_S,
    HDU_DNS_IP,
    HDU_PORTAL_DOMAIN,
    SRUN_IP,
)
from hdu_netdebugger.net.dns import DnsResult, query_a, query_a_system
from hdu_netdebugger.registry import checker

if TYPE_CHECKING:
    from ipaddress import IPv4Address

__all__ = ["DNSCheck"]


@checker("检查DNS可用性", order=20)
class DNSCheck(Checker):
    """校验校园网 DNS 的解析结果，并对比本机自己的解析情况。"""

    async def check(self) -> None:
        server = self.config.dns_server or HDU_DNS_IP
        self.context.dns_available = False

        self._log_configured_servers(server)

        result = await query_a(HDU_PORTAL_DOMAIN, server, timeout_s=self.config.dns_timeout_s)
        self._log_query(f"校园 DNS {server}", result)

        system = await self._query_system()
        campus_ok = result.error is None and result.addresses and result.contains(SRUN_IP)
        if campus_ok or (system is not None and system.contains(SRUN_IP)):
            # 「认证状态」要的是"能不能用域名请求"，所以只要有一边解析正确就够了
            self.context.dns_available = True

        await self._check_interception()

        if campus_ok:
            self.passed("DNS 查询成功，返回了正确的 A 记录")
            return

        if result.error is not None:
            self.add_warning("DNS 查询失败")
            self.add_suggestion(f"请检查DNS服务器设置，建议把DNS服务器设置为 {server}")
            self.failed("DNS 查询失败")
            return

        if not result.addresses:
            self.add_warning("DNS 查询未返回任何结果")
            self.add_suggestion(f"请检查DNS服务器设置，建议把DNS服务器设置为 {server}")
            self.failed("DNS 查询未返回结果")
            return

        self.add_warning("DNS 查询未返回正确的记录")
        self.add_suggestion(f"请检查DNS服务器设置，建议把DNS服务器设置为 {server}")
        self.failed("DNS 查询未返回正确的记录")

    # -- 证据收集 ---------------------------------------------------------

    def _log_configured_servers(self, server: IPv4Address) -> None:
        """列出系统配置的 DNS，并说明本次查询的目标是不是其中之一。"""
        configured = sorted(
            {item for interface in self.session.interfaces() for item in interface.dns_servers}
        )
        self.log(f"系统配置的 DNS 服务器: {', '.join(configured) or '(无)'}")
        self.log(f"本次查询使用的 DNS 服务器: {server}")
        if configured and str(server) not in configured:
            self.log(f"（注意：{server} 并不在本机配置的 DNS 列表里，是按校园网默认值查询的）")

    def _log_query(self, label: str, result: DnsResult) -> None:
        """把一次查询的结果写进详细日志。"""
        if result.error is not None:
            self.log(f"{label} 查询失败: {result.error}")
            return
        self.log(f"{label} 返回 {len(result.addresses)} 条 A 记录:")
        self.log_lines(result.answer_lines)

    async def _query_system(self) -> DnsResult | None:
        """用系统配置的解析器查一次，并给出可操作的结论。"""
        try:
            system = await query_a_system(HDU_PORTAL_DOMAIN, timeout_s=self.config.dns_timeout_s)
        except Exception as exc:  # 只是补充证据，不能因此判失败
            self.log(f"系统解析器查询异常: {exc!r}")
            return None

        self._log_query(f"系统解析器（{system.server}）", system)

        if system.contains(SRUN_IP):
            return system

        self.add_warning("本机 DNS 解析结果不可用")
        if system.error is not None:
            self.add_suggestion(
                f"本机 DNS 解析不了 {HDU_PORTAL_DOMAIN}（{system.error}）："
                f"建议把系统 DNS 改为校园 DNS {HDU_DNS_IP}"
            )
        else:
            self.add_suggestion(
                f"本机 DNS 把 {HDU_PORTAL_DOMAIN} 解析成了 {', '.join(system.addresses) or '(空)'}，"
                f"不是认证服务器 {SRUN_IP}；建议把系统 DNS 改为校园 DNS {HDU_DNS_IP}"
            )
        self.add_suggestion(
            f"临时验证可以用 hosts 映射：{SRUN_IP} {HDU_PORTAL_DOMAIN}（验证完请撤掉）"
        )
        return system

    async def _check_interception(self) -> None:
        """向不可路由地址发同样的查询，用来判断本机 DNS 是否被接管。"""
        probe = await query_a(
            HDU_PORTAL_DOMAIN,
            DNS_INTERCEPTION_PROBE_IP,
            timeout_s=DNS_INTERCEPTION_TIMEOUT_S,
        )
        if probe.error is not None or not probe.addresses:
            self.log(f"DNS 接管检测: 向不可路由地址 {DNS_INTERCEPTION_PROBE_IP} 查询无应答（正常）")
            return

        self.add_warning(
            f"本机 DNS 查询被接管：向不可路由的 {DNS_INTERCEPTION_PROBE_IP} 查询也返回了 "
            f"{', '.join(probe.addresses)}，说明 UDP/53 被本地拦下自行应答（代理 / TUN / 本地 DNS）"
        )
        self.add_suggestion(
            "本次「DNS 可用」只说明本机能解析该域名，不代表校园 DNS 正常；"
            "要真正检查校园 DNS，请关掉代理 / TUN 的 DNS 接管后重跑"
        )
