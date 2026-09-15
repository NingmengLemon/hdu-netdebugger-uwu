"""编排器：顺序、条件跳过、过滤、取消、进度回调。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.models.enums import CheckStatus
from hdu_netdebugger.registry import checker, condition
from hdu_netdebugger.runner import ProgressEvent, RunResult, run_checks, select_checkers
from tests.fakes import FakeHttp, FakePinger, make_session

if TYPE_CHECKING:
    from hdu_netdebugger.session import RunSession


class _Recorder:
    """记录执行顺序的共享容器。"""

    def __init__(self) -> None:
        self.order: list[str] = []


RECORDER = _Recorder()


@checker("编排测试-甲", order=1001)
class _Alpha(Checker):
    async def check(self) -> None:
        RECORDER.order.append(self.name)
        self.log("alpha 日志")
        self.passed("甲通过")


@checker("编排测试-乙", order=1002)
class _Beta(Checker):
    async def check(self) -> None:
        RECORDER.order.append(self.name)
        self.add_warning("乙有点问题")
        self.warned("乙警告")


@checker("编排测试-丙", order=1003)
class _Gamma(Checker):
    async def check(self) -> None:
        RECORDER.order.append(self.name)
        raise RuntimeError("丙炸了")


@checker("编排测试-丁", order=1004)
class _Delta(Checker):
    @condition("环境就绪")
    def _ready(self) -> bool:
        return False

    async def check(self) -> None:  # pragma: no cover - 条件不满足不会执行
        RECORDER.order.append(self.name)
        self.passed("丁通过")


@checker("编排测试-戊", order=1005)
class _Epsilon(Checker):
    @condition("条件自身出错")
    def _boom(self) -> bool:
        raise RuntimeError("条件炸了")

    async def check(self) -> None:  # pragma: no cover - 条件异常会被判为不满足
        RECORDER.order.append(self.name)
        self.passed("戊通过")


NAMES = ["编排测试-甲", "编排测试-乙", "编排测试-丙", "编排测试-丁", "编排测试-戊"]


@pytest.fixture(autouse=True)
def _clear_recorder() -> None:
    RECORDER.order.clear()


@pytest.fixture
def extra_session() -> RunSession:
    return make_session(pinger=FakePinger(count=1), http=FakeHttp())


class TestSelectCheckers:
    def test_only_filter(self) -> None:
        assert [spec.name for spec in select_checkers(only=["编排测试-甲"])] == ["编排测试-甲"]

    def test_skip_filter(self) -> None:
        selected = select_checkers(only=NAMES[:2], skip=["编排测试-乙"])
        assert [spec.name for spec in selected] == ["编排测试-甲"]

    def test_no_filter_returns_all_in_order(self) -> None:
        specs = select_checkers()
        assert [spec.order for spec in specs] == sorted(spec.order for spec in specs)

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(KeyError):
            select_checkers(only=["完全不存在的名字"])


class TestRunChecks:
    async def test_executes_in_order(self) -> None:
        result = await run_checks(only=NAMES[:3])
        assert RECORDER.order == NAMES[:3]
        assert [item.name for item in result.outcomes] == NAMES[:3]

    async def test_status_mapping(self) -> None:
        result = await run_checks(only=NAMES[:3])
        statuses = {item.name: item.status for item in result.outcomes}
        assert statuses["编排测试-甲"] is CheckStatus.PASSED
        assert statuses["编排测试-乙"] is CheckStatus.WARNED
        assert statuses["编排测试-丙"] is CheckStatus.FAILED

    async def test_exception_is_reported_not_raised(self) -> None:
        result = await run_checks(only=["编排测试-丙"])
        outcome = result.outcomes[0]
        assert outcome.status is CheckStatus.FAILED
        assert "检查执行失败" in outcome.summary
        assert "丙炸了" in outcome.details

    async def test_false_condition_skips(self) -> None:
        result = await run_checks(only=["编排测试-丁"])
        outcome = result.outcomes[0]
        assert outcome.status is CheckStatus.SKIPPED
        assert outcome.summary == "检查被跳过"
        assert outcome.skipped_reason == "环境就绪"
        assert "未满足" in outcome.details

    async def test_broken_condition_skips_instead_of_crashing(self) -> None:
        result = await run_checks(only=["编排测试-戊"])
        outcome = result.outcomes[0]
        assert outcome.status is CheckStatus.SKIPPED
        assert outcome.skipped_reason == "条件自身出错"

    async def test_details_and_lists_are_carried_over(self) -> None:
        result = await run_checks(only=["编排测试-甲", "编排测试-乙"])
        alpha = result.by_name("编排测试-甲")
        beta = result.by_name("编排测试-乙")
        assert alpha is not None
        assert beta is not None
        assert alpha.details == "alpha 日志"
        assert beta.warnings == ["乙有点问题"]

    async def test_progress_callback_fires_in_order(self) -> None:
        events: list[ProgressEvent] = []
        await run_checks(only=NAMES[:3], on_progress=events.append)
        assert [event.index for event in events] == [0, 1, 2]
        assert all(event.total == 3 for event in events)
        assert [event.spec.name for event in events] == NAMES[:3]

    async def test_should_stop_halts_scheduling(self) -> None:
        seen: list[str] = []

        def should_stop() -> bool:
            return len(seen) >= 1

        def on_progress(event: ProgressEvent) -> None:
            seen.append(event.spec.name)

        result = await run_checks(only=NAMES[:4], on_progress=on_progress, should_stop=should_stop)
        assert [item.name for item in result.outcomes] == NAMES[:1]

    async def test_should_stop_before_first_check(self) -> None:
        result = await run_checks(only=NAMES[:3], should_stop=lambda: True)
        assert result.outcomes == []

    async def test_by_name_returns_none_for_missing(self) -> None:
        result = await run_checks(only=["编排测试-甲"])
        assert result.by_name("不存在") is None

    async def test_result_records_session_and_timing(self) -> None:
        result = await run_checks(only=["编排测试-甲"])
        assert isinstance(result, RunResult)
        assert result.session is not None
        assert result.duration_s >= 0
        assert result.started_at is not None

    async def test_context_is_reset_between_runs(self) -> None:
        session = make_session(pinger=FakePinger(count=1), http=FakeHttp())
        session.context.dns_available = True
        session.context.has_tunnel_interface = True
        await run_checks(only=["编排测试-甲"], session=session)
        assert session.context.dns_available is False
        assert session.context.has_tunnel_interface is False

    async def test_explicit_session_is_not_closed(self) -> None:
        http = FakeHttp()
        session = make_session(pinger=FakePinger(count=1), http=http)
        await run_checks(only=["编排测试-甲"], session=session)
        assert http.closed is False

    async def test_config_override_is_applied(self) -> None:
        session = make_session(pinger=FakePinger(count=1), http=FakeHttp())
        config = RunConfig(ping_count=7)
        await run_checks(only=["编排测试-甲"], session=session, config=config)
        assert session.config.ping_count == 7

    async def test_verbose_flag_propagates(self) -> None:
        session = make_session(pinger=FakePinger(count=1), http=FakeHttp())
        await run_checks(only=["编排测试-甲"], session=session, verbose=True)
        assert session.verbose is True
