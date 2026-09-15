"""GUI 应用入口（占位实现，将在 GUI 阶段替换为完整窗口）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["run_app"]


def run_app(
    *,
    verbose: bool = False,
    argv: Sequence[str] | None = None,
) -> int:
    """启动 Qt 应用。

    Raises:
        ImportError: 未安装 PySide6。
    """
    del verbose, argv  # 占位实现尚未使用这些参数
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401, PLC0415
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise ImportError("未安装 PySide6。请执行： uv sync --extra gui") from exc

    raise NotImplementedError("GUI 尚未实现")
