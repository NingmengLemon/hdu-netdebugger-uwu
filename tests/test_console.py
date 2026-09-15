"""终端渲染：verbose 详情、doctor 表格、检查项清单、结论汇总.

重点是 rich markup 的转义：警告 / 建议 / 详情里装的是**网络原文**
（重定向 Location、代理地址、接口描述、异常消息），方括号若被当成样式标签，
轻则静默吃掉文本，重则抛 ``MarkupError`` 把 ``--verbose`` 直接弄崩。
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from hdu_netdebugger.checkers import load_builtin_checkers
from hdu_netdebugger.console import (
    ConsoleReporter,
    render_checker_table,
    render_doctor,
)
from hdu_netdebugger.models.enums import CheckStatus
from hdu_netdebugger.models.result import CheckOutcome
from hdu_netdebugger.registry import all_checkers, get_checker
from hdu_netdebugger.runner import ProgressEvent, RunResult

load_builtin_checkers()


def recording_console() -> tuple[Console, StringIO]:
    """不带颜色、足够宽的 Console，渲染结果写进 StringIO。"""
    buffer = StringIO()
    console = Console(file=buffer, width=200, no_color=True, highlight=False, emoji=False)
    return console, buffer


def make_event(outcome: CheckOutcome) -> ProgressEvent:
    """按 outcome 的名称取到已注册的检查项，构造一个进度事件。"""
    return ProgressEvent(spec=get_checker(outcome.name), index=0, total=1, outcome=outcome)


class TestVerboseRendering:
    def test_warning_and_suggestion_keep_literal_brackets(self) -> None:
        """回归：``[red]`` 曾被 rich 当成样式标签静默吃掉，打印出来的 URL 与真实的不同。"""
        console, buffer = recording_console()
        reporter = ConsoleReporter(console, verbose=True)
        outcome = CheckOutcome(
            name="检查公网可达性",
            order=35,
            status=CheckStatus.FAILED,
            summary="公网不可达",
            warnings=["HTTP 请求被重定向到 http://portal.hdu.edu.cn/[red]login，疑似门户劫持"],
            suggestions=["请确认已完成校园网认证 [yellow]"],
        )

        reporter.on_progress(make_event(outcome))

        rendered = buffer.getvalue()
        assert "[red]login" in rendered
        assert "[yellow]" in rendered

    def test_unbalanced_closing_tag_does_not_raise(self) -> None:
        """回归：详情里出现 ``[/]`` 时 rich 会抛 MarkupError，把 ``-v`` 弄崩。"""
        console, buffer = recording_console()
        reporter = ConsoleReporter(console, verbose=True)
        outcome = CheckOutcome(
            name="检查公网可达性",
            order=35,
            status=CheckStatus.FAILED,
            summary="公网不可达",
            details="请求失败: ConnectError: [/]",
        )

        reporter.on_progress(make_event(outcome))

        assert "ConnectError: [/]" in buffer.getvalue()

    def test_details_are_skipped_when_not_verbose(self) -> None:
        console, buffer = recording_console()
        reporter = ConsoleReporter(console, verbose=False)
        outcome = CheckOutcome(
            name="检查公网可达性",
            order=35,
            status=CheckStatus.PASSED,
            summary="公网可达",
            details="这句不该出现",
        )

        reporter.on_progress(make_event(outcome))

        assert "这句不该出现" not in buffer.getvalue()


class TestSummaryRendering:
    def test_summary_shows_headline_and_actionables(self) -> None:
        console, buffer = recording_console()
        reporter = ConsoleReporter(console)
        result = RunResult(
            outcomes=[
                CheckOutcome(
                    name="检查公网可达性",
                    order=35,
                    status=CheckStatus.FAILED,
                    summary="公网不可达",
                    warnings=["阿里主DNS不可达。"],
                    suggestions=["请检查IP配置或尝试重新进行物理连接"],
                )
            ],
            duration_s=1.5,
        )

        reporter.summary(result)

        rendered = buffer.getvalue()
        assert "检查结论" in rendered
        assert "发现 1 项失败" in rendered
        assert "请检查IP配置或尝试重新进行物理连接" in rendered


class TestDoctorRendering:
    def test_backend_error_text_is_escaped(self) -> None:
        """依赖探测的失败信息（异常字符串）同样会带方括号。"""
        console, buffer = recording_console()

        console.print(render_doctor([("Ping 后端 system", "不可用 [red]oops", False, "")]))

        assert "[red]oops" in buffer.getvalue()

    def test_pass_and_note_are_rendered(self) -> None:
        console, buffer = recording_console()

        console.print(render_doctor([("依赖 httpx", "已安装", True, "hnd gui 需要它")]))

        rendered = buffer.getvalue()
        assert "依赖 httpx" in rendered
        assert "hnd gui 需要它" in rendered


class TestCheckerTableRendering:
    def test_lists_names_and_conditions(self) -> None:
        console, buffer = recording_console()

        console.print(render_checker_table(all_checkers()))

        rendered = buffer.getvalue()
        assert "检查DNS可用性" in rendered
        assert "运营商ID非空且有效" in rendered
