"""各个检查项的行为（全部走假网络，不产生真实流量）。"""

from __future__ import annotations

import json
import subprocess
import sys
from ipaddress import IPv4Network
from typing import TYPE_CHECKING, Any, Self

import pytest

from hdu_netdebugger.checkers.auth import AuthCheck
from hdu_netdebugger.checkers.bas import BASCheck
from hdu_netdebugger.checkers.dns import DNSCheck
from hdu_netdebugger.checkers.get_info import GetInfoCheck
from hdu_netdebugger.checkers.ipv6 import V6_PROBE_TARGET, IPv6Check
from hdu_netdebugger.checkers.isp import ISPCheck
from hdu_netdebugger.checkers.mtu import MtuCheck
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
    PUBLIC_ANCHOR_IP,
    SRUN_IP,
    SRUN_PRODUCT_GATEWAYS,
    NetworkKind,
    SrunProductId,
)
from hdu_netdebugger.context import CheckContext
from hdu_netdebugger.models.enums import CheckResultStatus
from hdu_netdebugger.models.interface import InterfaceInfo, InterfaceKind
from hdu_netdebugger.net.dns import DnsResult
from hdu_netdebugger.net.mtu import MAX_PROBE_PAYLOAD
from hdu_netdebugger.net.ping import Backend, BackendUnavailable
from hdu_netdebugger.net.routes import DefaultRoute, RouteInfo
from hdu_netdebugger.session import RunSession
from tests.fakes import FakeHttp, FakePinger, FakeRouter, make_session, srun_jsonp

if TYPE_CHECKING:
    from hdu_netdebugger.models.result import CheckResult

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

    async def test_logs_the_default_route_table(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """多条默认路由同时存在，是"网线插着却从隧道出去"的直接原因。"""
        sample = InterfaceInfo(
            name="以太网",
            description="Realtek",
            kind=InterfaceKind.ETHERNET,
            is_up=True,
            ipv4=("10.150.0.100",),
            index=15,
        )
        session = make_session(
            interfaces=[sample],
            routes=[
                DefaultRoute(interface_index=15, next_hop="10.150.0.1", metric=0),
                DefaultRoute(interface_index=75, next_hop="198.18.0.2", metric=0),
            ],
        )
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [sample],
        )

        result = await run_check(GetInfoCheck, session)

        details = result.details or ""
        assert "IPv4 默认路由（共 2 条" in details
        assert "以太网 (ifIndex 15) 下一跳 10.150.0.1" in details
        assert "ifIndex 75" in details

    async def test_omits_the_route_table_when_unavailable(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """拿不到路由表时静默跳过，不打印一个空标题。"""
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

        assert "IPv4 默认路由" not in (result.details or "")

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

    async def test_detects_eduroam_network(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """eduroam 也是校园网接入方式，原版会把它判成"未检测到校园网地址"。"""
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="WLAN",
                    description="Intel",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("10.252.1.5",),
                )
            ],
        )

        result = await run_check(GetInfoCheck, session)

        assert result.status is CheckResultStatus.PASS
        assert result.summary == "检测到eduroam 漫游网络"
        assert session.context.network_kind is NetworkKind.EDUROAM
        assert any("eduroam" in item for item in result.suggestions)

    @pytest.mark.parametrize(
        ("address", "keyword"),
        [
            ("169.254.10.20", "DHCP"),
            ("192.168.1.10", "路由器"),
            ("10.230.5.5", "10.230"),
        ],
    )
    async def test_reports_abnormal_segments(
        self,
        address: str,
        keyword: str,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """三类异常网段要说清是哪一种（官方手册把它们分得很清楚）。"""
        sample = InterfaceInfo(
            name="以太网",
            description="Realtek",
            kind=InterfaceKind.ETHERNET,
            is_up=True,
            ipv4=(address,),
        )
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [sample],
        )

        result = await run_check(GetInfoCheck, session)

        assert any(keyword in item for item in result.warnings)

    async def test_link_local_suggests_dhcp_fix(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """169.254 的处置是明确的：查链路，路由器改 DHCP（别用 PPPoE 拨号）。"""
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("169.254.10.20",),
                )
            ],
        )

        result = await run_check(GetInfoCheck, session)

        assert any("DHCP" in item and "PPPoE" in item for item in result.suggestions)

    async def test_extra_campus_network_is_reported_as_custom(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """用户用 `--campus-network` 追加的网段标成"自定义"，并提示可反馈补进默认值。"""
        sample = InterfaceInfo(
            name="WLAN",
            description="Intel",
            kind=InterfaceKind.ETHERNET,
            is_up=True,
            ipv4=("10.151.1.100",),
        )
        session = make_session(
            interfaces=[sample],
            extra_campus_networks=(IPv4Network("10.151.0.0/16"),),
        )
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [sample],
        )

        result = await run_check(GetInfoCheck, session)

        assert result.status is CheckResultStatus.PASS
        assert result.summary == "检测到自定义校园网段"
        assert session.context.network_kind is NetworkKind.CUSTOM
        assert any("--campus-network" in item for item in result.suggestions)

    async def test_extra_campus_network_does_not_leak_into_context(
        self,
        session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """没传追加网段时，10.151.x 仍然是"未检测到校园网地址"。"""
        monkeypatch.setattr(
            "hdu_netdebugger.checkers.get_info.list_interfaces",
            lambda: [
                InterfaceInfo(
                    name="WLAN",
                    description="Intel",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("10.151.1.100",),
                )
            ],
        )

        result = await run_check(GetInfoCheck, session)

        assert result.summary == "未检测到校园网地址"
        assert session.context.network_kind is None


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

    async def test_warns_when_a_reply_comes_from_another_address(
        self,
        session: RunSession,
        pinger: FakePinger,
    ) -> None:
        """ICMP 回复理应来自被探测的主机；来源不对说明中间有设备替它应答。"""
        pinger.set_reachable(str(BAS_IP))
        pinger.source = "192.0.2.99"

        result = await run_check(BASCheck, session)

        assert any("非目标地址" in item for item in result.warnings)
        assert any("192.0.2.99" in item for item in result.warnings)

    async def test_no_source_warning_when_source_matches(
        self,
        session: RunSession,
        pinger: FakePinger,
    ) -> None:
        pinger.set_reachable(str(BAS_IP))
        pinger.source = str(BAS_IP)

        result = await run_check(BASCheck, session)

        assert not any("非目标地址" in item for item in result.warnings)

    async def test_bas_unreachable_in_teaching_area_warns_not_fails(
        self,
        session: RunSession,
    ) -> None:
        """`10.150.0.1` 是**生活区** BAS；教学区拿它不通是预期现象，不能判故障。

        官方手册没给教学区的对应测试点（``campus.html:32`` 写着"各教学楼有线地址待补充"），
        所以教学区用户原本会稳定看到一次假失败。
        """
        session.context.network_kind = NetworkKind.CAMPUS

        result = await run_check(BASCheck, session)

        assert result.status is CheckResultStatus.WARN
        assert result.summary == "BAS 不可达（教学区场景，仅供参考）"
        assert any("教学区" in item for item in result.warnings)

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
        pinger.set_reachable("223.5.5.5", str(PUBLIC_ANCHOR_IP))
        fake_http.add_no_content(MIUI_ROM_204_URL)
        result = await run_check(PublicNetCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert result.summary == "公网可达"

    async def test_public_net_anchor_disagreement_is_reported(
        self,
        session: RunSession,
        pinger: FakePinger,
        fake_http: FakeHttp,
    ) -> None:
        """主锚点通、备用锚点不通：两个数字给出相反结论时必须说清楚。

        依据：群里讨论过 223.5.5.5 本身也会被劫持 / 限速，单个锚点不足以断定公网状态。
        """
        pinger.set_reachable("223.5.5.5")
        fake_http.add_no_content(MIUI_ROM_204_URL)

        result = await run_check(PublicNetCheck, session)

        assert any("公网锚点结果不一致" in item for item in result.warnings)
        assert result.summary == "公网可达", "结论仍以 HTTP 探测为准"

    async def test_public_net_secondary_anchor_alone_can_carry_the_verdict(
        self,
        session: RunSession,
        pinger: FakePinger,
        fake_http: FakeHttp,
    ) -> None:
        """主锚点不通但备用锚点通 —— 公网其实是通的（原来的写法会报"主DNS不可达"）。"""
        pinger.set_reachable(str(PUBLIC_ANCHOR_IP))
        fake_http.add_no_content(MIUI_ROM_204_URL)

        result = await run_check(PublicNetCheck, session)

        assert result.summary == "公网可达"
        assert any("备用锚点" in item for item in result.warnings)

    async def test_public_net_both_anchors_down(
        self,
        session: RunSession,
        pinger: FakePinger,
        fake_http: FakeHttp,
    ) -> None:
        fake_http.add_no_content(MIUI_ROM_204_URL)

        result = await run_check(PublicNetCheck, session)

        assert any("两个公网锚点都不可达" in item for item in result.warnings)

    async def test_public_net_ping_loss_but_http_ok(
        self,
        session: RunSession,
        pinger: FakePinger,
        fake_http: FakeHttp,
    ) -> None:
        """Ping 全丢但 HTTP 通了：总览是"公网可达"，状态被警告提升为 WARN。"""
        fake_http.add_no_content(MIUI_ROM_204_URL)
        pinger.count = 2
        pinger.set_reachable(str(PUBLIC_ANCHOR_IP))
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
        pinger.set_pattern(str(PUBLIC_ANCHOR_IP), (True, False))

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

    async def test_falls_back_to_plain_http(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        srun_ok_text: str,
        ok_url: str,
    ) -> None:
        """HTTPS 不通时改用明文 HTTP（校园认证接口监听 80，官方地址就是明文）。"""
        session.context.dns_available = True
        fake_http.add(ok_url, error="ConnectError: SSL 握手失败")
        plain_url = f"http://{HDU_PORTAL_DOMAIN}/cgi-bin/rad_user_info?callback=jQuery"
        fake_http.add(plain_url, text=srun_ok_text)

        result = await run_check(AuthCheck, session)

        # 认证本身是正常的（总览取自 HTTP 那条路径的结果），但 HTTPS 不通确实是问题，
        # 所以状态被警告提升为 WARN —— 这正是我们想要的
        assert result.status is CheckResultStatus.WARN
        assert result.summary == "认证状态正常"
        assert any("改用明文 HTTP" in item for item in result.warnings)
        assert plain_url in fake_http.calls

    async def test_reports_failure_when_both_transports_fail(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        ok_url: str,
    ) -> None:
        session.context.dns_available = True
        fake_http.add(ok_url, error="ConnectError: SSL 握手失败")

        result = await run_check(AuthCheck, session)

        assert result.status is CheckResultStatus.FAIL
        assert "HTTPS 失败" in (result.details or "")

    async def test_logs_the_portal_page_redirect(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        srun_ok_text: str,
        ok_url: str,
    ) -> None:
        """门户首页跳到哪里是一条独立线索（未认证跳登录页、已认证跳管理页）。"""
        session.context.dns_available = True
        fake_http.add(ok_url, text=srun_ok_text)
        fake_http.add(
            f"http://{HDU_PORTAL_DOMAIN}/",
            status_code=302,
            headers={"location": "/index_1.html"},
        )

        result = await run_check(AuthCheck, session)

        assert "门户首页" in (result.details or "")
        assert "/index_1.html" in (result.details or "")

    async def test_logs_session_and_quota_facts(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        srun_ok_text: str,
        ok_url: str,
    ) -> None:
        """会话时长 / 余量 / 拨号方式 / 服务端版本都是白拿的诊断字段。"""
        session.context.dns_available = True
        fake_http.add(ok_url, text=srun_ok_text)

        result = await run_check(AuthCheck, session)

        details = result.details or ""
        assert "会话与配额" in details
        assert "本次在线时长" in details
        assert "余量" in details
        assert "认证服务端版本" in details

    async def test_never_prints_identity_fields(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        ok_url: str,
    ) -> None:
        """身份字段一律不打印（个人信息，且对诊断没有增量价值）。"""
        session.context.dns_available = True
        fake_http.add(
            ok_url,
            text=srun_jsonp(
                {
                    "error": "ok",
                    "products_id": "4",
                    "user_name": "SENSITIVE-ACCOUNT",
                    "user_mac": "SENSITIVE-MAC",
                    "phone": "SENSITIVE-PHONE",
                    "email": "SENSITIVE-EMAIL",
                    "real_name": "SENSITIVE-NAME",
                }
            ),
        )

        result = await run_check(AuthCheck, session)

        details = result.details or ""
        for secret in (
            "SENSITIVE-ACCOUNT",
            "SENSITIVE-MAC",
            "SENSITIVE-PHONE",
            "SENSITIVE-EMAIL",
            "SENSITIVE-NAME",
        ):
            assert secret not in details

    async def test_logs_device_addresses_versus_local_addresses(
        self,
        ok_url: str,
        srun_ok_text: str,
    ) -> None:
        """在线设备地址与本机地址对照：认证的是本机还是路由器后面别的设备。"""
        http = FakeHttp()
        http.add(ok_url, text=srun_ok_text)
        session = make_session(
            http=http,
            interfaces=[
                InterfaceInfo(
                    name="以太网",
                    description="Realtek",
                    kind=InterfaceKind.ETHERNET,
                    is_up=True,
                    ipv4=("10.150.0.100",),
                )
            ],
        )
        session.context.dns_available = True

        result = await run_check(AuthCheck, session)

        assert "在线设备里包含本机地址: 10.150.0.100" in (result.details or "")

    async def test_logs_when_no_device_address_is_local(
        self,
        session: RunSession,
        fake_http: FakeHttp,
        srun_ok_text: str,
        ok_url: str,
    ) -> None:
        session.context.dns_available = True
        fake_http.add(ok_url, text=srun_ok_text)

        result = await run_check(AuthCheck, session)

        assert "都不在本机网卡上" in (result.details or "")

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
        pinger.set_reachable(*map(str, SRUN_PRODUCT_GATEWAYS[SrunProductId.CHINA_TELECOM]))
        result = await run_check(ISPCheck, session)
        assert result.status is CheckResultStatus.PASS
        assert result.summary == "中国电信网关可达"

    async def test_preferred_candidate_answer_needs_no_note(
        self,
        session: RunSession,
        pinger: FakePinger,
    ) -> None:
        """手册给的首选地址应答时，不该出现"与手册不一致"的提示。"""
        session.context.isp_id = SrunProductId.CHINA_TELECOM
        preferred = SRUN_PRODUCT_GATEWAYS[SrunProductId.CHINA_TELECOM][0]
        pinger.set_reachable(str(preferred))

        result = await run_check(ISPCheck, session)

        assert result.status is CheckResultStatus.PASS
        assert not any("与手册/原版不一致" in item for item in result.suggestions)

    async def test_fallback_candidate_answer_is_reported(
        self,
        session: RunSession,
        pinger: FakePinger,
    ) -> None:
        """首选不应答、备选应答：结论仍是可达，但要说清实际应答的是哪个。

        这条正好对应"原版的 60.176.40.1 与手册的 183.157.176.1 谁对"这个悬案 ——
        工具的立场是**谁应答就报谁**，把判断留给证据。
        """
        session.context.isp_id = SrunProductId.CHINA_TELECOM
        preferred, fallback = SRUN_PRODUCT_GATEWAYS[SrunProductId.CHINA_TELECOM]
        pinger.set_reachable(str(fallback))

        result = await run_check(ISPCheck, session)

        assert result.status is CheckResultStatus.PASS
        assert f"首选候选 {preferred} 无应答" in (result.details or "")
        assert f"实际应答的是 {fallback}" in (result.details or "")
        assert any("与手册/原版不一致" in item for item in result.suggestions)

    async def test_all_candidates_down_lists_them(
        self,
        session: RunSession,
    ) -> None:
        session.context.isp_id = SrunProductId.CHINA_TELECOM

        result = await run_check(ISPCheck, session)

        assert result.status is CheckResultStatus.FAIL
        warnings = " ".join(result.warnings)
        for candidate in SRUN_PRODUCT_GATEWAYS[SrunProductId.CHINA_TELECOM]:
            assert str(candidate) in warnings

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
        _, fallback = SRUN_PRODUCT_GATEWAYS[SrunProductId.CHINA_TELECOM]
        pinger.set_pattern(str(fallback), (True, False))

        result = await run_check(ISPCheck, session)

        assert result.status is CheckResultStatus.WARN
        assert result.summary == "中国电信网关可达，但存在丢包"
        assert any("丢包率 50%" in item for item in result.warnings)


# ---------------------------------------------------------------------------
# 代理
# ---------------------------------------------------------------------------


class TestMtuCheck:
    """路径 MTU：小包通、大包不通那类故障（实测校园目标 996 字节以上全丢）。"""

    @staticmethod
    def _pinger() -> FakePinger:
        """三个目标都健康；需要时再单独覆盖某个目标的边界。"""
        pinger = FakePinger()
        for target in (BAS_IP, SRUN_IP, ALI_DNS_IP):
            pinger.set_mtu_edge(str(target), MAX_PROBE_PAYLOAD)
        return pinger

    async def test_healthy_path_passes(self) -> None:
        result = await run_check(MtuCheck, make_session(pinger=self._pinger()))

        assert result.status is CheckResultStatus.PASS
        assert "未发现路径 MTU 问题" in result.summary

    async def test_silent_drop_is_warned_as_blackhole(self) -> None:
        """有上限但从不回"需要分片" —— 处置与"只是 MTU 偏小"不同。"""
        pinger = self._pinger()
        pinger.set_mtu_edge(str(SRUN_IP), 996)

        result = await run_check(MtuCheck, make_session(pinger=pinger))

        assert result.status is CheckResultStatus.WARN
        assert any("静默丢弃" in item and "996" in item for item in result.warnings)
        assert any("大包被静默丢弃" in item for item in result.suggestions)

    async def test_frag_needed_is_worded_differently(self) -> None:
        """中间设备回了"需要分片"时不该说成黑洞。"""
        pinger = self._pinger()
        pinger.set_mtu_edge(str(SRUN_IP), 1400, frag_needed=True)

        result = await run_check(MtuCheck, make_session(pinger=pinger))

        assert any("有回 ICMP 需要分片" in item for item in result.warnings)
        assert not any("静默丢弃" in item for item in result.warnings)

    async def test_unreachable_target_does_not_break_the_check(self) -> None:
        result = await run_check(MtuCheck, make_session(pinger=FakePinger()))

        assert result.status is CheckResultStatus.WARN
        assert "未发现路径 MTU 受限" in result.summary

    async def test_tunnel_egress_is_annotated(self) -> None:
        """有 TUN 时这类测量会被隧道污染，必须标注而不是沉默。"""
        tunnel = RouteInfo(interface_index=75, metric=0)
        session = make_session(
            pinger=self._pinger(),
            router=FakeRouter({str(BAS_IP): tunnel, str(SRUN_IP): tunnel, str(ALI_DNS_IP): tunnel}),
            interfaces=[
                InterfaceInfo(
                    name="Mihomo",
                    description="Meta Tunnel",
                    kind=InterfaceKind.PROP_VIRTUAL,
                    is_up=True,
                    index=75,
                )
            ],
        )

        result = await run_check(MtuCheck, session)

        assert any("经由隧道类接口出站" in item for item in result.warnings)

    async def test_missing_df_backend_is_reported(self) -> None:
        """后端不支持 DF 时必须说"没测"，而不是悄悄给出"没问题"。"""

        class _NoDfSession(RunSession):
            async def ping_size(self, *_args: Any, **_kwargs: Any) -> Any:
                raise BackendUnavailable("icmplib 后端不支持 DF（不分片）探测")

        session = _NoDfSession(router=FakeRouter(), interfaces=[])

        result = await run_check(MtuCheck, session)

        assert result.status is CheckResultStatus.WARN
        assert "无法进行路径 MTU 探测" in result.summary
        assert any("--ping-backend" in item for item in result.suggestions)


class TestIPv6Check:
    """IPv6：群日志里唯一有完整证据链的故障就在这一面。"""

    @staticmethod
    def _interface(*addresses: str) -> InterfaceInfo:
        return InterfaceInfo(
            name="以太网",
            description="Realtek",
            kind=InterfaceKind.ETHERNET,
            is_up=True,
            ipv4=("192.0.2.20",),
            ipv6=addresses,
        )

    async def test_no_usable_ipv6_is_reported_as_not_applicable(self) -> None:
        """只有链路本地与 Teredo 时要说"不适用"，而不是硬报一个失败（本机实测如此）。"""
        session = make_session(
            interfaces=[
                self._interface(
                    "fe80::1%15",
                    "2001:0:14c9:d502:2cad:f749:8a6c:9b1e",
                    "::1",
                )
            ]
        )

        result = await run_check(IPv6Check, session)

        assert result.status is CheckResultStatus.PASS
        assert "IPv6 相关故障不适用" in result.summary

    async def test_global_ipv6_that_cannot_reach_the_internet_warns(self) -> None:
        session = make_session(interfaces=[self._interface("2400:3200::abcd")])

        result = await run_check(IPv6Check, session)

        assert result.status is CheckResultStatus.WARN
        assert any("v6 不通" in item for item in result.warnings) or ("v6 不通" in result.summary)

    async def test_working_ipv6_passes_and_measures_mtu(self) -> None:
        pinger = FakePinger()
        pinger.set_reachable(V6_PROBE_TARGET)
        pinger.set_mtu_edge(V6_PROBE_TARGET, MAX_PROBE_PAYLOAD)
        session = make_session(
            pinger=pinger,
            interfaces=[self._interface("2400:3200::abcd")],
        )

        result = await run_check(IPv6Check, session)

        assert result.status is CheckResultStatus.PASS
        assert "IPv6 可用" in result.summary

    async def test_campus_ula_counts_as_usable_and_is_labelled(self) -> None:
        """手册给的校园 v6 是 FC01::/48（属 ULA），也算可用地址并标注。"""
        pinger = FakePinger()
        pinger.set_reachable(V6_PROBE_TARGET)
        pinger.set_mtu_edge(V6_PROBE_TARGET, MAX_PROBE_PAYLOAD)
        session = make_session(
            pinger=pinger,
            interfaces=[self._interface("fc01::1234")],
        )

        result = await run_check(IPv6Check, session)

        assert "属校园 IPv6 网段" in (result.details or "")

    async def test_v6_mtu_limit_is_warned(self) -> None:
        """正是群日志里那次：1420 通、1428 全丢、收不到 ICMPv6 Type 2。"""
        pinger = FakePinger()
        pinger.set_reachable(V6_PROBE_TARGET)
        pinger.set_mtu_edge(V6_PROBE_TARGET, 1420)
        session = make_session(
            pinger=pinger,
            interfaces=[self._interface("2400:3200::abcd")],
        )

        result = await run_check(IPv6Check, session)

        assert result.status is CheckResultStatus.WARN
        assert any("IPv6 路径 MTU 受限" in item and "1420" in item for item in result.warnings)
        assert any("ICMPv6 Type 2" in item for item in result.suggestions)


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
