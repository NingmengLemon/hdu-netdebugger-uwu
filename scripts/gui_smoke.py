"""GUI 冒烟测试脚本（离屏运行，用于 CI / 截图）。

用法::

    uv run python scripts/gui_smoke.py [截图输出路径]

在 ``QT_QPA_PLATFORM=offscreen`` 下构造主窗口、跑完一轮检查并把窗口截图存盘，
以此验证 Qt 组件能正常装配、后台线程能跑通、结果能回填到界面。

退出码：``0`` 冒烟通过，``1`` 冒烟失败 —— "运行期出错 / 有检查项没拿到结果 /
截图写不出来 / 超时" 都算失败。这是 CI 门禁，不能在坏版本上返回 0。

注意这里只判定**界面链路**是否正常，不判定网络结论：某台机器上"公网不可达"
是合法结果，不该让冒烟失败。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from hdu_netdebugger.checkers import load_builtin_checkers
from hdu_netdebugger.gui.main_window import MainWindow
from hdu_netdebugger.gui.style import apply_theme

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hdu_netdebugger.gui.widgets.check_card import CheckCard

TIMEOUT_MS = 120_000


def judge(
    cards: Sequence[CheckCard],
    *,
    saved: bool,
    runtime_errors: Sequence[str],
) -> list[str]:
    """判定这次冒烟是否失败，返回失败原因列表（空列表表示通过）。

    单独抽出来是为了能被单测覆盖：这个函数一旦失效，CI 门禁就会在坏版本上返回 0。
    """
    problems: list[str] = []
    if not cards:
        problems.append("没有注册任何检查项")
    if not saved:
        problems.append("截图写入失败")
    if runtime_errors:
        problems.append(f"运行期错误: {runtime_errors[0]}")
    missing = [card.spec_name for card in cards if card.outcome is None]
    if missing:
        problems.append("以下检查项没有结果: " + "、".join(missing))
    return problems


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("gui_smoke.png")
    load_builtin_checkers()

    app = QApplication.instance() or QApplication(["gui_smoke"])
    apply_theme(app)

    # 运行级失败时主窗口会弹**模态**错误框：模态会开一个嵌套事件循环，
    # 而下面的轮询定时器照样会触发 —— 于是截图里带着一个错误对话框、
    # 脚本却不知道出过事。这里把它拦下来记录，既保证截图干净，也让失败可见。
    runtime_errors: list[str] = []

    def record_critical(
        _parent: object,
        _title: str,
        text: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        runtime_errors.append(str(text))
        return QMessageBox.StandardButton.Ok

    QMessageBox.critical = staticmethod(record_critical)  # ty: ignore[invalid-assignment]

    window = MainWindow()
    window.resize(980, 900)
    window.show()
    window.start_checks()

    state = {"done": False, "code": 1}

    def poll() -> None:
        if window.is_running:
            return
        state["done"] = True

        saved = window.grab().save(str(output))
        summary = window._summary.text()  # noqa: SLF001 - 冒烟脚本读取界面状态
        print(f"screenshot -> {output} ({'ok' if saved else 'FAILED'})")
        print(f"summary    -> {summary}")

        for card in window._cards:  # noqa: SLF001 - 冒烟脚本读取界面状态
            outcome = card.outcome
            if outcome is None:
                print(f"  [??] {card.spec_name}: 无结果")
                continue
            print(f"  [{outcome.status.value:>8}] {card.spec_name}: {outcome.summary}")

        problems = judge(
            window._cards,  # noqa: SLF001
            saved=saved,
            runtime_errors=runtime_errors,
        )

        state["code"] = 1 if problems else 0
        if problems:
            print("SMOKE FAILED: " + "; ".join(problems), file=sys.stderr)
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

    code = app.exec()
    return state["code"] if state["done"] else code


if __name__ == "__main__":
    raise SystemExit(main())
