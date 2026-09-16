"""各个检查项的行为（全部走假网络，不产生真实流量）。"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING, Any, Self

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
    ALI_DNS_IP,
    BAS_IP,
    DNS_INTERCEPTION_PROBE_IP,
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
from hdu_netdebugger.net.ping import Backend
from hdu_netdebugger.net.routes import RouteInfo
from tests.fakes import FakeHttp, FakePinger, FakeRouter, make_session, srun_jsonp

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
                    ipv4=("10.150.0.100",),
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
        # 判定失败时要说清"按什么标准判的、该反馈什么" —— 网段属于会变的事实，
        # 不能只丢一句"未检测到"就让人去猜
        assert any("10.150.0.0/16" in item and "10.249.0.0/16" in item for item in result.warnings)
        assert any("反馈给 ICU" in item for item in result.suggestions)

    async def test_no_interfaces_fails(self, session: RunSession, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("hdu_netdebugger.checkers.get_info.list_interfaces", list)
        result = await run_check(GetInfoCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "未获取到网络接口信息"

    async def test_logs_ip_config_evidence(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """DHCP / 网关 / DNS 是排查校园网最常用的证据（官方流程第一步就是查 IP）。"""
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("10.150.0.100",),
                    index=15,
                    gateway="10.150.0.1",
                    dns_servers=("210.32.32.1",),
                    dhcp_enabled=True,
                )
            ],
        )

        result = await run_check(GetInfoCheck, session)

        details = result.details or ""
        assert "DHCP: 已启用" in details
        assert "默认网关: 10.150.0.1" in details
        assert "DNS 服务器: 210.32.32.1" in details

    async def test_warns_when_probes_egress_through_a_tunnel(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """本机实测场景：TUN 以 metric 0 抢走默认路由，所有探测都从隧道出去。

        这时"BAS 可达"并不代表校园物理链路可达，必须让读者看见。
        """
        tunnel_route = RouteInfo(interface_index=75, next_hop="198.18.0.2", metric=0)
        session = make_session(
            router=FakeRouter(
                {
                    str(BAS_IP): tunnel_route,
                    str(SRUN_IP): tunnel_route,
                    str(ALI_DNS_IP): tunnel_route,
                }
            )
        )
        session._interfaces = [
            InterfaceInfo(
                name="Mihomo",
                description="Meta Tunnel",
                kind=InterfaceKind.PROP_VIRTUAL,
                is_up=True,
                index=75,
            )
        ]
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("192.0.2.20",),
                    index=15,
                )
            ],
        )

        result = await run_check(GetInfoCheck, session)

        assert any("隧道类接口出站" in item for item in result.warnings)
        assert any("不代表校园网物理链路" in item for item in result.warnings)
        assert any("TUN 模式" in item for item in result.suggestions)
        details = result.details or ""
        assert "探测出站归属" in details
        assert "Mihomo" in details
        assert "metric 0" in details

    async def test_physical_egress_is_not_warned_about(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """探测走物理网卡时不加那条警告（否则人人都被吓一次）。"""
        session = make_session(
            router=FakeRouter(
                {
                    str(BAS_IP): RouteInfo(interface_index=15, next_hop="10.150.0.1", metric=25),
                }
            )
        )
        session._interfaces = [
            InterfaceInfo(
                name="以太网",
                description="Realtek",
                kind=InterfaceKind.ETHERNET,
                is_up=True,
                index=15,
            )
        ]
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("10.150.0.100",),
                    index=15,
                )
            ],
        )

        result = await run_check(GetInfoCheck, session)

        assert not any("隧道类接口出站" in item for item in result.warnings)

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

    async def test_bas_uses_configured_ping_count(
        self,
        session: RunSession,
        pinger: FakePinger,
    ) -> None:
        session.config.ping_count = 2
        result = await run_check(BASCheck, session)
        assert result.warnings
        # 用替身记录下来的调用参数断言，而不是在日志里找 "2"（任何含 2 的行都会误判）
        assert len(pinger.calls) == 1
        target, options, backend = pinger.calls[0]
        assert target == str(BAS_IP)
        assert options.count == 2
        assert backend is Backend.AUTO

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
        assert fake_http.calls == [MIUI_ROM_204_URL], "BAS 不通时应当去探 generate_204"

    @pytest.mark.parametrize(
        ("pattern", "loss_percent"),
        [
            ((True, False), 50),
            ((True, True, True, False), 25),
        ],
    )
    async def test_bas_partial_loss_warns(
        self,
        session: RunSession,
        pinger: FakePinger,
        pattern: tuple[bool, ...],
        loss_percent: int,
    ) -> None:
        """``0 < 成功数 < 总数``：以前 FakePinger 只能全成功或全超时，这条分支不可达。"""
        pinger.set_pattern(str(BAS_IP), pattern)
        result = await run_check(BASCheck, session)
        assert result.status is CheckResultStatus.WARN
        assert result.summary == "BAS 可达，但存在丢包"
        assert any(f"丢包率 {loss_percent}%" in item for item in result.warnings)
        assert any("检查网线" in item for item in result.suggestions)

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

    async def test_srun_reachability_partial_loss_warns(
        self,
        session: RunSession,
        pinger: FakePinger,
    ) -> None:
        pinger.set_pattern(str(SRUN_IP), (True, False, True, False))
        result = await run_check(SrunReachabilityCheck, session)
        assert result.status is CheckResultStatus.WARN
        assert result.summary == "深澜认证服务器可达，但存在丢包"
        assert any("丢包率 50%" in item for item in result.warnings)

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

    async def test_public_net_partial_loss_warns_on_loss_rate(
        self,
        session: RunSession,
        pinger: FakePinger,
        fake_http: FakeHttp,
    ) -> None:
        """丢包但不全丢：警告里要报出丢包率，结论仍由 HTTP 探针决定。"""
        fake_http.add_no_content(MIUI_ROM_204_URL)
        pinger.set_pattern("223.5.5.5", (True, False))

        result = await run_check(PublicNetCheck, session)

        assert result.status is CheckResultStatus.WARN
        assert result.summary == "公网可达"
        assert any("丢包率 50%" in item for item in result.warnings)
        assert any("检查网线" in item for item in result.suggestions)

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


def install_dns_queries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    campus: DnsResult,
    system: DnsResult | None = None,
    probe: DnsResult | None = None,
) -> list[str]:
    """把 DNS 检查用到的三处查询都换成替身，返回查询过的目标。

    三处是：校园 DNS、系统解析器、接管探测（向不可路由地址）。
    少打一个补丁，检查项就会去打**真实 DNS** —— 检查项测试必须无真实流量，
    而这里恰好新增了两条会走网络的查询，所以统一收在一个 helper 里。
    """
    calls: list[str] = []

    async def fake_query(_domain: str, server: object, **_kwargs: Any) -> DnsResult:
        text = str(server)
        calls.append(text)
        if text == str(DNS_INTERCEPTION_PROBE_IP):
            return probe if probe is not None else make_dns_result(error="查询超时（1s）")
        return campus

    async def fake_system(_domain: str, **_kwargs: Any) -> DnsResult:
        calls.append("<系统解析器>")
        return system if system is not None else make_dns_result(error="查询超时（5s）")

    monkeypatch.setattr("hdu_netdebugger.checkers.dns.query_a", fake_query)
    monkeypatch.setattr("hdu_netdebugger.checkers.dns.query_a_system", fake_system)
    return calls


class TestDNSCheck:
    async def test_correct_answer_passes(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        install_dns_queries(
            monkeypatch,
            campus=make_dns_result(addresses=[str(SRUN_IP)]),
            system=make_dns_result(addresses=[str(SRUN_IP)]),
        )
        result = await run_check(DNSCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert session.context.dns_available is True
        assert "DNS 接管检测" in (result.details or "")

    async def test_wrong_answer_fails(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        install_dns_queries(monkeypatch, campus=make_dns_result(addresses=["127.0.0.1"]))
        result = await run_check(DNSCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "DNS 查询未返回正确的记录"
        assert session.context.dns_available is False

    async def test_query_error_fails(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        install_dns_queries(monkeypatch, campus=make_dns_result(error="查询超时（5s）"))
        result = await run_check(DNSCheck, session)
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "DNS 查询失败"
        assert session.context.dns_available is False

    async def test_empty_answer_fails(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        install_dns_queries(monkeypatch, campus=make_dns_result())
        result = await run_check(DNSCheck, session)
        assert result.summary == "DNS 查询未返回结果"

    async def test_uses_configured_dns_server(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = install_dns_queries(
            monkeypatch,
            campus=make_dns_result(addresses=[str(SRUN_IP)]),
            system=make_dns_result(addresses=[str(SRUN_IP)]),
        )
        session.config.dns_server = "223.5.5.5"  # ty: ignore[invalid-assignment]

        await run_check(DNSCheck, session)

        assert "223.5.5.5" in calls

    async def test_logs_configured_servers_and_flags_a_mismatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """本机配置的 DNS 与工具查询的校园 DNS 不是一回事 —— 要说清楚。"""
        session = make_session(
            interfaces=[
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("192.0.2.20",),
                    dns_servers=("192.0.2.53",),
                )
            ]
        )
        install_dns_queries(
            monkeypatch,
            campus=make_dns_result(addresses=[str(SRUN_IP)]),
            system=make_dns_result(addresses=[str(SRUN_IP)]),
        )

        result = await run_check(DNSCheck, session)

        details = result.details or ""
        assert "系统配置的 DNS 服务器: 192.0.2.53" in details
        assert "并不在本机配置的 DNS 列表里" in details

    async def test_misconfigured_system_resolver_is_reported(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """校园 DNS 答对了、但**本机**解析不了 —— 这正是"能连 IP 打不开域名"那类故障。"""
        install_dns_queries(
            monkeypatch,
            campus=make_dns_result(addresses=[str(SRUN_IP)]),
            system=make_dns_result(error="查询超时（5s）"),
        )

        result = await run_check(DNSCheck, session)

        assert result.status is CheckResultStatus.WARN
        assert any("本机 DNS 解析结果不可用" in item for item in result.warnings)
        assert any("校园 DNS" in item for item in result.suggestions)
        assert any("hosts" in item for item in result.suggestions)
        assert session.context.dns_available is True, "校园 DNS 答对了，认证仍可用域名"

    async def test_system_resolver_alone_enables_domain_based_auth(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """校园 DNS 查不到、但本机自己能解析 → 结论仍是失败，但认证可以用域名。"""
        install_dns_queries(
            monkeypatch,
            campus=make_dns_result(error="查询超时（5s）"),
            system=make_dns_result(addresses=[str(SRUN_IP)]),
        )

        result = await run_check(DNSCheck, session)

        assert result.status is CheckResultStatus.FAIL
        assert session.context.dns_available is True

    async def test_dns_interception_is_reported(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """本机 DNS 被接管时，"校园 DNS 可用"这个结论根本立不住（实测本机如此）。"""
        install_dns_queries(
            monkeypatch,
            campus=make_dns_result(addresses=[str(SRUN_IP)]),
            system=make_dns_result(addresses=[str(SRUN_IP)]),
            probe=make_dns_result(addresses=[str(SRUN_IP)]),
        )

        result = await run_check(DNSCheck, session)

        assert result.status is CheckResultStatus.WARN
        assert any("本机 DNS 查询被接管" in item for item in result.warnings)
        assert any("不代表校园 DNS 正常" in item for item in result.suggestions)


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
        assert "设备IP: 10.150.0.100" in details
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

    async def test_gateway_partial_loss_warns(
        self,
        session: RunSession,
        pinger: FakePinger,
    ) -> None:
        session.context.isp_id = SrunProductId.CHINA_TELECOM
        pinger.set_pattern("60.176.40.1", (True, False))

        result = await run_check(ISPCheck, session)

        assert result.status is CheckResultStatus.WARN
        assert result.summary == "中国电信网关可达，但存在丢包"
        assert any("丢包率 50%" in item for item in result.warnings)


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

    async def test_env_proxy_ignored_when_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """回归：``--no-env-proxy`` 一边让请求绕开代理、一边把检查判失败。"""
        monkeypatch.setattr("hdu_netdebugger.checkers.proxy.detect_system_proxy", list)
        monkeypatch.setenv("HTTPS_PROXY", PROXY_URL)
        session = make_session(trust_env_proxy=False)
        result = await run_check(ProxyCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert result.summary == "未检测到代理配置"
        assert "--no-env-proxy" in (result.details or "")

    async def test_system_proxy_still_counts_when_env_proxy_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--no-env-proxy`` 只管环境变量；系统代理是另一回事，照旧判定。"""
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.proxy.detect_system_proxy",
            lambda: [ProxyHit(source="系统代理", detail="10.0.0.1:8080")],
        )
        monkeypatch.setenv("HTTPS_PROXY", PROXY_URL)
        session = make_session(trust_env_proxy=False)
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


class TestSystemProxyParsing:
    """系统代理的**解析**逻辑：不依赖真机。

    macOS 分支手上没有机器，但 ``scutil --proxy`` 的输出格式是稳定的，
    把解析钉住即可；Windows 分支打桩注册表。
    """

    def test_macos_scutil_output_is_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        output = (
            "<dictionary> {\n"
            "  HTTPEnable : 1\n"
            "  HTTPPort : 7890\n"
            "  HTTPProxy : 127.0.0.1\n"
            "  HTTPSEnable : 0\n"
            "  SOCKSEnable : 1\n"
            "  ExceptionsList : <array> {\n"
            "    0 : *.local\n"
            "  }\n"
            "}\n"
        )

        class _Completed:
            stdout = output

        monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _Completed())
        monkeypatch.setattr(sys, "platform", "darwin")

        hits = detect_system_proxy()

        assert [(hit.source, hit.detail) for hit in hits] == [
            ("系统代理", "HTTP"),
            ("系统代理", "SOCKS"),
        ]

    def test_macos_scutil_failure_returns_no_hits(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(*_args: object, **_kwargs: object) -> object:
            raise OSError("scutil 不存在")

        monkeypatch.setattr(subprocess, "run", boom)
        monkeypatch.setattr(sys, "platform", "darwin")

        assert detect_system_proxy() == []

    @pytest.mark.skipif(sys.platform != "win32", reason="winreg 仅 Windows 有")
    def test_windows_registry_values_are_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import winreg  # noqa: PLC0415 - winreg 仅 Windows 有，模块级导入会让 Linux 收集失败

        values: dict[str, object] = {
            "ProxyEnable": 1,
            "ProxyServer": "10.0.0.1:8080;http=127.0.0.1:7890",
            "AutoConfigURL": "",
        }

        class _Key:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *_exc: object) -> bool:
                return False

        def fake_query(_key: object, name: str) -> tuple[object, int]:
            if name not in values:
                raise FileNotFoundError(name)
            return values[name], 4

        monkeypatch.setattr(winreg, "OpenKey", lambda *_args, **_kwargs: _Key())
        monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

        hits = detect_system_proxy()

        assert [(hit.source, hit.detail) for hit in hits] == [("系统代理", "10.0.0.1:8080")], (
            "本机环回代理（用户自己开的加速器）应当被过滤掉"
        )

    @pytest.mark.skipif(sys.platform != "win32", reason="winreg 仅 Windows 有")
    def test_windows_pac_url_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import winreg  # noqa: PLC0415 - winreg 仅 Windows 有，模块级导入会让 Linux 收集失败

        values: dict[str, object] = {
            "ProxyEnable": 0,
            "ProxyServer": "",
            "AutoConfigURL": "http://pac.example/proxy.pac",
        }

        class _Key:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *_exc: object) -> bool:
                return False

        monkeypatch.setattr(winreg, "OpenKey", lambda *_args, **_kwargs: _Key())
        monkeypatch.setattr(winreg, "QueryValueEx", lambda _key, name: (values[name], 4))

        assert [(hit.source, hit.detail) for hit in detect_system_proxy()] == [
            ("PAC 自动配置", "http://pac.example/proxy.pac")
        ]

    @pytest.mark.skipif(sys.platform != "win32", reason="winreg 仅 Windows 有")
    def test_windows_registry_error_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import winreg  # noqa: PLC0415 - winreg 仅 Windows 有，模块级导入会让 Linux 收集失败

        def boom(*_args: object, **_kwargs: object) -> object:
            raise OSError("读不到注册表项")

        monkeypatch.setattr(winreg, "OpenKey", boom)

        assert detect_system_proxy() == []


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
    payload = json.dumps({"在线设备": "10.150.0.100"}, ensure_ascii=False)
    assert "在线设备" in payload
