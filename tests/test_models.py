"""结果模型与构建器语义。"""

from __future__ import annotations

import pytest

from hdu_netdebugger.models.enums import CheckResultStatus, CheckStatus, PingStatus
from hdu_netdebugger.models.result import CheckOutcome, CheckResultBuilder


class TestCheckResultBuilder:
    def test_default_is_pass(self) -> None:
        result = CheckResultBuilder().build()
        assert result.status is CheckResultStatus.PASS
        assert result.summary == ""
        assert result.details is None
        assert result.warnings == []
        assert result.suggestions == []

    def test_settle_sets_summary_and_details(self) -> None:
        result = CheckResultBuilder().passed("一切正常", "详细日志").build()
        assert result.status is CheckResultStatus.PASS
        assert result.summary == "一切正常"
        assert result.details == "详细日志"

    def test_status_only_escalates(self) -> None:
        """先失败再"通过"不能把结论降级 —— 与原版 ChangeStatus 语义一致。"""
        builder = CheckResultBuilder()
        builder.failed("炸了", "详情")
        builder.passed("其实没事")
        result = builder.build()
        assert result.status is CheckResultStatus.FAIL
        assert result.summary == "其实没事"

    def test_warn_escalates_from_pass(self) -> None:
        builder = CheckResultBuilder()
        builder.passed("通过")
        builder.warned("但有问题")
        assert builder.status is CheckResultStatus.WARN

    def test_add_warning_escalates_pass_to_warn(self) -> None:
        builder = CheckResultBuilder()
        builder.passed("通过")
        builder.add_warning("有点小问题")
        result = builder.build()
        assert result.status is CheckResultStatus.WARN
        assert result.warnings == ["有点小问题"]

    def test_add_warning_does_not_downgrade_fail(self) -> None:
        builder = CheckResultBuilder()
        builder.failed("失败")
        builder.add_warning("警告")
        assert builder.build().status is CheckResultStatus.FAIL

    def test_add_suggestion_does_not_change_status(self) -> None:
        builder = CheckResultBuilder()
        builder.passed("通过")
        builder.add_suggestion("建议重启")
        result = builder.build()
        assert result.status is CheckResultStatus.PASS
        assert result.suggestions == ["建议重启"]

    def test_warnings_and_suggestions_are_deduplicated(self) -> None:
        builder = CheckResultBuilder()
        builder.add_warning("重复")
        builder.add_warning("重复")
        builder.add_suggestion("重复")
        builder.add_suggestion("重复")
        result = builder.build()
        assert result.warnings == ["重复"]
        assert result.suggestions == ["重复"]

    @pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
    def test_blank_messages_are_ignored(self, blank: str) -> None:
        builder = CheckResultBuilder()
        builder.add_warning(blank)
        builder.add_suggestion(blank)
        result = builder.build()
        assert result.warnings == []
        assert result.suggestions == []
        assert result.status is CheckResultStatus.PASS

    def test_reset_clears_everything(self) -> None:
        builder = CheckResultBuilder()
        builder.failed("失败", "详情").add_warning("警告").add_suggestion("建议")
        builder.reset()
        result = builder.build()
        assert result.status is CheckResultStatus.PASS
        assert result.summary == ""
        assert result.details is None
        assert result.warnings == []
        assert result.suggestions == []

    def test_build_returns_independent_snapshots(self) -> None:
        """build() 之后再改 builder 不应影响已产出的结果。"""
        builder = CheckResultBuilder()
        builder.add_warning("第一条")
        first = builder.build()
        builder.add_warning("第二条")
        assert first.warnings == ["第一条"]


class TestCheckStatus:
    @pytest.mark.parametrize(
        ("result_status", "expected"),
        [
            (CheckResultStatus.PASS, CheckStatus.PASSED),
            (CheckResultStatus.WARN, CheckStatus.WARNED),
            (CheckResultStatus.FAIL, CheckStatus.FAILED),
        ],
    )
    def test_from_result(self, result_status: CheckResultStatus, expected: CheckStatus) -> None:
        assert CheckStatus.from_result(result_status) is expected

    @pytest.mark.parametrize(
        ("status", "label"),
        [
            (CheckStatus.UNCHECKED, "未检查"),
            (CheckStatus.CHECKING, "检查中"),
            (CheckStatus.PASSED, "通过"),
            (CheckStatus.FAILED, "失败"),
            (CheckStatus.WARNED, "警告"),
            (CheckStatus.SKIPPED, "跳过"),
        ],
    )
    def test_labels(self, status: CheckStatus, label: str) -> None:
        assert status.label == label

    def test_is_problem(self) -> None:
        assert CheckStatus.FAILED.is_problem
        assert CheckStatus.WARNED.is_problem
        assert not CheckStatus.PASSED.is_problem
        assert not CheckStatus.SKIPPED.is_problem


class TestPingStatus:
    def test_labels(self) -> None:
        assert PingStatus.SUCCESS.label == "成功"
        assert PingStatus.TIMEOUT.label == "超时"
        assert PingStatus.UNREACHABLE.label == "目标不可达"
        assert PingStatus.ERROR.label == "错误"


class TestCheckOutcome:
    def test_to_dict_is_json_ready(self) -> None:
        outcome = CheckOutcome(
            name="检查DNS可用性",
            order=20,
            status=CheckStatus.WARNED,
            summary="有警告",
            warnings=["w"],
            suggestions=["s"],
            duration_s=0.123456,
        )
        payload = outcome.to_dict()
        assert payload["name"] == "检查DNS可用性"
        assert payload["status"] == "warned"
        assert payload["status_label"] == "警告"
        assert payload["duration_s"] == 0.123
        assert payload["skipped_reason"] is None
        assert payload["warnings"] == ["w"]

    def test_is_problem(self) -> None:
        assert CheckOutcome(name="a", order=0, status=CheckStatus.FAILED).is_problem
        assert not CheckOutcome(name="a", order=0, status=CheckStatus.PASSED).is_problem
