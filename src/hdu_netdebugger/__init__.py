"""HDU NetDebugger (Uwu) — 杭州电子科技大学校园网诊断工具.

[HDU-ICU/HDU_NetDebugger](https://github.com/HDU-ICU/HDU_NetDebugger) 的 Python 复刻版。

对外主要入口：

* :mod:`hdu_netdebugger.cli` —— Typer 命令行界面（主要使用方式）
* :mod:`hdu_netdebugger.gui` —— PySide6 可选图形界面
* :func:`hdu_netdebugger.runner.run_checks` —— 供第三方调用的编排 API
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("hdu-netdebugger-uwu")
except PackageNotFoundError:  # pragma: no cover - 仅在未安装（直接跑源码）时触发
    __version__ = "0.0.0.dev0"
