"""检查结果模型与构建器.

对应 C# 原版的 ``Models/CheckResult.cs`` 与 ``Services/CheckResultBuilder.cs``。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hdu_netdebugger.models.enums import CheckResultStatus, CheckStatus

__all__ = ["CheckOutcome", "CheckResult", "CheckResultBuilder"]


@dataclass(slots=True)
class CheckResult:
    """单个检查项的执行结果。"""

    status: CheckResultStatus = CheckResultStatus.PASS
    summary: str = ""
    details: str | None = None
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


class CheckResultBuilder:
    """按原版语义累积 :class:`CheckResult`。

    与 C# 实现保持一致的两条规则：

    1. 状态只会"更严重"（``PASS < WARN < FAIL``），不会被后续调用降级；
    2. 警告与建议自动去重，且空白字符串会被忽略。

    添加警告会把 ``PASS`` 提升为 ``WARN``，添加建议不会改变状态。
    """

    __slots__ = ("_details", "_status", "_suggestions", "_summary", "_warnings")

    def __init__(self) -> None:
        self._status = CheckResultStatus.PASS
        self._summary = ""
        self._details: str | None = None
        self._warnings: list[str] = []
        self._suggestions: list[str] = []

    # -- 状态设置 ---------------------------------------------------------

    def passed(self, summary: str, details: str | None = None) -> CheckResultBuilder:
        """标记为通过。"""
        return self._settle(CheckResultStatus.PASS, summary, details)

    def warned(self, summary: str, details: str | None = None) -> CheckResultBuilder:
        """标记为警告。"""
        return self._settle(CheckResultStatus.WARN, summary, details)

    def failed(self, summary: str, details: str | None = None) -> CheckResultBuilder:
        """标记为失败。"""
        return self._settle(CheckResultStatus.FAIL, summary, details)

    def _settle(
        self,
        status: CheckResultStatus,
        summary: str,
        details: str | None,
    ) -> CheckResultBuilder:
        self._status = max(self._status, status)
        self._summary = summary
        self._details = details
        return self

    # -- 附加信息 ---------------------------------------------------------

    def add_warning(self, message: str) -> CheckResultBuilder:
        """追加一条警告；同时把 ``PASS`` 提升为 ``WARN``。"""
        if message.strip() and message not in self._warnings:
            self._warnings.append(message)
        if self._status is CheckResultStatus.PASS:
            self._status = CheckResultStatus.WARN
        return self

    def add_suggestion(self, suggestion: str) -> CheckResultBuilder:
        """追加一条建议（去重，不影响状态）。"""
        if suggestion.strip() and suggestion not in self._suggestions:
            self._suggestions.append(suggestion)
        return self

    # -- 读取 -------------------------------------------------------------

    @property
    def status(self) -> CheckResultStatus:
        """当前累积出的状态。"""
        return self._status

    def build(self) -> CheckResult:
        """产出不可变语义的结果快照（内部列表已复制）。"""
        return CheckResult(
            status=self._status,
            summary=self._summary,
            details=self._details,
            warnings=list(self._warnings),
            suggestions=list(self._suggestions),
        )

    def reset(self) -> None:
        """恢复初始状态，供同一个检查项实例复用。"""
        self._status = CheckResultStatus.PASS
        self._summary = ""
        self._details = None
        self._warnings.clear()
        self._suggestions.clear()


@dataclass(slots=True)
class CheckOutcome:
    """一个检查项在一次运行中的最终产出。

    这是 CLI / GUI / 报告层唯一消费的结构，对应原版 ``CheckItemViewModel``
    在界面绑定中暴露的那几个字段。
    """

    name: str
    order: int
    status: CheckStatus
    summary: str = ""
    details: str = ""
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    #: 因条件不满足而跳过时的说明（仅 ``status is SKIPPED`` 时有值）
    skipped_reason: str | None = None
    #: 该检查项耗时（秒）
    duration_s: float = 0.0

    @property
    def is_problem(self) -> bool:
        """是否为需要用户关注的结果。"""
        return self.status.is_problem

    def to_dict(self) -> dict[str, object]:
        """转换为可 JSON 序列化的字典。"""
        return {
            "name": self.name,
            "order": self.order,
            "status": self.status.value,
            "status_label": self.status.label,
            "summary": self.summary,
            "details": self.details,
            "warnings": list(self.warnings),
            "suggestions": list(self.suggestions),
            "skipped_reason": self.skipped_reason,
            "duration_s": round(self.duration_s, 3),
        }
