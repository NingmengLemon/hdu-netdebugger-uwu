"""冻结入口：窗口版 GUI（不带控制台窗口）.

``launch()`` 内部才导入 PySide6，所以 CLI 那个产物可以在 Analysis 里把
PySide6 / shiboken6 排除掉，体积能小一大截。
"""

from __future__ import annotations

from hdu_netdebugger.gui import launch

if __name__ == "__main__":
    raise SystemExit(launch())
