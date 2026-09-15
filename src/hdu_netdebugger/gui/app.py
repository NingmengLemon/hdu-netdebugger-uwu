"""Qt 应用装配与启动."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from hdu_netdebugger.gui.style import apply_theme

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["run_app"]


def run_app(
    *,
    verbose: bool = False,
    argv: Sequence[str] | None = None,
) -> int:
    """创建 ``QApplication``、显示主窗口并进入事件循环。

    Args:
        verbose: 是否把检查过程同步打印到终端。
        argv: 传给 ``QApplication`` 的参数（默认取 ``sys.argv``）。

    Returns:
        进程退出码。

    Raises:
        ImportError: 未安装 PySide6。
    """
    try:
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415 - 可选依赖，延迟导入
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise ImportError("未安装 PySide6。请执行： uv sync --extra gui") from exc

    from hdu_netdebugger.checkers import load_builtin_checkers  # noqa: PLC0415

    load_builtin_checkers()

    existing = QApplication.instance()
    app = (
        existing
        if isinstance(existing, QApplication)
        else QApplication(list(argv) if argv else sys.argv)
    )
    app.setApplicationName("HDU Net Debugger")
    app.setApplicationVersion(_version())
    apply_theme(app)

    from hdu_netdebugger.gui.main_window import MainWindow  # noqa: PLC0415 - 需在 QApplication 之后

    window = MainWindow(verbose=verbose)
    window.show()
    return app.exec()


def _version() -> str:
    from hdu_netdebugger import __version__  # noqa: PLC0415

    return __version__
