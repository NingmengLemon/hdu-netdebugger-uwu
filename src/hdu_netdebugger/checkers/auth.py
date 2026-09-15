"""检查 6：认证状态.

对应原版 ``Checkers/AuthCheck.cs``：请求深澜的 ``rad_user_info``，
解析出在线设备、运营商与错误码；运营商 ID 会写进
:attr:`CheckContext.isp_id`，供「检查运营商网关可达性」使用。

与原版一致：DNS 可用时用域名请求，否则退回 IP 直连（此时 TLS 证书大概率不匹配，
可用 ``--insecure`` 关闭校验）。
"""

from __future__ import annotations

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import HDU_PORTAL_DOMAIN, HDU_PORTAL_ROUTE, SRUN_IP
from hdu_netdebugger.models.srun import SrunAuthResponse, parse_auth_response
from hdu_netdebugger.registry import checker

__all__ = ["AuthCheck"]


@checker("检查认证状态", order=25)
class AuthCheck(Checker):
    """查询并解读深澜认证接口返回。"""

    async def check(self) -> None:
        host = self._resolve_host()
        url = f"https://{host}{HDU_PORTAL_ROUTE}"
        self.log(f"请求 URL: {url}")

        response = await self.session.http.get(url)
        if response.error is not None:
            self.add_warning(f"认证请求失败: {response.error}")
            self.add_suggestion("请检查网络连通性，或联系ICU并提供错误信息")
            self.failed("认证状态查询失败")
            return

        payload = parse_auth_response(response.text)
        if payload is None:
            self.add_warning("无法解析认证响应")
            self.add_suggestion("请联系ICU并提供错误信息")
            self.log(f"响应原文为: {response.text[:2000]}")
            self.failed("认证状态查询失败")
            return

        self._record_isp(payload)
        self._log_payload(payload)

        if payload.is_ok:
            self.passed("认证状态正常")
            return

        if payload.is_not_online:
            self.add_warning("当前未认证")
            self.add_suggestion(f"请前往认证页面进行认证，网址为 https://{HDU_PORTAL_DOMAIN}")
            self.failed("未认证")
            return

        self.add_warning(f"认证错误: {payload.error}")
        self.add_suggestion(f"请尝试重新认证，网址为 https://{HDU_PORTAL_DOMAIN}")
        self.add_suggestion("如问题依旧，请联系ICU并提供错误信息")
        self.failed("认证状态异常")

    def _resolve_host(self) -> str:
        """决定认证接口的 host。"""
        if self.config.auth_host:
            self.log("使用命令行指定的 host 进行认证状态请求。")
            return self.config.auth_host
        if self.context.dns_available:
            self.log("DNS 可用，使用域名进行认证状态请求。")
            return HDU_PORTAL_DOMAIN
        self.log("DNS 不可用，使用 IP 地址进行认证状态请求。")
        return str(SRUN_IP)

    def _record_isp(self, payload: SrunAuthResponse) -> None:
        """把运营商信息写入共享上下文。"""
        self.context.raw_products_id = payload.products_id
        self.context.isp_id = payload.isp_id

    def _log_payload(self, payload: SrunAuthResponse) -> None:
        """输出认证响应摘要。"""
        devices = payload.active_devices

        self.log("认证响应内容:")
        self.log(f"运营商: {payload.billing_name}")
        self.log(f"认证方式: {payload.domain}")
        self.log(f"错误信息: {payload.error}")
        self.log(f"在线设备数量: {payload.online_device_total}")
        self.log(f"在线设备(非空)数量: {len(devices)}")
        self.log("在线设备详情:")
        for key, device in devices.items():
            self.log(f"id: {key}")
            self.log(f"- 设备IP: {device.ip}")
            self.log(f"- 设备IPv6: {device.ip6}")
            self.log(f"- 设备系统: {device.os_name}")
            self.log()
