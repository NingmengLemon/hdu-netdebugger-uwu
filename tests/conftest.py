"""共享测试夹具。

替身实现放在 :mod:`tests.fakes`，这里只负责把它们包装成 pytest 夹具，
让每个测试函数按需索取。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from hdu_netdebugger.checkers import load_builtin_checkers  # noqa: F401 - 导入即注册
from tests.fakes import (
    SRUN_DEVICE_DETAIL,
    SRUN_OK_PAYLOAD,
    FakeHttp,
    FakePinger,
    make_session,
    srun_jsonp,
)

if TYPE_CHECKING:
    from hdu_netdebugger.session import RunSession


@pytest.fixture
def pinger() -> FakePinger:
    """一个所有目标都不可达的假 Pinger。"""
    return FakePinger()


@pytest.fixture
def fake_http() -> FakeHttp:
    """空的假 HTTP 客户端。"""
    return FakeHttp()


@pytest.fixture
def session(pinger: FakePinger, fake_http: FakeHttp) -> RunSession:
    """注入了假网络的最小 session。"""
    return make_session(pinger=pinger, http=fake_http)


@pytest.fixture
def srun_device_detail() -> dict[str, Any]:
    """一份在线设备明细（含一台空 IP 的占位设备）。"""
    return json.loads(json.dumps(SRUN_DEVICE_DETAIL))


@pytest.fixture
def srun_ok_payload() -> dict[str, Any]:
    """一份「认证正常」的响应体（``online_device_detail`` 仍为对象）。"""
    return json.loads(json.dumps(SRUN_OK_PAYLOAD))


@pytest.fixture
def srun_ok_text(srun_ok_payload: dict[str, Any], srun_device_detail: dict[str, Any]) -> str:
    """一份「认证正常」的完整 JSONP 响应（``online_device_detail`` 被二次编码）。"""
    payload = dict(srun_ok_payload)
    payload["online_device_detail"] = json.dumps(srun_device_detail, ensure_ascii=False)
    return srun_jsonp(payload)
