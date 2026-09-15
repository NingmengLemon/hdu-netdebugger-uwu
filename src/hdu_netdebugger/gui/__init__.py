"""PySide6 可选图形界面.

本包是可选的：只有安装 ``gui`` extra（``uv sync --extra gui``）后才可用。
:func:`launch` 是所有 Qt 依赖的唯一入口，因此没装 PySide6 时导入本包也不会报错。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["launch"]

if TYPE_CHECKING:
    from collections.abc import Sequence

_GUI_HINT = "GUI 依赖未安装。请执行： uv sync --extra gui"


def launch(
    *,
    verbose: bool = False,
    argv: Sequence[str] | None = None,
) -> int:
    """启动图形界面并阻塞到窗口关闭。

    Args:
        verbose: 是否在终端同步打印检查过程。
        argv: 传给 ``QApplication`` 的参数（测试 / 嵌入时使用）。

    Returns:
        进程退出码。

    Raises:
        ImportError: 未安装 PySide6。
    """
    try:
        from hdu_netdebugger.gui.app import run_app  # noqa: PLC0415 - 可选依赖，延迟导入
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise ImportError(_GUI_HINT) from exc

    return run_app(verbose=verbose, argv=argv)
