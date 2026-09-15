"""终端渲染.

把 :class:`~hdu_netdebugger.runner.RunResult` 画成人类可读的输出。
渲染逻辑与 CLI 参数解析分开，方便单独测试，也方便 ``--json`` 时整体关掉。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hdu_netdebugger import __version__
from hdu_netdebugger.report import summarize
from hdu_netdebugger.theme import CHECK_STATUS_STYLES, icon_for

if TYPE_CHECKING:
    from rich.console import RenderableType

    from hdu_netdebugger.models.enums import CheckStatus
    from hdu_netdebugger.models.result import CheckOutcome
    from hdu_netdebugger.registry import CheckerSpec
    from hdu_netdebugger.runner import ProgressEvent, RunResult

__all__ = ["ConsoleReporter", "render_checker_table", "render_doctor"]


class ConsoleReporter:
    """把运行过程与结果输出到终端。

    ``verbose`` 打开时额外打印每一项的详细日志。
    """

    def __init__(self, console: Console, *, verbose: bool = False) -> None:
        self.console = console
        self.verbose = verbose
        self._total = 0

    # -- 运行过程 ---------------------------------------------------------

    def banner(self, total: int) -> None:
        """打印起始横幅。"""
        self._total = total
        self.console.print(
            Panel(
                Text.assemble(
                    ("HDU Net Debugger ", "bold cyan"),
                    (f"v{__version__}", "dim"),
                    ("  ·  ", "dim"),
                    (f"共 {total} 项检查", "dim"),
                ),
                border_style="cyan",
                padding=(0, 1),
            )
        )

    def on_progress(self, event: ProgressEvent) -> None:
        """单个检查项完成时输出一行。"""
        outcome = event.outcome
        style = CHECK_STATUS_STYLES[outcome.status]
        icon = icon_for(outcome.status)

        line = Text()
        line.append(f"{icon} ", style=style)
        line.append(f"{outcome.name}", style="bold" if outcome.is_problem else "")
        line.append(f"  {outcome.summary}", style=style)
        line.append(f"  ({outcome.duration_s:.2f}s)", style="dim")
        self.console.print(line)

        if self.verbose:
            self._render_details(outcome)

    def _render_details(self, outcome: CheckOutcome) -> None:
        """缩进输出单项的警告 / 建议 / 详细日志。"""
        for warning in outcome.warnings:
            self.console.print(f"    [yellow]警告:[/] {warning}")
        for suggestion in outcome.suggestions:
            self.console.print(f"    [cyan]建议:[/] {suggestion}")
        if outcome.skipped_reason:
            self.console.print(f"    [dim]跳过原因: {outcome.skipped_reason}[/]")
        if outcome.details:
            for raw in outcome.details.splitlines():
                self.console.print(f"    [dim]│ {raw}[/]")

    # -- 汇总 -------------------------------------------------------------

    def summary(self, result: RunResult) -> None:
        """打印整体结论与行动建议。"""
        stats = summarize(result.outcomes)
        style = self._overall_style(stats.failed, stats.warned)

        self.console.print()
        self.console.print(
            Panel(
                Text.assemble(
                    (self._overall_title(stats.failed, stats.warned), f"bold {style}"),
                    ("\n"),
                    (stats.headline, ""),
                    ("\n"),
                    (f"总耗时 {result.duration_s:.2f}s", "dim"),
                ),
                title="检查结论",
                border_style=style,
                padding=(0, 1),
            )
        )

        actionable = [item for item in result.outcomes if item.suggestions or item.warnings]
        if not actionable:
            return

        table = Table(title="待处理事项", show_lines=True, header_style="bold")
        table.add_column("检查项", style="bold", no_wrap=True)
        table.add_column("状态", no_wrap=True)
        table.add_column("警告 / 建议", overflow="fold")
        for outcome in actionable:
            table.add_row(
                outcome.name,
                Text(outcome.status.label, style=CHECK_STATUS_STYLES[outcome.status]),
                self._actionable_text(outcome),
            )
        self.console.print(table)

    @staticmethod
    def _actionable_text(outcome: CheckOutcome) -> Text:
        text = Text()
        for warning in outcome.warnings:
            text.append("⚠ ", style="yellow")
            text.append(f"{warning}\n")
        for suggestion in outcome.suggestions:
            text.append("💡 ", style="cyan")
            text.append(f"{suggestion}\n")
        text.rstrip()
        return text

    @staticmethod
    def _overall_style(failed: int, warned: int) -> str:
        if failed:
            return "red"
        if warned:
            return "yellow"
        return "green"

    @staticmethod
    def _overall_title(failed: int, warned: int) -> str:
        if failed:
            return f"发现 {failed} 项失败"
        if warned:
            return f"发现 {warned} 项警告"
        return "全部通过"


def render_checker_table(specs: list[CheckerSpec]) -> RenderableType:
    """渲染「检查项清单」表格。"""
    table = Table(title="已注册的检查项", header_style="bold", show_lines=False)
    table.add_column("序号", justify="right", style="dim", no_wrap=True)
    table.add_column("名称", style="bold", no_wrap=True)
    table.add_column("前置条件", overflow="fold")
    table.add_column("说明", overflow="fold")
    for spec in specs:
        conditions = "、".join(item.name for item in spec.conditions) or "—"
        table.add_row(
            str(spec.order),
            spec.name,
            conditions,
            (spec.cls.__doc__ or "").strip().splitlines()[0] if spec.cls.__doc__ else "—",
        )
    return table


def render_doctor(items: list[tuple[str, str, bool, str]]) -> RenderableType:
    """渲染「环境自检」表格。

    Args:
        items: ``(项目, 结果, 是否通过, 备注)`` 四元组列表。
    """
    table = Table(title="运行环境自检", header_style="bold", show_lines=False)
    table.add_column("项目", style="bold", no_wrap=True)
    table.add_column("结果", overflow="fold")
    table.add_column("", no_wrap=True)
    for label, value, ok, note in items:
        table.add_row(
            label,
            value,
            Text("✔", style="green") if ok else Text("✖", style="red"),
        )
        if note:
            table.add_row("", Text(note, style="dim"), "")
    return table


def render_check_outcome(outcome: CheckOutcome) -> RenderableType:
    """把单个检查项渲染成一段可折叠内容（GUI/调试用）。"""
    blocks: list[RenderableType] = [
        Text.assemble(
            (f"{icon_for(outcome.status)} ", CHECK_STATUS_STYLES[outcome.status]),
            (f"{outcome.name}\n", "bold"),
            (outcome.summary, CHECK_STATUS_STYLES[outcome.status]),
        )
    ]
    if outcome.warnings:
        blocks.append(Text("\n".join(f"⚠ {item}" for item in outcome.warnings), style="yellow"))
    if outcome.suggestions:
        blocks.append(Text("\n".join(f"💡 {item}" for item in outcome.suggestions), style="cyan"))
    if outcome.details:
        blocks.append(Text(outcome.details, style="dim"))
    return Group(*blocks)


def make_console(*, stderr: bool = False, quiet: bool = False) -> Console:
    """构造统一的 rich Console。"""
    return Console(
        stderr=stderr,
        quiet=quiet,
        highlight=False,
        soft_wrap=False,
        emoji=True,
    )


def is_problem_status(status: CheckStatus) -> bool:
    """状态是否需要用户关注。"""
    return status.is_problem
