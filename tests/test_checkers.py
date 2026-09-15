"""各个检查项的行为（全部走假网络，不产生真实流量）。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from hdu_netdebugger.checkers.auth import AuthCheck
from hdu_netdebugger.checkers.bas import BASCheck
from hdu_netdebugger.checkers.dns import DNSCheck
from hdu_netdebugger.checkers.get_info import GetInfoCheck
from hdu_netdebugger.checkers.isp import ISPCheck
from hdu_netdebugger.checkers.proxy import (
    ProxyCheck,
    ProxyHit,
    _is_loopback_proxy,
    detect_env_proxy,
    detect_system_proxy,
)
from hdu_netdebugger.checkers.public_net import PublicNetCheck
from hdu_netdebugger.checkers.srun_reachability import SrunReachabilityCheck
from hdu_netdebugger.constants import (
    BAS_IP,
    HDU_PORTAL_DOMAIN,
    MIUI_ROM_204_URL,
    SRUN_IP,
    NetworkKind,
    SrunProductId,
)
from hdu_netdebugger.context import CheckContext
from hdu_netdebugger.models.enums import CheckResultStatus
from hdu_netdebugger.models.interface import InterfaceInfo, InterfaceKind
from hdu_netdebugger.net.dns import DnsResult
from tests.fakes import FakeHttp, FakePinger, srun_jsonp

if TYPE_CHECKING:
    from hdu_netdebugger.models.result import CheckResult
    from hdu_netdebugger.session import RunSession

PROXY_URL = "http://127.0.0.1:7890"


async def run_check(checker_type: type, session: RunSession) -> CheckResult:
    """执行一个检查项并返回结果。"""
    return await checker_type(session).execute()


# ---------------------------------------------------------------------------
# 获取基本信息
# ---------------------------------------------------------------------------


class TestGetInfoCheck:
    async def test_detects_dorm_network(self, session: RunSession, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("10.150.31.97",),
                )
            ],
        )
        result = await run_check(GetInfoCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert result.summary == "检测到生活区网络"
        assert session.context.network_kind is NetworkKind.DORM

    async def test_detects_campus_network(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("10.249.1.1",),
                )
            ],
        )
        result = await run_check(GetInfoCheck, session)
        assert result.summary == "检测到教学区网络"
        assert session.context.network_kind is NetworkKind.CAMPUS

    async def test_non_campus_address_warns(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("192.0.2.20",),
                )
            ],
        )
        result = await run_check(GetInfoCheck, session)
        assert result.status is CheckResultStatus.WARN
        assert result.summary == "未检测到校园网地址"
        assert session.context.network_kind is None

    async def test_no_interfaces_fails(self, session: RunSession, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("hdu_netdebugger.checkers.get_info.list_interfaces", list)
        result = await run_check(GetInfoCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "未获取到网络接口信息"

    async def test_down_interface_is_ignored(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=False,
                    ipv4=("10.150.1.1",),
                )
            ],
        )
        result = await run_check(GetInfoCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "未找到符合条件的网络接口"

    @pytest.mark.parametrize("kind", [InterfaceKind.TUNNEL, InterfaceKind.PROP_VIRTUAL])
    async def test_tunnel_interface_warns(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
        kind: InterfaceKind,
    ) -> None:
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="Mihomo",
                    description="Meta Tunnel",
                    kind=kind,
                    is_up=True,
                    ipv4=("198.18.0.1",),
                )
            ],
        )
        result = await run_check(GetInfoCheck, session)
        assert any("类型网络接口" in item for item in result.warnings)
        assert session.context.has_tunnel_interface is True

    async def test_ethernet_and_tunnel_together(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("10.150.1.1",),
                ),
                InterfaceInfo(
                    name="Mihomo",
                    description="Meta Tunnel",
                    kind=InterfaceKind.PROP_VIRTUAL,
                    is_up=True,
                    ipv4=("198.18.0.1",),
                ),
            ],
        )
        result = await run_check(GetInfoCheck, session)
        assert result.summary == "检测到生活区网络"
        assert result.status is CheckResultStatus.WARN
        assert session.context.has_tunnel_interface is True

    async def test_interfaces_are_stored_on_context(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        sample = [
            InterfaceInfo(
                name="以太网",
                description="Realtek",
                kind=InterfaceKind.ETHERNET,
                is_up=True,
                ipv4=("10.150.1.1",),
            )
        ]
        monkeypatch.setattr("hdu_netdebugger.checkers.get_info.list_interfaces", lambda: sample)
        await run_check(GetInfoCheck, session)
        assert session.context.interfaces == sample


# ---------------------------------------------------------------------------
# 简单 Ping 类检查
# ---------------------------------------------------------------------------


class TestPingBasedChecks:
    async def test_bas_pass(self, session: RunSession, pinger: FakePinger) -> None:
        pinger.set_reachable(str(BAS_IP))
        result = await run_check(BASCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert result.summary == "BAS 可达"

    async def test_bas_uses_configured_ping_count(self, session: RunSession) -> None:
        session.config.ping_count = 2
        result = await run_check(BASCheck, session)
        assert result.warnings
        assert any("2" in line for line in (result.details or "").splitlines())

    async def test_bas_full_loss_fails(
        self,
        session: RunSession,
        fake_http: FakeHttp,
    ) -> None:
        fake_http.add(MIUI_ROM_204_URL, status_code=200)
        result = await run_check(BASCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "BAS 不可达"
        assert any("物理连接" in item for item in result.suggestions)

    async def test_bas_unreachable_but_public_net_works_hints_wrong_network(
        self,
        session: RunSession,
        fake_http: FakeHttp,
    ) -> None:
        """BAS 不通但 generate_204 返回 204 → 很可能根本不在校园网。"""
        fake_http.add_no_content(MIUI_ROM_204_URL)
        result = await run_check(BASCheck, session)
        assert any("不是在使用校园网" in item for item in result.warnings)
        assert any("校园网环境" in item for item in result.suggestions)

    async def test_srun_reachability_pass(self, session: RunSession, pinger: FakePinger) -> None:
        pinger.set_reachable(str(SRUN_IP))
        result = await run_check(SrunReachabilityCheck, session)
        assert result.status is CheckResultStatus.PASS

    async def test_srun_reachability_fail(self, session: RunSession) -> None:
        result = await run_check(SrunReachabilityCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "深澜认证服务器不可达"

    async def test_public_net_pass(
        self,
        session: RunSession,
        pinger: FakePinger,
        fake_http: FakeHttp,
    ) -> None:
        pinger.set_reachable("223.5.5.5")
        fake_http.add_no_content(MIUI_ROM_204_URL)
        result = await run_check(PublicNetCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert result.summary == "公网可达"

    async def test_public_net_ping_loss_but_http_ok(
        self,
        session: RunSession,
        pinger: FakePinger,
        fake_http: FakeHttp,
    ) -> None:
        """Ping 全丢但 HTTP 通了：总览是"公网可达"，状态被警告提升为 WARN。"""
        fake_http.add_no_content(MIUI_ROM_204_URL)
        pinger.count = 2
        result = await run_check(PublicNetCheck, session)
        assert result.summary == "公网可达"
        assert result.status is CheckResultStatus.WARN
        assert any("阿里主DNS不可达" in item for item in result.warnings)

    async def test_public_net_redirect_hints_portal_capture(
        self,
        session: RunSession,
        fake_http: FakeHttp,
    ) -> None:
        fake_http.add(
            MIUI_ROM_204_URL,
            status_code=302,
            headers={"location": "http://portal.hdu.edu.cn/"},
        )
        result = await run_check(PublicNetCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert any("门户劫持" in item for item in result.warnings)

    async def test_public_net_http_error(self, session: RunSession) -> None:
        result = await run_check(PublicNetCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert any("无法通过HTTP访问公网地址" in item for item in result.warnings)


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------


def make_dns_result(
    *,
    addresses: list[str] | None = None,
    error: str | None = None,
) -> DnsResult:
    return DnsResult(
        domain=HDU_PORTAL_DOMAIN,
        server="210.32.32.1",
        addresses=addresses or [],
        answer_lines=[f"{HDU_PORTAL_DOMAIN}. 60 IN A {a}" for a in (addresses or [])],
        error=error,
    )


class TestDNSCheck:
    async def test_correct_answer_passes(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        async def fake_query(*_args: Any, **_kwargs: Any) -> DnsResult:
            return make_dns_result(addresses=[str(SRUN_IP)])

        monkeypatch.setattr("hdu_netdebugger.checkers.dns.query_a", fake_query)
        result = await run_check(DNSCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert session.context.dns_available is True

    async def test_wrong_answer_fails(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        async def fake_query(*_args: Any, **_kwargs: Any) -> DnsResult:
            return make_dns_result(addresses=["127.0.0.1"])

        monkeypatch.setattr("hdu_netdebugger.checkers.dns.query_a", fake_query)
        result = await run_check(DNSCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "DNS 查询未返回正确的记录"
        assert session.context.dns_available is False

    async def test_query_error_fails(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        async def fake_query(*_args: Any, **_kwargs: Any) -> DnsResult:
            return make_dns_result(error="查询超时（5s）")

        monkeypatch.setattr("hdu_netdebugger.checkers.dns.query_a", fake_query)
        result = await run_check(DNSCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "DNS 查询失败"
        assert session.context.dns_available is False

    async def test_empty_answer_fails(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        async def fake_query(*_args: Any, **_kwargs: Any) -> DnsResult:
            return make_dns_result()

        monkeypatch.setattr("hdu_netdebugger.checkers.dns.query_a", fake_query)
        result = await run_check(DNSCheck, session)
        assert result.summary == "DNS 查询未返回结果"

    async def test_uses_configured_dns_server(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[str] = []

        async def fake_query(domain: str, server: object, **_kwargs: Any) -> DnsResult:
            seen.append(str(server))
            return make_dns_result(addresses=[str(SRUN_IP)])

        monkeypatch.setattr("hdu_netdebugger.checkers.dns.query_a", fake_query)
        session.config.dns_server = "223.5.5.5"  # ty: ignore[invalid-assignment]
        await run_check(DNSCheck, session)
        assert seen == ["223.5.5.5"]


# ---------------------------------------------------------------------------
# 认证状态
# ---------------------------------------------------------------------------


class TestAuthCheck:
    @pytest.fixture
    def ok_url(self) -> str:
        return f"https://{HDU_PORTAL_DOMAIN}/cgi-bin/rad_user_info?callback=jQuery"

    async def test_ok_via_domain(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        srun_ok_text: str,
        ok_url: str,
    ) -> None:
        session.context.dns_available = True
        fake_http.add(ok_url, text=srun_ok_text)
        result = await run_check(AuthCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert result.summary == "认证状态正常"
        assert session.context.isp_id is SrunProductId.CHINA_TELECOM
        assert session.context.raw_products_id == "4"
        assert "DNS 可用" in (result.details or "")

    async def test_falls_back_to_ip_when_dns_down(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        srun_ok_text: str,
    ) -> None:
        url = f"https://{SRUN_IP}/cgi-bin/rad_user_info?callback=jQuery"
        fake_http.add(url, text=srun_ok_text)
        result = await run_check(AuthCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert "DNS 不可用" in (result.details or "")

    async def test_auth_host_override(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        srun_ok_text: str,
    ) -> None:
        session.config.auth_host = "portal.example.com"
        fake_http.add(
            "https://portal.example.com/cgi-bin/rad_user_info?callback=jQuery",
            text=srun_ok_text,
        )
        result = await run_check(AuthCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert "命令行指定" in (result.details or "")

    async def test_not_online(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        ok_url: str,
    ) -> None:
        session.context.dns_available = True
        fake_http.add(ok_url, text=srun_jsonp({"error": "not_online_error"}))
        result = await run_check(AuthCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "未认证"
        assert any("认证页面" in item for item in result.suggestions)

    async def test_other_error_code(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        ok_url: str,
    ) -> None:
        session.context.dns_available = True
        fake_http.add(ok_url, text=srun_jsonp({"error": "user_error"}))
        result = await run_check(AuthCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "认证状态异常"
        assert any("user_error" in item for item in result.warnings)

    async def test_unparsable_response(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        ok_url: str,
    ) -> None:
        session.context.dns_available = True
        fake_http.add(ok_url, text="<html>portal</html>")
        result = await run_check(AuthCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert any("无法解析认证响应" in item for item in result.warnings)

    async def test_network_error(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        ok_url: str,
    ) -> None:
        session.context.dns_available = True
        fake_http.add(ok_url, error="ConnectError: 连接被拒绝")
        result = await run_check(AuthCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert any("认证请求失败" in item for item in result.warnings)

    async def test_logs_online_devices(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        srun_ok_text: str,
        ok_url: str,
    ) -> None:
        session.context.dns_available = True
        fake_http.add(ok_url, text=srun_ok_text)
        result = await run_check(AuthCheck, session)
        details = result.details or ""
        assert "在线设备(非空)数量: 1" in details
        assert "设备IP: 10.150.184.118" in details
        assert "运营商: 电信" in details

    async def test_invalid_isp_id_records_none(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        ok_url: str,
    ) -> None:
        session.context.dns_available = True
        fake_http.add(ok_url, text=srun_jsonp({"error": "ok", "products_id": "99"}))
        await run_check(AuthCheck, session)
        assert session.context.isp_id is None
        assert session.context.raw_products_id == "99"


# ---------------------------------------------------------------------------
# 运营商网关
# ---------------------------------------------------------------------------


class TestISPCheck:
    def test_condition_false_without_isp(self, session: RunSession) -> None:
        instance = ISPCheck(session)
        assert instance.isp_available() is False

    @pytest.mark.parametrize("isp", list(SrunProductId))
    def test_condition_true_for_known_isp(
        self,
        session: RunSession,
        isp: SrunProductId,
    ) -> None:
        session.context.isp_id = isp
        assert ISPCheck(session).isp_available() is True

    async def test_gateway_reachable(self, session: RunSession, pinger: FakePinger) -> None:
        session.context.isp_id = SrunProductId.CHINA_TELECOM
        pinger.set_reachable("60.176.40.1")
        result = await run_check(ISPCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert result.summary == "中国电信网关可达"

    async def test_gateway_unreachable(self, session: RunSession) -> None:
        session.context.isp_id = SrunProductId.CHINA_MOBILE
        result = await run_check(ISPCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "中国移动网关不可达"
        assert "117.147.121.1" in (result.details or "")

    async def test_without_isp_fails_gracefully(self, session: RunSession) -> None:
        result = await run_check(ISPCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "未获取到运营商信息"


# ---------------------------------------------------------------------------
# 代理
# ---------------------------------------------------------------------------


class TestProxyCheck:
    async def test_no_proxy_passes(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("hdu_netdebugger.checkers.proxy.detect_system_proxy", list)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        result = await run_check(ProxyCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert result.summary == "未检测到代理配置"

    async def test_system_proxy_detected(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        hit = ProxyHit(source="系统代理", detail="10.0.0.1:8080")
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.proxy.detect_system_proxy",
            lambda: [hit],
        )
        result = await run_check(ProxyCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "检测到代理配置"
        assert any("关闭代理" in item for item in result.suggestions)

    async def test_env_proxy_detected(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("hdu_netdebugger.checkers.proxy.detect_system_proxy", list)
        monkeypatch.setenv("HTTPS_PROXY", PROXY_URL)
        result = await run_check(ProxyCheck, session)
        assert result.status is CheckResultStatus.FAIL

    async def test_tunnel_warning_is_appended(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("hdu_netdebugger.checkers.proxy.detect_system_proxy", list)
        session.context.has_tunnel_interface = True
        result = await run_check(ProxyCheck, session)
        assert any("Tunnel 类型网络接口" in item for item in result.warnings)


class TestDetectEnvProxy:
    def test_detects_upper_and_lower_case(self) -> None:
        hits = detect_env_proxy({"HTTP_PROXY": "a", "https_proxy": "b"})
        assert {hit.source for hit in hits} == {"HTTP_PROXY", "https_proxy"}

    def test_ignores_empty_values(self) -> None:
        assert detect_env_proxy({"HTTP_PROXY": "", "HTTPS_PROXY": None}) == []

    def test_returns_empty_for_plain_dict(self) -> None:
        assert detect_env_proxy({}) == []


class TestProxyHelpers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("127.0.0.1:7890", True),
            ("localhost:1080", True),
            ("http://127.0.0.1:8080", True),
            ("http=127.0.0.1:8080", True),
            ("10.0.0.1:8080", False),
            ("proxy.corp.example:3128", False),
        ],
    )
    def test_is_loopback_proxy(self, value: str, expected: bool) -> None:
        assert _is_loopback_proxy(value) is expected

    def test_detect_system_proxy_does_not_raise(self) -> None:
        """任何平台都应当返回列表而不是抛异常。"""
        assert isinstance(detect_system_proxy(), list)


# ---------------------------------------------------------------------------
# 上下文重置
# ---------------------------------------------------------------------------


def test_context_reset_clears_everything() -> None:
    context = CheckContext()
    context.dns_available = True
    context.network_kind = NetworkKind.DORM
    context.has_tunnel_interface = True
    context.isp_id = SrunProductId.CHINA_MOBILE
    context.raw_products_id = "5"
    context.interfaces.append(
        InterfaceInfo(name="x", description="y", kind=InterfaceKind.OTHER, is_up=True)
    )
    context.reset()
    assert context.dns_available is False
    assert context.network_kind is None
    assert context.has_tunnel_interface is False
    assert context.isp_id is None
    assert context.raw_products_id == ""
    assert context.interfaces == []


def test_json_dumps_of_details_is_stable() -> None:
    """详细日志里可能混入 JSON，确认不会因为编码问题炸掉。"""
    payload = json.dumps({"在线设备": "10.150.184.118"}, ensure_ascii=False)
    assert "在线设备" in payload
