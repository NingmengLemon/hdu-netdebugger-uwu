"""检查报告生成.

Markdown 结构对齐原版 ``Utils/ResultExportUtils.cs`` 的 ``ReportBuilder``，
另外补上整体统计与 JSON 输出，方便脚本 / CI 消费。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from hdu_netdebugger import __version__
from hdu_netdebugger.models.enums import CheckStatus
from hdu_netdebugger.theme import CHECK_STATUS_EMOJI

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hdu_netdebugger.models.result import CheckOutcome

__all__ = [
    "REPORT_FOOTER",
    "ReportSummary",
    "build_json",
    "build_markdown",
    "exit_code_for",
    "summarize",
]

#: 退出码约定
EXIT_OK = 0
EXIT_WARN = 1
EXIT_FAIL = 2

REPORT_FOOTER = f"由 hdu-netdebugger-uwu v{__version__} 生成"


@dataclass(frozen=True, slots=True)
class ReportSummary:
    """整体统计。"""

    total: int
    passed: int
    warned: int
    failed: int
    skipped: int
    unchecked: int

    @property
    def checked(self) -> int:
        """实际执行过的检查项数量（不含跳过与未检查）。"""
        return self.passed + self.warned + self.failed

    @property
    def problems(self) -> int:
        """存在问题的检查项数量。"""
        return self.warned + self.failed

    @property
    def headline(self) -> str:
        """一行式结论。"""
        parts = [f"共 {self.total} 项"]
        if self.passed:
            parts.append(f"通过 {self.passed}")
        if self.warned:
            parts.append(f"警告 {self.warned}")
        if self.failed:
            parts.append(f"失败 {self.failed}")
        if self.skipped:
            parts.append(f"跳过 {self.skipped}")
        if self.unchecked:
            parts.append(f"未检查 {self.unchecked}")
        return " · ".join(parts)


def summarize(outcomes: Sequence[CheckOutcome]) -> ReportSummary:
    """统计各状态的数量。"""
    counts = dict.fromkeys(CheckStatus, 0)
    for outcome in outcomes:
        counts[outcome.status] += 1
    return ReportSummary(
        total=len(outcomes),
        passed=counts[CheckStatus.PASSED],
        warned=counts[CheckStatus.WARNED],
        failed=counts[CheckStatus.FAILED],
        skipped=counts[CheckStatus.SKIPPED],
        unchecked=counts[CheckStatus.UNCHECKED] + counts[CheckStatus.CHECKING],
    )


def exit_code_for(outcomes: Sequence[CheckOutcome]) -> int:
    """把结果映射成进程退出码：``0`` 全通过 / ``1`` 有警告 / ``2`` 有失败。"""
    summary = summarize(outcomes)
    if summary.failed:
        return EXIT_FAIL
    if summary.warned:
        return EXIT_WARN
    return EXIT_OK


def build_markdown(
    outcomes: Sequence[CheckOutcome],
    *,
    checked_at: datetime | None = None,
    generated_at: datetime | None = None,
) -> str:
    """生成 Markdown 报告。"""
    generated = generated_at or datetime.now()
    summary = summarize(outcomes)

    lines = [
        "# HDU Net Debugger 检查结果报告",
        "",
        f"生成时间: {generated:%Y-%m-%d %H:%M:%S}",
        f"检查时间: {checked_at:%Y-%m-%d %H:%M:%S}" if checked_at else "检查时间: (未知)",
        f"结果概览: {summary.headline}",
        "",
    ]

    for outcome in outcomes:
        lines.extend(_markdown_section(outcome))

    lines.extend(["---", "", REPORT_FOOTER, ""])
    return "\n".join(lines)


def _markdown_section(outcome: CheckOutcome) -> list[str]:
    """渲染单个检查项的 Markdown 段落。"""
    emoji = CHECK_STATUS_EMOJI[outcome.status]
    lines = [
        f"## {emoji} {outcome.name}",
        "",
        f"- 状态: {outcome.status.label} (`{outcome.status.value}`)",
        f"- 总览: {outcome.summary or '(无)'}",
    ]

    if outcome.skipped_reason:
        lines.append(f"- 跳过原因: {outcome.skipped_reason}")

    if outcome.suggestions:
        lines.append("- 建议:")
        lines.extend(f"  - {item}" for item in outcome.suggestions)

    if outcome.warnings:
        lines.append("- 警告:")
        lines.extend(f"  - {item}" for item in outcome.warnings)

    lines.extend(
        [
            f"- 耗时: {outcome.duration_s:.2f}s",
            "- 详情:",
            "",
            "```text",
            outcome.details or "(无)",
            "```",
            "",
        ]
    )
    return lines


def build_json(
    outcomes: Sequence[CheckOutcome],
    *,
    checked_at: datetime | None = None,
    generated_at: datetime | None = None,
) -> str:
    """生成 JSON 报告（UTF-8，保留中文原文）。"""
    generated = generated_at or datetime.now()
    summary = summarize(outcomes)
    payload = {
        "generator": "hdu-netdebugger-uwu",
        "version": __version__,
        "generated_at": generated.isoformat(timespec="seconds"),
        "checked_at": checked_at.isoformat(timespec="seconds") if checked_at else None,
        "summary": {
            "total": summary.total,
            "passed": summary.passed,
            "warned": summary.warned,
            "failed": summary.failed,
            "skipped": summary.skipped,
            "unchecked": summary.unchecked,
        },
        "checks": [outcome.to_dict() for outcome in outcomes],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
