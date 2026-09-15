"""检查项注册表.

C# 原版通过反射扫描 ``AppDomain`` 里带 ``[Checker]`` 特性的类来发现检查项。
Python 的做法更直接：用装饰器在**类定义时**登记，注册表就是唯一事实来源，
不需要 import 反射，也能被类型检查器看见。

用法::

    @checker("检查公网可达性", order=35)
    class PublicNetCheck(Checker):
        @condition("网络已连通")
        def _has_network(self) -> bool:
            return self.context.network_kind is not None

        async def check(self) -> None: ...
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from hdu_netdebugger.checkers.base import Checker
    from hdu_netdebugger.config import RunConfig
    from hdu_netdebugger.context import CheckContext

__all__ = [
    "CheckerSpec",
    "ConditionSpec",
    "all_checkers",
    "checker",
    "condition",
    "get_checker",
    "registered_names",
    "resolve_names",
    "unregister_all",
]

CheckerT = TypeVar("CheckerT", bound="Checker")

#: 条件函数被标记时写入的属性名
_CONDITION_ATTR = "__hnd_condition_name__"


@dataclass(frozen=True, slots=True)
class ConditionSpec:
    """检查项的前置条件。"""

    name: str
    #: 未绑定的方法对象，调用时传入检查项实例
    func: Callable[..., object]

    async def evaluate(self, instance: Checker) -> bool:
        """求值；同步方法会被直接调用，异步方法会被 await。"""
        outcome = self.func(instance)
        if inspect.isawaitable(outcome):
            return bool(await outcome)
        return bool(outcome)


@dataclass(frozen=True, slots=True)
class CheckerSpec:
    """一个已注册的检查项。"""

    name: str
    order: int
    cls: type[Checker]
    conditions: tuple[ConditionSpec, ...] = ()

    def create(self, context: CheckContext, config: RunConfig) -> Checker:
        """实例化检查项。"""
        return self.cls(context, config)


_REGISTRY: dict[str, CheckerSpec] = {}


def condition(name: str) -> Callable[[object], object]:
    """把方法标记为检查项的前置条件。

    Args:
        name: 条件的可读名称，会出现在"跳过原因"里。
    """

    def decorator(func: object) -> object:
        setattr(func, _CONDITION_ATTR, name)
        return func

    return decorator


def checker(
    name: str,
    *,
    order: int = 0,
    override: bool = False,
) -> Callable[[type[CheckerT]], type[CheckerT]]:
    """把一个 :class:`~hdu_netdebugger.checkers.base.Checker` 子类登记进注册表。

    Args:
        name: 检查项显示名，也是 ``--only`` / ``--skip`` 的匹配目标。
        order: 执行顺序，越小越先执行。
        override: 允许覆盖同名检查项（默认重名直接报错，避免静默失效）。

    Raises:
        TypeError: 被装饰的对象不是 :class:`Checker` 子类。
        ValueError: 名称重复且未指定 ``override=True``。
    """

    def decorator(cls: type[CheckerT]) -> type[CheckerT]:
        from hdu_netdebugger.checkers.base import Checker  # noqa: PLC0415 - 避免循环导入

        if not (isinstance(cls, type) and issubclass(cls, Checker)):
            raise TypeError(f"{cls!r} 不是 Checker 的子类")
        if not name.strip():
            raise ValueError("检查项名称不能为空")

        existing = _REGISTRY.get(name)
        if existing is not None and not override:
            raise ValueError(
                f"检查项名称重复: {name!r} 已被 {existing.cls.__qualname__} 注册；"
                "如需替换请显式传入 override=True"
            )

        conditions = _collect_conditions(cls)
        cls.name = name
        cls.order = order
        _REGISTRY[name] = CheckerSpec(name=name, order=order, cls=cls, conditions=conditions)
        return cls

    return decorator


def _collect_conditions(cls: type[Checker]) -> tuple[ConditionSpec, ...]:
    """按定义顺序收集被 :func:`condition` 标记的方法。"""
    specs: list[ConditionSpec] = []
    for member in vars(cls).values():
        label = getattr(member, _CONDITION_ATTR, None)
        if label is not None:
            specs.append(ConditionSpec(name=str(label), func=member))
    return tuple(specs)


def all_checkers() -> list[CheckerSpec]:
    """返回全部已注册检查项，按 ``(order, name)`` 稳定排序。"""
    return sorted(_REGISTRY.values(), key=lambda spec: (spec.order, spec.name))


def registered_names() -> list[str]:
    """返回全部已注册检查项的名称（按执行顺序）。"""
    return [spec.name for spec in all_checkers()]


def get_checker(name: str) -> CheckerSpec:
    """按精确名称取检查项。

    Raises:
        KeyError: 名称不存在。
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"未注册的检查项: {name!r}") from None


def resolve_names(patterns: list[str]) -> list[CheckerSpec]:
    """把用户给的名字/子串解析为检查项列表。

    匹配优先级（逐个 pattern 解析，结果按执行顺序去重合并）：

    1. 精确名称
    2. 大小写不敏感的名称
    3. 名称子串（大小写不敏感）

    Args:
        patterns: 用户输入的名称、名称片段或序号。

    Raises:
        KeyError: 某个 pattern 匹配不到任何检查项。
    """
    specs = all_checkers()
    resolved: dict[str, CheckerSpec] = {}

    for pattern in patterns:
        needle = pattern.strip()
        if not needle:
            continue

        matches = [spec for spec in specs if spec.name == needle]
        if not matches:
            lowered = needle.casefold()
            matches = [spec for spec in specs if spec.name.casefold() == lowered]
        if not matches:
            lowered = needle.casefold()
            matches = [spec for spec in specs if lowered in spec.name.casefold()]
        if not matches and needle.lstrip("-").isdigit():
            wanted = int(needle)
            matches = [spec for spec in specs if spec.order == wanted]
        if not matches:
            known = "、".join(registered_names())
            raise KeyError(f"没有匹配 {pattern!r} 的检查项。已注册: {known}")

        for spec in matches:
            resolved[spec.name] = spec

    return sorted(resolved.values(), key=lambda spec: (spec.order, spec.name))


def unregister_all() -> None:
    """清空注册表（仅供测试使用）。"""
    _REGISTRY.clear()
