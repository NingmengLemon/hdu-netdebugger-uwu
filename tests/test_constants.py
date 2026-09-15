"""常量与网络区域判定。"""

from __future__ import annotations

from ipaddress import IPv4Address

import pytest

from hdu_netdebugger.constants import (
    ALI_DNS_IP,
    BAS_IP,
    CAMPUS_NETWORK,
    DORM_NETWORK,
    HDU_DNS_IP,
    HDU_PORTAL_DOMAIN,
    HDU_PORTAL_ROUTE,
    MIUI_ROM_204_URL,
    SRUN_IP,
    SRUN_PRODUCT_GATEWAYS,
    SRUN_PRODUCT_NAMES,
    NetworkKind,
    SrunErrCode,
    SrunProductId,
    dorm_or_campus,
)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("10.150.0.1", NetworkKind.DORM),
        ("10.150.255.254", NetworkKind.DORM),
        ("10.249.0.1", NetworkKind.CAMPUS),
        ("10.249.255.254", NetworkKind.CAMPUS),
        ("10.149.255.255", None),
        ("10.151.0.1", None),
        ("192.0.2.20", None),
        ("192.168.1.1", None),
    ],
)
def test_dorm_or_campus(address: str, expected: NetworkKind | None) -> None:
    assert dorm_or_campus(IPv4Address(address)) is expected


def test_network_kind_labels() -> None:
    assert NetworkKind.DORM.label == "生活区网络"
    assert NetworkKind.CAMPUS.label == "教学区网络"


def test_topology_constants_are_consistent() -> None:
    """拓扑常量之间不应互相矛盾。"""
    assert BAS_IP in DORM_NETWORK
    assert HDU_DNS_IP not in DORM_NETWORK
    assert SRUN_IP not in DORM_NETWORK
    assert SRUN_IP not in CAMPUS_NETWORK
    assert HDU_PORTAL_ROUTE.startswith("/")
    assert HDU_PORTAL_DOMAIN.count(".") >= 2
    assert MIUI_ROM_204_URL.startswith("http://")
    assert ALI_DNS_IP.is_global


def test_srun_product_maps_align() -> None:
    """运营商 ID → 名称 / 网关 两张表必须一一对应。"""
    assert set(SRUN_PRODUCT_NAMES) == set(SRUN_PRODUCT_GATEWAYS)
    assert set(SRUN_PRODUCT_NAMES) == set(SrunProductId)
    assert SRUN_PRODUCT_NAMES[SrunProductId.CHINA_TELECOM] == "中国电信"
    assert str(SRUN_PRODUCT_GATEWAYS[SrunProductId.CHINA_UNICOM]) == "172.20.64.1"


def test_srun_product_maps_are_immutable() -> None:
    with pytest.raises(TypeError):
        SRUN_PRODUCT_NAMES[SrunProductId.CHINA_MOBILE] = "篡改"  # ty: ignore[invalid-assignment]


def test_srun_product_id_is_int_comparable() -> None:
    """JSON 里拿到的是字符串，转成 int 后要能直接比较。"""
    assert int("4") == SrunProductId.CHINA_TELECOM
    assert SrunProductId(3) is SrunProductId.CHINA_UNICOM
    with pytest.raises(ValueError, match="9 is not a valid"):
        SrunProductId(9)


def test_srun_error_codes() -> None:
    assert SrunErrCode.OK == "ok"
    assert SrunErrCode.NOT_ONLINE == "not_online_error"
