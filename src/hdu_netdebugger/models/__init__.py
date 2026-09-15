"""数据模型层.

对外的三个观察面：

* :mod:`hdu_netdebugger.models.result` —— 检查结果与运行产出（CLI / GUI / 报告共用）
* :mod:`hdu_netdebugger.models.srun` —— 深澜认证响应
* :mod:`hdu_netdebugger.models.ping` —— Ping 结果
"""

from __future__ import annotations

from hdu_netdebugger.models.enums import CheckResultStatus, CheckStatus, PingStatus
from hdu_netdebugger.models.interface import InterfaceInfo, InterfaceKind
from hdu_netdebugger.models.ping import MultiPingResult, PingReply
from hdu_netdebugger.models.result import CheckOutcome, CheckResult, CheckResultBuilder
from hdu_netdebugger.models.srun import (
    DeviceItem,
    SrunAuthResponse,
    parse_auth_response,
    parse_srun_jsonp,
)

__all__ = [
    "CheckOutcome",
    "CheckResult",
    "CheckResultBuilder",
    "CheckResultStatus",
    "CheckStatus",
    "DeviceItem",
    "InterfaceInfo",
    "InterfaceKind",
    "MultiPingResult",
    "PingReply",
    "PingStatus",
    "SrunAuthResponse",
    "parse_auth_response",
    "parse_srun_jsonp",
]
