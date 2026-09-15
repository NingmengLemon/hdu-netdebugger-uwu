"""枚举类型：检查结果严重程度、检查项生命周期状态、Ping 状态。"""

from __future__ import annotations

from enum import IntEnum, StrEnum

__all__ = ["CheckResultStatus", "CheckStatus", "PingStatus"]


class CheckResultStatus(IntEnum):
    """单个检查项的结果严重程度。

    整数取值保证可比较（``PASS < WARN < FAIL``），使 :class:`~hdu_netdebugger.models.CheckResultBuilder`
    可以只在"更严重"时提升状态，而不会被后续的 ``passed()`` 降级 —— 与原版
    ``CheckResultBuilder.ChangeStatus`` 的语义一致。
    """

    PASS = 0
    WARN = 1
    FAIL = 2


class CheckStatus(StrEnum):
    """检查项在界面/报告中呈现的生命周期状态。"""

    UNCHECKED = "unchecked"
    CHECKING = "checking"
    PASSED = "passed"
    FAILED = "failed"
    WARNED = "warned"
    SKIPPED = "skipped"

    @property
    def label(self) -> str:
        """中文显示名。"""
        return _CHECK_STATUS_LABELS[self]

    @property
    def is_problem(self) -> bool:
        """是否为需要用户关注的状态（失败或警告）。"""
        return self in _PROBLEM_STATUSES

    @classmethod
    def from_result(cls, status: CheckResultStatus) -> CheckStatus:
        """把 :class:`CheckResultStatus` 映射为对外状态。"""
        return _RESULT_TO_STATUS[status]


_CHECK_STATUS_LABELS: dict[CheckStatus, str] = {
    CheckStatus.UNCHECKED: "未检查",
    CheckStatus.CHECKING: "检查中",
    CheckStatus.PASSED: "通过",
    CheckStatus.FAILED: "失败",
    CheckStatus.WARNED: "警告",
    CheckStatus.SKIPPED: "跳过",
}

_PROBLEM_STATUSES = frozenset({CheckStatus.FAILED, CheckStatus.WARNED})

_RESULT_TO_STATUS: dict[CheckResultStatus, CheckStatus] = {
    CheckResultStatus.PASS: CheckStatus.PASSED,
    CheckResultStatus.WARN: CheckStatus.WARNED,
    CheckResultStatus.FAIL: CheckStatus.FAILED,
}


class PingStatus(StrEnum):
    """单次 ICMP Echo 的结果。

    对应 C# ``System.Net.NetworkInformation.IPStatus`` 里我们关心的几个取值。
    """

    SUCCESS = "success"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    ERROR = "error"

    @property
    def label(self) -> str:
        """中文显示名。"""
        return _PING_STATUS_LABELS[self]


_PING_STATUS_LABELS: dict[PingStatus, str] = {
    PingStatus.SUCCESS: "成功",
    PingStatus.TIMEOUT: "超时",
    PingStatus.UNREACHABLE: "目标不可达",
    PingStatus.ERROR: "错误",
}
