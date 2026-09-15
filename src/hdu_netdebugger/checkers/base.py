"""检查项基类与详细日志容器."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from hdu_netdebugger.models.result import CheckResult, CheckResultBuilder

if TYPE_CHECKING:
    from collections.abc import Iterable

    from hdu_netdebugger.config import RunConfig
    from hdu_netdebugger.context import CheckContext

__all__ = ["Checker", "DetailsLog"]


class DetailsLog:
    """逐行累积的详细日志。

    对应 C# 原版 ``CheckerBase`` 里的 ``StringBuilder DetailsBuilder``，但把
    "追加 + 结束时 ``TrimEnd`` 换行" 收敛成一次性渲染。
    """

    __slots__ = ("_lines",)

    def __init__(self) -> None:
        self._lines: list[str] = []

    def line(self, text: str = "") -> None:
        """追加一行。"""
        self._lines.append(text)

    def extend(self, lines: Iterable[str]) -> None:
        """批量追加多行。"""
        self._lines.extend(lines)

    def clear(self) -> None:
        """清空。"""
        self._lines.clear()

    @property
    def text(self) -> str:
        """渲染为字符串，去掉首尾空白行（等价于原版的 ``TrimEnd``）。"""
        return "\n".join(self._lines).strip("\n")

    def __bool__(self) -> bool:
        return bool(self._lines)

    def __len__(self) -> int:
        return len(self._lines)


class Checker(ABC):
    """所有检查项的基类。

    子类实现 :meth:`check`，并通过 :meth:`log` 写详细日志、通过
    :meth:`passed` / :meth:`warned` / :meth:`failed` 给出最终结论。
    ``name`` 与 ``order`` 由 :func:`hdu_netdebugger.registry.checker` 装饰器注入。
    """

    #: 检查项显示名（由装饰器填充）
    name: ClassVar[str] = ""
    #: 执行顺序，越小越先执行（由装饰器填充）
    order: ClassVar[int] = 0

    def __init__(self, context: CheckContext, config: RunConfig) -> None:
        self.context = context
        self.config = config
        self._builder = CheckResultBuilder()
        self._details = DetailsLog()

    # -- 生命周期 ---------------------------------------------------------

    @abstractmethod
    async def check(self) -> None:
        """执行检查并把结论写入内部 builder。"""

    async def execute(self) -> CheckResult:
        """执行检查，捕获任何异常并转成失败结果（等价于原版 ``ExecuteAsync``）。"""
        self.reset()
        try:
            await self.check()
        except Exception as exc:
            return self._builder.failed(
                f"检查执行失败: {exc}",
                f"异常详情: {exc!r}",
            ).build()
        return self._builder.build()

    def reset(self) -> None:
        """清空内部状态，供同一个实例复用。"""
        self._builder.reset()
        self._details.clear()

    # -- 结论 -------------------------------------------------------------

    @property
    def result(self) -> CheckResult:
        """当前累积的结果快照。"""
        return self._builder.build()

    def passed(self, summary: str) -> None:
        """以"通过"结束检查，详情取当前日志。"""
        self._builder.passed(summary, self._details.text)

    def warned(self, summary: str) -> None:
        """以"警告"结束检查，详情取当前日志。"""
        self._builder.warned(summary, self._details.text)

    def failed(self, summary: str) -> None:
        """以"失败"结束检查，详情取当前日志。"""
        self._builder.failed(summary, self._details.text)

    def add_warning(self, message: str) -> None:
        """追加一条警告（会把"通过"提升为"警告"）。"""
        self._builder.add_warning(message)

    def add_suggestion(self, suggestion: str) -> None:
        """追加一条建议。"""
        self._builder.add_suggestion(suggestion)

    # -- 日志 -------------------------------------------------------------

    def log(self, text: str = "") -> None:
        """追加一行详细日志。"""
        self._details.line(text)

    def log_lines(self, lines: Iterable[str]) -> None:
        """批量追加详细日志。"""
        self._details.extend(lines)
