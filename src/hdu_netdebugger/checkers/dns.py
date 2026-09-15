"""检查 5：DNS 可用性.

对应原版 ``Checkers/DNSCheck.cs``。向杭电 DNS 查询 ``portal.hdu.edu.cn``，
并校验返回的 A 记录中确实包含深澜认证服务器的地址 —— 只"能解析"不够，
解析结果正确才算通过。结果会写进 :attr:`CheckContext.dns_available`，
供「检查认证状态」决定用域名还是 IP 直连。
"""

from __future__ import annotations

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import HDU_DNS_IP, HDU_PORTAL_DOMAIN, SRUN_IP
from hdu_netdebugger.net.dns import query_a
from hdu_netdebugger.registry import checker

__all__ = ["DNSCheck"]


@checker("检查DNS可用性", order=20)
class DNSCheck(Checker):
    """校验校园网 DNS 的解析结果。"""

    async def check(self) -> None:
        server = self.config.dns_server or HDU_DNS_IP
        self.context.dns_available = False

        result = await query_a(
            HDU_PORTAL_DOMAIN,
            server,
            timeout_s=self.config.dns_timeout_s,
        )

        if result.error is not None:
            self.add_warning("DNS 查询失败")
            self.log(f"DNS 查询失败，错误信息: {result.error}")
            self.add_suggestion(f"请检查DNS服务器设置，建议把DNS服务器设置为 {server}")
            self.failed("DNS 查询失败")
            return

        if not result.addresses:
            self.add_warning("DNS 查询未返回任何结果")
            self.add_suggestion(f"请检查DNS服务器设置，建议把DNS服务器设置为 {server}")
            self.failed("DNS 查询未返回结果")
            return

        self.log("DNS 查询成功，返回结果如下：")
        self.log_lines(result.answer_lines)

        if result.contains(SRUN_IP):
            self.context.dns_available = True
            self.passed("DNS 查询成功，返回了正确的 A 记录")
            return

        self.add_warning("DNS 查询未返回正确的记录")
        self.add_suggestion(f"请检查DNS服务器设置，建议把DNS服务器设置为 {server}")
        self.failed("DNS 查询未返回正确的记录")
