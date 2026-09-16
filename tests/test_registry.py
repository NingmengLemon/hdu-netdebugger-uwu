"""检查项注册表与条件求值。"""

from __future__ import annotations

from typing import ClassVar

import pytest

from hdu_netdebugger.checkers.base import Checker, DetailsLog
from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.context import CheckContext
from hdu_netdebugger.registry import (
    CheckerSpec,
    all_checkers,
    checker,
    condition,
    get_checker,
    registered_names,
    resolve_names,
)
from hdu_netdebugger.session import RunSession


class _AsyncCondition(Checker):
    async def check(self) -> None:  # pragma: no cover - 测试不执行
        self.passed("ok")

    @condition("异步条件")
    async def _async_ok(self) -> bool:
        return True


class _MixedConditions(Checker):
    async def check(self) -> None:  # pragma: no cover - 测试不执行
        self.passed("ok")

    @condition("总是真")
    def _always(self) -> bool:
        return True

    @condition("总是假")
    def _never(self) -> bool:
        return False

    @condition("会炸")
    def _boom(self) -> bool:
        raise RuntimeError("条件自身出错")


class TestBuiltinRegistry:
    """只断言内置检查项，不假设注册表里没有别的测试用检查项。"""

    BUILTIN_NAMES: ClassVar[list[str]] = [
        "获取基本信息",
        "检查代理状态",
        "检查BAS可达性",
        "检查生活区深澜认证可达性",
        "检查DNS可用性",
        "检查认证状态",
        "检查运营商网关可达性",
        "检查公网可达性",
        "检查路径MTU",
        "检查IPv6",
        "检查逐跳路径",
    ]
    #: 前 8 项的 order 与原版一一对应（`-1/5/.../35` 是原版的执行顺序，不能改）；
    #: 本复刻新增的能力排在后面，避免插进原版序列里改变既有执行次序。
    BUILTIN_ORDERS: ClassVar[list[int]] = [-1, 5, 10, 15, 20, 25, 30, 35, 37, 38, 39]
    #: 原版 8 项的顺序（用于单独钉住"没有改动原版次序"）
    ORIGINAL_ORDERS: ClassVar[list[int]] = [-1, 5, 10, 15, 20, 25, 30, 35]

    def builtin(self) -> list[CheckerSpec]:
        return [
            spec
            for spec in all_checkers()
            if spec.cls.__module__.startswith("hdu_netdebugger.checkers.")
        ]

    def test_all_builtin_checkers_registered(self) -> None:
        assert [spec.name for spec in self.builtin()] == self.BUILTIN_NAMES

    def test_orders_match_original(self) -> None:
        assert [spec.order for spec in self.builtin()] == self.BUILTIN_ORDERS

    def test_original_orders_are_untouched(self) -> None:
        """新增检查项只能排在原版 8 项之后，不能改变原版的执行次序。"""
        assert [spec.order for spec in self.builtin()][: len(self.ORIGINAL_ORDERS)] == (
            self.ORIGINAL_ORDERS
        )

    def test_only_isp_check_has_condition(self) -> None:
        conditioned = {spec.name: [c.name for c in spec.conditions] for spec in self.builtin()}
        assert conditioned["检查运营商网关可达性"] == ["运营商ID非空且有效"]
        assert all(
            not names for name, names in conditioned.items() if name != "检查运营商网关可达性"
        )

    def test_specs_are_instantiable(self) -> None:
        session = RunSession(config=RunConfig(), context=CheckContext())
        for spec in self.builtin():
            instance = spec.create(session)
            assert isinstance(instance, Checker)
            assert instance.name == spec.name

    def test_registered_names_are_sorted_by_order(self) -> None:
        names = registered_names()
        assert names == [spec.name for spec in all_checkers()]
        for name in self.BUILTIN_NAMES:
            assert name in names


class TestCheckerDecorator:
    def test_registers_and_injects_metadata(self) -> None:
        @checker("测试用检查项-注册", order=999)
        class _Sample(Checker):
            async def check(self) -> None:  # pragma: no cover - 测试不执行
                self.passed("ok")

        assert _Sample.name == "测试用检查项-注册"
        assert _Sample.order == 999
        assert get_checker("测试用检查项-注册").cls is _Sample

    def test_duplicate_name_rejected(self) -> None:
        @checker("测试用检查项-重复")
        class _First(Checker):
            async def check(self) -> None:  # pragma: no cover
                self.passed("ok")

        with pytest.raises(ValueError, match="检查项名称重复"):

            @checker("测试用检查项-重复")
            class _Second(Checker):
                async def check(self) -> None:  # pragma: no cover
                    self.passed("ok")

    def test_duplicate_name_allowed_with_override(self) -> None:
        @checker("测试用检查项-覆盖")
        class _Original(Checker):
            async def check(self) -> None:  # pragma: no cover
                self.passed("ok")

        @checker("测试用检查项-覆盖", override=True)
        class _Replacement(Checker):
            async def check(self) -> None:  # pragma: no cover
                self.passed("ok")

        assert get_checker("测试用检查项-覆盖").cls is _Replacement
        assert _Original.name == "测试用检查项-覆盖"

    def test_non_checker_class_rejected(self) -> None:
        with pytest.raises(TypeError, match="不是 Checker 的子类"):

            @checker("测试用检查项-非法")  # ty: ignore[invalid-argument-type]
            class _NotAChecker:
                pass

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="名称不能为空"):

            @checker("   ")
            class _Blank(Checker):
                async def check(self) -> None:  # pragma: no cover
                    self.passed("ok")


class TestConditions:
    def test_conditions_collected_in_definition_order(self) -> None:
        @checker("测试用检查项-条件顺序")
        class _WithConditions(_MixedConditions):
            pass

        names = [item.name for item in get_checker("测试用检查项-条件顺序").conditions]
        assert names == ["总是真", "总是假", "会炸"]

    async def test_sync_condition_is_evaluated(self) -> None:
        @checker("测试用检查项-同步条件")
        class _Sync(_MixedConditions):
            pass

        spec = get_checker("测试用检查项-同步条件")
        instance = spec.create(RunSession(config=RunConfig(), context=CheckContext()))
        assert await spec.conditions[0].evaluate(instance) is True
        assert await spec.conditions[1].evaluate(instance) is False

    async def test_async_condition_is_awaited(self) -> None:
        @checker("测试用检查项-异步条件")
        class _Async(_AsyncCondition):
            pass

        spec = get_checker("测试用检查项-异步条件")
        instance = spec.create(RunSession(config=RunConfig(), context=CheckContext()))
        assert await spec.conditions[0].evaluate(instance) is True

    async def test_condition_exception_propagates_to_runner(self) -> None:
        """条件自身抛异常时，evaluate 不吞异常，交给 runner 判为"不满足"。"""
        spec = get_checker("测试用检查项-条件顺序")
        instance = spec.create(RunSession(config=RunConfig(), context=CheckContext()))
        with pytest.raises(RuntimeError, match="条件自身出错"):
            await spec.conditions[2].evaluate(instance)

    async def test_subclass_can_override_inherited_condition(self) -> None:
        """派生类用同名条件覆盖父类 —— 对应原版的"平台特化版本优先"。"""

        @checker("测试用检查项-条件覆盖")
        class _Override(_MixedConditions):
            @condition("总是假")
            def _never(self) -> bool:
                return True

        spec = get_checker("测试用检查项-条件覆盖")
        instance = spec.create(RunSession(config=RunConfig(), context=CheckContext()))
        assert [item.name for item in spec.conditions] == ["总是真", "总是假", "会炸"]
        assert await spec.conditions[1].evaluate(instance) is True


class TestResolveNames:
    def test_exact_match(self) -> None:
        assert [spec.name for spec in resolve_names(["检查DNS可用性"])] == ["检查DNS可用性"]

    def test_substring_match_returns_multiple(self) -> None:
        resolved = [spec.name for spec in resolve_names(["认证"])]
        assert resolved == ["检查生活区深澜认证可达性", "检查认证状态"]

    def test_case_insensitive_match(self) -> None:
        assert [spec.name for spec in resolve_names(["bas"])] == ["检查BAS可达性"]

    def test_numeric_order_match(self) -> None:
        assert [spec.name for spec in resolve_names(["35"])] == ["检查公网可达性"]
        assert [spec.name for spec in resolve_names(["-1"])] == ["获取基本信息"]

    def test_results_are_sorted_by_order(self) -> None:
        resolved = resolve_names(["检查认证状态", "获取基本信息", "检查BAS可达性"])
        assert [spec.order for spec in resolved] == [-1, 10, 25]

    def test_duplicate_patterns_are_merged(self) -> None:
        assert len(resolve_names(["DNS", "检查DNS可用性", "dns"])) == 1

    def test_unknown_pattern_raises(self) -> None:
        with pytest.raises(KeyError, match="没有匹配"):
            resolve_names(["根本不存在的检查项"])

    def test_blank_patterns_are_skipped(self) -> None:
        assert resolve_names(["", "   "]) == []


class TestCheckerBase:
    async def test_exception_becomes_failed_result(self) -> None:
        class _Boom(Checker):
            async def check(self) -> None:
                raise RuntimeError("模拟崩溃")

        session = RunSession(config=RunConfig(), context=CheckContext())
        result = await _Boom(session).execute()
        assert result.status.name == "FAIL"
        assert "检查执行失败" in result.summary
        assert "模拟崩溃" in (result.details or "")

    async def test_reset_clears_previous_run(self) -> None:
        class _Once(Checker):
            async def check(self) -> None:
                self.log("一行日志")
                self.add_warning("一条警告")
                self.failed("失败")

        session = RunSession(config=RunConfig(), context=CheckContext())
        instance = _Once(session)
        first = await instance.execute()
        assert first.status.name == "FAIL"
        second = await instance.execute()
        assert second.warnings == ["一条警告"]
        assert second.details == "一行日志"

    async def test_details_trim_trailing_blank_lines(self) -> None:
        class _Logging(Checker):
            async def check(self) -> None:
                self.log("第一行")
                self.log()
                self.log("")
                self.passed("ok")

        session = RunSession(config=RunConfig(), context=CheckContext())
        result = await _Logging(session).execute()
        assert result.details == "第一行"

    def test_details_log_helpers(self) -> None:
        log = DetailsLog()
        assert not log
        log.line("a")
        log.extend(["b", "c"])
        assert len(log) == 3
        assert log.text == "a\nb\nc"
        log.clear()
        assert log.text == ""
