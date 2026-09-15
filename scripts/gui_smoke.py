"""GUI 冒烟测试脚本（离屏运行，用于 CI / 截图）。

用法::

    uv run python scripts/gui_smoke.py [截图输出路径]

在 ``QT_QPA_PLATFORM=offscreen`` 下构造主窗口、跑完一轮检查并把窗口截图存盘，
以此验证 Qt 组件能正常装配、后台线程能跑通、结果能回填到界面。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hdu_netdebugger.checkers import load_builtin_checkers
from hdu_netdebugger.gui.main_window import MainWindow
from hdu_netdebugger.gui.style import apply_theme

TIMEOUT_MS = 120_000


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("gui_smoke.png")
    load_builtin_checkers()

    app = QApplication.instance() or QApplication(["gui_smoke"])
    apply_theme(app)

    window = MainWindow()
    window.resize(980, 900)
    window.show()
    window.start_checks()

    state = {"done": False}

    def poll() -> None:
        if window.is_running:
            return
        state["done"] = True
        window.grab().save(str(output))
        summary = window._summary.text()  # noqa: SLF001 - 冒烟脚本读取界面状态
        print(f"screenshot -> {output}")
        print(f"summary    -> {summary}")
        for card in window._cards:  # noqa: SLF001
            outcome = card.outcome
            if outcome is None:
                print(f"  [??] {card.spec_name}: 无结果")
                continue
            print(f"  [{outcome.status.value:>8}] {card.spec_name}: {outcome.summary}")
        app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(200)

    guard = QTimer()
    guard.setSingleShot(True)

    def bail() -> None:
        if not state["done"]:
            print("TIMEOUT: 检查未在预期时间内完成", file=sys.stderr)
            app.exit(1)

    guard.timeout.connect(bail)
    guard.start(TIMEOUT_MS)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
