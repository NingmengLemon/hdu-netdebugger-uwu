"""检查 6：认证状态.

对应原版 ``Checkers/AuthCheck.cs``：请求深澜的 ``rad_user_info``，
解析出在线设备、运营商与错误码；运营商 ID 会写进
:attr:`CheckContext.isp_id`，供「检查运营商网关可达性」使用。

与原版一致：DNS 可用时用域名请求，否则退回 IP 直连（此时 TLS 证书大概率不匹配，
可用 ``--insecure`` 关闭校验）。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.constants import HDU_PORTAL_DOMAIN, HDU_PORTAL_ROUTE, SRUN_IP
from hdu_netdebugger.models.srun import SrunAuthResponse, parse_auth_response
from hdu_netdebugger.registry import checker

if TYPE_CHECKING:
    from hdu_netdebugger.net.http import HttpResponse

__all__ = ["AuthCheck"]


@checker("检查认证状态", order=25)
class AuthCheck(Checker):
    """查询并解读深澜认证接口返回。"""

    async def check(self) -> None:
        host = self._resolve_host()
        https_url = f"https://{host}{HDU_PORTAL_ROUTE}"
        self.log(f"请求 URL: {https_url}")

        response = await self.session.http.get(https_url)
        self.log(f"HTTPS: {response.describe()}")

        if response.error is not None:
            response = await self._retry_over_plain_http(host, response)
        if response.error is not None:
            self.add_warning(f"认证请求失败: {response.error}")
            await self._explain_transport_failure()
            self.add_suggestion("请检查网络连通性，或联系ICU并提供错误信息")
            self.failed("认证状态查询失败")
            return

        await self._log_portal_page(host)

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

    async def _explain_transport_failure(self) -> None:
        """区分"网络层不通"与"网络层通、但 80/443 不通"。

        实测（教学区，2026-09）：到 `192.168.112.97` 的 ICMP 4/4 通、DNS 也解析正确，
        但 HTTPS 与明文 HTTP 都是 `ConnectError: All connection attempts failed` ——
        也就是**网络层可达、传输层被拦**。这种结论靠"认证请求失败"一句话是给不出来的，
        所以这里补一次 ICMP 探测来区分，并直接给出对应的排查方向。
        """
        probe = await self.session.ping(SRUN_IP)
        self.log(f"补充探测: 对 {SRUN_IP} 的 ICMP 成功 {probe.success_count}/{probe.total_count}")
        if not probe.success_count:
            self.add_suggestion(
                f"到 {SRUN_IP} 的 ICMP 也不通，属于网络层不可达：先看检查 4（深澜可达性）"
            )
            return

        self.add_warning(
            f"网络层可达但传输层不通：ICMP 到 {SRUN_IP} 正常、DNS 解析也正常，"
            "但 TCP 80 与 443 都连不上"
        )
        self.add_suggestion(
            "这种「ICMP 通、Web 端口不通」通常是所在网段到门户的 80/443 被策略拦下，"
            "或门户只在特定网段提供服务；请把这条结论（连同所在区域）反馈给 ICU"
        )

    async def _retry_over_plain_http(self, host: str, failed: HttpResponse) -> HttpResponse:
        """HTTPS 走不通时，用明文 HTTP 再试一次。

        校园认证接口同时提供明文入口（官方给出的认证地址就是 ``http://192.168.112.97``），
        所以 TLS / 代理 / 中间设备把 HTTPS 弄坏时，明文这条路往往仍然可用。实测明文
        比 HTTPS 快得多（19ms vs 800ms 的 TLS 握手）。这里只 GET 认证状态，不发送账号口令。
        """
        plain_url = f"http://{host}{HDU_PORTAL_ROUTE}"
        self.log(f"HTTPS 失败（{failed.error}），回退明文 HTTP 再试: {plain_url}")

        plain = await self.session.http.get(plain_url)
        self.log(f"HTTP: {plain.describe()}")
        if plain.error is not None:
            return failed

        self.add_warning("HTTPS 请求失败，本次改用明文 HTTP 完成认证状态查询")
        self.add_suggestion(
            "HTTPS 不通通常是 TLS / 证书 / 代理 / 中间设备的问题；"
            "若能上网但只有 HTTPS 异常，建议检查系统代理设置"
        )
        return plain

    async def _log_portal_page(self, host: str) -> None:
        """顺带记录门户首页的响应（证据，不参与判定）。

        未认证时门户首页会跳到登录页，已认证时会跳到管理页 —— 重定向目标本身就是
        一条有用线索，而这一步不依赖认证接口返回什么。
        """
        root = await self.session.http.get(f"http://{host}/")
        location = root.headers.get("location", "(无)")
        self.log(f"门户首页: {root.describe()}，重定向到 {location}")

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
        self._log_session_facts(payload)

    def _log_session_facts(self, payload: SrunAuthResponse) -> None:
        """会话时长、余量、拨号方式等"白拿"的诊断字段。

        这些字段解析器一直都在解析（对应原版的 ``SrunAuthResponse``），只是没人看。
        依据（钉钉群 334 条日志）："认证正常但上不了网"这一类里，配额 / 欠费 / 掉线
        以及"是不是路由器或别的设备在认证"是最常见的两种原因。

        **只打印非身份字段**：``user_name`` / ``user_mac`` / ``phone`` / ``email`` /
        ``real_name`` 一律不打印 —— 它们是个人信息，对诊断也没有增量价值。
        """
        self.log("会话与配额:")
        self.log(f"- 本次在线时长: {_elapsed_text(payload.add_time)}（服务端 add_time）")
        self.log(
            f"- 最近一次保活: {_elapsed_text(payload.keepalive_time)}前（服务端 keepalive_time）"
        )
        self.log(
            f"- 本次流量: 上行 {_megabytes(payload.bytes_out)} / 下行 {_megabytes(payload.bytes_in)}"
        )
        self.log(f"- 累计流量: {_megabytes(payload.sum_bytes)}")
        self.log(
            f"- 余量: {_megabytes(payload.remain_bytes)} / "
            f"{_duration_text(payload.remain_seconds)}"
            "（0 可能表示不限量，也可能表示已用尽，需向运营商确认）"
        )
        self.log(f"- 拨号方式: pppoe_dial={payload.pppoe_dial or '(未给出)'}")
        self.log(f"- 认证服务端版本: {payload.sysver or '(未给出)'}")
        self._log_device_vs_local(payload)

    def _log_device_vs_local(self, payload: SrunAuthResponse) -> None:
        """把在线设备地址与本机地址对照：认证的到底是本机还是路由器后面的别的设备。"""
        local = {
            address
            for interface in self.session.interfaces()
            for address in interface.ipv4
            if not address.startswith("169.254.")
        }
        device_ips = {device.ip for device in payload.active_devices.values() if device.ip}
        if not device_ips:
            return

        matched = sorted(device_ips & local)
        if matched:
            self.log(f"- 在线设备里包含本机地址: {', '.join(matched)}")
            return
        self.log(
            f"- 在线设备地址（{', '.join(sorted(device_ips))}）都不在本机网卡上："
            "本机可能位于路由器 / NAT 之后，或认证的是另一台设备"
        )


def _elapsed_text(stamp: int) -> str:
    """把服务端时间戳渲染成"距今多久"的时长（不含"前"字，措辞交给调用方）。"""
    if not stamp:
        return "(未给出)"
    seconds = int(time.time()) - stamp
    if seconds < 0 or seconds > 90 * 86400:
        # 与本机时钟差得离谱时不要硬算，直接给出原始值
        return f"(服务端时间戳 {stamp}，与本机时钟对不上)"
    return _duration_text(seconds)


def _duration_text(seconds: int) -> str:
    """把秒数渲染成中文时长。"""
    if seconds <= 0:
        return "0 秒"
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分"


def _megabytes(value: int) -> str:
    """把字节数渲染成 MB / GB。"""
    if value <= 0:
        return "0 字节"
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GB"
    return f"{value / 1024**2:.1f} MB"
