"""报告生成与终端主题。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from hdu_netdebugger.models.enums import CheckStatus
from hdu_netdebugger.models.result import CheckOutcome
from hdu_netdebugger.report import (
    EXIT_FAIL,
    EXIT_OK,
    EXIT_WARN,
    REPORT_FOOTER,
    build_json,
    build_markdown,
    exit_code_for,
    summarize,
)
from hdu_netdebugger.theme import CHECK_STATUS_STYLES, UNICODE_ICONS, icon_for

CHECKED_AT = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
GENERATED_AT = datetime(2025, 1, 1, 12, 0, 5, tzinfo=UTC)


def outcome(
    name: str,
    status: CheckStatus,
    *,
    order: int = 0,
    summary: str = "总览",
    details: str = "详情",
    warnings: list[str] | None = None,
    suggestions: list[str] | None = None,
    skipped_reason: str | None = None,
) -> CheckOutcome:
    return CheckOutcome(
        name=name,
        order=order,
        status=status,
        summary=summary,
        details=details,
        warnings=warnings or [],
        suggestions=suggestions or [],
        skipped_reason=skipped_reason,
        duration_s=0.5,
    )


ALL_STATUSES = [
    outcome("通过项", CheckStatus.PASSED, order=1),
    outcome(
        "警告项",
        CheckStatus.WARNED,
        order=2,
        warnings=["有点问题"],
        suggestions=["重启试试"],
    ),
    outcome("失败项", CheckStatus.FAILED, order=3, warnings=["坏了"]),
    outcome("跳过项", CheckStatus.SKIPPED, order=4, skipped_reason="条件未满足"),
    outcome("未检查项", CheckStatus.UNCHECKED, order=5),
]


class TestSummarize:
    def test_counts_each_status(self) -> None:
        stats = summarize(ALL_STATUSES)
        assert stats.total == 5
        assert stats.passed == 1
        assert stats.warned == 1
        assert stats.failed == 1
        assert stats.skipped == 1
        assert stats.unchecked == 1
        assert stats.checked == 3
        assert stats.problems == 2

    def test_empty_list(self) -> None:
        stats = summarize([])
        assert stats.total == 0
        assert stats.headline == "共 0 项"

    def test_checking_counts_as_unchecked(self) -> None:
        stats = summarize([outcome("进行中", CheckStatus.CHECKING)])
        assert stats.unchecked == 1
        assert stats.checked == 0

    def test_headline_only_lists_non_zero(self) -> None:
        stats = summarize([outcome("a", CheckStatus.PASSED)])
        assert stats.headline == "共 1 项 · 通过 1"


class TestExitCode:
    def test_all_pass(self) -> None:
        assert exit_code_for([outcome("a", CheckStatus.PASSED)]) == EXIT_OK

    def test_warning(self) -> None:
        assert exit_code_for([outcome("a", CheckStatus.WARNED)]) == EXIT_WARN

    def test_failure_wins_over_warning(self) -> None:
        outcomes = [outcome("a", CheckStatus.WARNED), outcome("b", CheckStatus.FAILED)]
        assert exit_code_for(outcomes) == EXIT_FAIL

    def test_skipped_does_not_fail(self) -> None:
        assert exit_code_for([outcome("a", CheckStatus.SKIPPED)]) == EXIT_OK

    def test_empty_is_ok(self) -> None:
        assert exit_code_for([]) == EXIT_OK


class TestMarkdown:
    def test_header_and_footer(self) -> None:
        report = build_markdown([outcome("检查项", CheckStatus.PASSED)], checked_at=CHECKED_AT)
        assert report.startswith("# HDU Net Debugger 检查结果报告")
        assert "生成时间:" in report
        assert "检查时间: 2025-01-01 12:00:00" in report
        assert REPORT_FOOTER in report

    def test_missing_checked_at(self) -> None:
        report = build_markdown([outcome("检查项", CheckStatus.PASSED)])
        assert "检查时间: (未知)" in report

    def test_sections_include_status_and_details(self) -> None:
        report = build_markdown(
            [outcome("检查DNS可用性", CheckStatus.PASSED, details="DNS 正常")],
            checked_at=CHECKED_AT,
        )
        assert "## ✅ 检查DNS可用性" in report
        assert "- 状态: 通过 (`passed`)" in report
        assert "DNS 正常" in report
        assert "```text" in report

    def test_warning_and_suggestion_lists(self) -> None:
        report = build_markdown(
            [
                outcome(
                    "警告项",
                    CheckStatus.WARNED,
                    warnings=["第一条", "第二条"],
                    suggestions=["建议一"],
                )
            ],
            checked_at=CHECKED_AT,
        )
        assert "- 警告:" in report
        assert "  - 第一条" in report
        assert "  - 第二条" in report
        assert "- 建议:" in report
        assert "  - 建议一" in report

    def test_skipped_reason_is_rendered(self) -> None:
        report = build_markdown(
            [outcome("跳过项", CheckStatus.SKIPPED, skipped_reason="运营商ID非空且有效")],
            checked_at=CHECKED_AT,
        )
        assert "- 跳过原因: 运营商ID非空且有效" in report
        assert "⏭️" in report

    def test_empty_details_placeholder(self) -> None:
        report = build_markdown(
            [outcome("空详情", CheckStatus.PASSED, details="")],
            checked_at=CHECKED_AT,
        )
        assert "(无)" in report

    def test_summary_line_present(self) -> None:
        report = build_markdown(ALL_STATUSES, checked_at=CHECKED_AT)
        assert "结果概览: 共 5 项" in report


class TestJson:
    def test_structure(self) -> None:
        payload = json.loads(
            build_json(ALL_STATUSES, checked_at=CHECKED_AT, generated_at=GENERATED_AT)
        )
        assert payload["generator"] == "hdu-netdebugger-uwu"
        assert payload["checked_at"] == "2025-01-01T12:00:00+00:00"
        assert payload["generated_at"] == "2025-01-01T12:00:05+00:00"
        assert payload["summary"]["total"] == 5
        assert len(payload["checks"]) == 5
        assert payload["checks"][1]["warnings"] == ["有点问题"]

    def test_null_checked_at(self) -> None:
        payload = json.loads(build_json([], generated_at=GENERATED_AT))
        assert payload["checked_at"] is None

    def test_chinese_is_not_escaped(self) -> None:
        text = build_json([outcome("获取基本信息", CheckStatus.PASSED)], checked_at=CHECKED_AT)
        assert "获取基本信息" in text


class TestTheme:
    @pytest.mark.parametrize("status", list(CheckStatus))
    def test_every_status_has_icon_and_style(self, status: CheckStatus) -> None:
        assert icon_for(status)
        assert status in UNICODE_ICONS
        assert status in CHECK_STATUS_STYLES

    def test_ascii_fallback(self) -> None:
        assert icon_for(CheckStatus.PASSED, ascii_only=True) == "OK"
        assert icon_for(CheckStatus.FAILED, ascii_only=True) == "XX"

    def test_unicode_icons(self) -> None:
        assert icon_for(CheckStatus.PASSED) == "✔"
        assert icon_for(CheckStatus.FAILED) == "✖"
        assert icon_for(CheckStatus.WARNED) == "⚠"
