"""检查编排.

对应原版 ``ViewModels/MainViewModel.RunChecks`` 与 ``CheckItemViewModel.RunAsync``：
按 ``order`` 顺序执行检查项，先判定前置条件，条件不满足就标记为「跳过」。

与原版的差别：

* 用 :class:`ProgressEvent` 回调把过程实时抛给 CLI / GUI，而不是让编排逻辑依赖界面框架；
* 记录每项耗时，并按 ``(order, name)`` 稳定排序产出。

典型用法::

    result = await run_checks(only=["检查DNS可用性", "检查认证状态"], verbose=True)
    for outcome in result.outcomes:
        print(outcome.status.label, outcome.name, outcome.summary)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from hdu_netdebugger import checkers as _checkers  # noqa: F401 - 导入即注册内置检查项
from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.models.enums import CheckStatus
from hdu_netdebugger.models.result import CheckOutcome
from hdu_netdebugger.net.ping import Backend
from hdu_netdebugger.registry import CheckerSpec, all_checkers, resolve_names
from hdu_netdebugger.session import RunSession

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["ProgressEvent", "RunResult", "run_checks", "select_checkers"]


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """执行过程中的进度通知。"""

    spec: CheckerSpec
    index: int
    total: int
    outcome: CheckOutcome


@dataclass(slots=True)
class RunResult:
    """一次完整运行的产出。"""

    outcomes: list[CheckOutcome] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    duration_s: float = 0.0
    #: 本次运行实际使用的 session（含跨检查项状态），供调用方检查
    session: RunSession | None = None

    def by_name(self, name: str) -> CheckOutcome | None:
        """按名称取结果。"""
        for outcome in self.outcomes:
            if outcome.name == name:
                return outcome
        return None


def select_checkers(
    *,
    only: Sequence[str] | None = None,
    skip: Sequence[str] | None = None,
) -> list[CheckerSpec]:
    """按 ``--only`` / ``--skip`` 过滤出待执行的检查项。

    Args:
        only: 只执行这些（名称 / 子串 / order），为空表示全部。
        skip: 从结果中排除这些。

    Returns:
        按 ``(order, name)`` 排序的检查项列表。

    Raises:
        KeyError: 某个名称匹配不到检查项。
    """
    selected = resolve_names(list(only)) if only else all_checkers()
    if not skip:
        return list(selected)
    excluded = {spec.name for spec in resolve_names(list(skip))}
    return [spec for spec in selected if spec.name not in excluded]


async def run_checks(
    *,
    config: RunConfig | None = None,
    only: Sequence[str] | None = None,
    skip: Sequence[str] | None = None,
    verbose: bool = False,
    ping_backend: Backend | None = None,
    session: RunSession | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> RunResult:
    """执行检查项并汇总结论。

    Args:
        config: 运行配置；为 ``None`` 时使用默认值。
        only: 只执行这些检查项。
        skip: 跳过这些检查项。
        verbose: 是否输出详细过程（供调用方读取 ``session.verbose``）。
        ping_backend: 强制指定的 Ping 后端。
        session: 复用的 session（测试或多次运行共享状态时使用）。
        on_progress: 每完成一个检查项就回调一次。

    Returns:
        包含全部检查项结论的 :class:`RunResult`。
    """
    active = session or RunSession(config=config or RunConfig())
    if config is not None:
        active.config = config
    active.verbose = verbose
    active.ping_backend = ping_backend if ping_backend is not None else Backend.AUTO
    active.context.reset()

    specs = select_checkers(only=only, skip=skip)
    started = datetime.now()
    clock = time.perf_counter()
    outcomes: list[CheckOutcome] = []

    try:
        for index, spec in enumerate(specs):
            outcome = await _run_one(spec, active)
            outcomes.append(outcome)
            if on_progress is not None:
                on_progress(
                    ProgressEvent(
                        spec=spec,
                        index=index,
                        total=len(specs),
                        outcome=outcome,
                    )
                )
    finally:
        if session is None:
            await active.aclose()

    return RunResult(
        outcomes=outcomes,
        started_at=started,
        duration_s=time.perf_counter() - clock,
        session=active,
    )


async def _run_one(spec: CheckerSpec, session: RunSession) -> CheckOutcome:
    """执行单个检查项，处理前置条件与计时。"""
    instance = spec.create(session)
    clock = time.perf_counter()

    skipped_reason = await _first_failed_condition(spec, instance)
    if skipped_reason is not None:
        return CheckOutcome(
            name=spec.name,
            order=spec.order,
            status=CheckStatus.SKIPPED,
            summary="检查被跳过",
            details=f'条件 "{skipped_reason}" 未满足，跳过此检查',
            skipped_reason=skipped_reason,
            duration_s=time.perf_counter() - clock,
        )

    result = await instance.execute()
    return CheckOutcome(
        name=spec.name,
        order=spec.order,
        status=CheckStatus.from_result(result.status),
        summary=result.summary,
        details=result.details or "",
        warnings=list(result.warnings),
        suggestions=list(result.suggestions),
        duration_s=time.perf_counter() - clock,
    )


async def _first_failed_condition(spec: CheckerSpec, instance: object) -> str | None:
    """返回第一个不满足的条件名；全部满足时返回 ``None``。"""
    for condition in spec.conditions:
        try:
            satisfied = await condition.evaluate(instance)  # ty: ignore[invalid-argument-type]
        except Exception:
            return condition.name
        if not satisfied:
            return condition.name
    return None
