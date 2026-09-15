"""图形界面（离屏运行）。

Qt 的 ``offscreen`` 平台插件让窗口能在没有显示器的环境里构造与渲染，
因此这些用例在 CI 里也能跑。没有装 PySide6 时整个模块会被跳过。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="需要 gui extra： uv sync --extra gui")

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMessageBox,
)

from hdu_netdebugger.checkers import load_builtin_checkers
from hdu_netdebugger.gui import style
from hdu_netdebugger.gui.main_window import MainWindow
from hdu_netdebugger.models.enums import CheckStatus
from hdu_netdebugger.models.result import CheckOutcome
from hdu_netdebugger.registry import all_checkers
from hdu_netdebugger.runner import ProgressEvent, RunResult

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from hdu_netdebugger.gui.widgets.check_card import CheckCard

CHECKED_AT = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)


def noop_dialog(*_args: object, **_kwargs: object) -> None:
    """替代 QMessageBox 的弹窗，避免测试阻塞。"""
    return


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """整个模块共用一个 QApplication（Qt 只允许存在一个）。"""
    load_builtin_checkers()
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(["pytest-qt"])
    style.apply_theme(app)
    return app


@pytest.fixture
def window(qapp: QApplication) -> Iterator[MainWindow]:
    win = MainWindow()
    yield win
    win.close()
    win.deleteLater()


def make_outcome(
    name: str,
    status: CheckStatus,
    *,
    warnings: list[str] | None = None,
    suggestions: list[str] | None = None,
) -> CheckOutcome:
    return CheckOutcome(
        name=name,
        order=20,
        status=status,
        summary=f"{name} 的结论",
        details="第一行\n第二行",
        warnings=warnings or [],
        suggestions=suggestions or [],
    )


def first_card(window: MainWindow) -> CheckCard:
    """取第一张卡片（测试需要访问内部控件）。"""
    return window._cards[0]


class TestTheme:
    def test_qss_avoids_global_widget_background(self) -> None:
        """``QWidget { background-color }`` 会把所有 QLabel 变成不透明色块。"""
        assert "QWidget {" in style.APP_QSS
        widget_block = style.APP_QSS.split("QWidget {", 1)[1].split("}", 1)[0]
        assert "background-color" not in widget_block

    def test_labels_are_transparent(self) -> None:
        assert "QLabel {" in style.APP_QSS
        label_block = style.APP_QSS.split("QLabel {", 1)[1].split("}", 1)[0]
        assert "background: transparent" in label_block

    def test_every_font_rule_has_explicit_color(self) -> None:
        """只写 font-size 会让前景色回落到未定义值（深色模式下白字白底）。"""
        offenders = [
            block.split("{", 1)[0].strip()
            for block in style.APP_QSS.split("}")[:-1]
            if "font-size" in block and "color:" not in block
        ]
        assert offenders == []

    def test_palette_is_light(self, qapp: QApplication) -> None:
        assert qapp.palette().color(QPalette.ColorRole.Window) == QColor(style.WINDOW_BG)
        assert qapp.palette().color(QPalette.ColorRole.WindowText) == QColor(style.TEXT)

    @pytest.mark.parametrize("status", list(CheckStatus))
    def test_status_helpers(self, status: CheckStatus) -> None:
        assert style.status_icon(status)
        assert style.status_color(status).startswith("#")

    def test_repolish_does_not_raise(self, qapp: QApplication) -> None:
        style.repolish(QLabel("x"))


class TestCheckCard:
    def test_card_per_checker(self, window: MainWindow) -> None:
        assert len(window._cards) == len(all_checkers())

    def test_initial_state(self, window: MainWindow) -> None:
        card = first_card(window)
        assert card.outcome is None
        assert not card.is_expanded
        assert card.spec_name

    def test_apply_pass_keeps_collapsed(self, window: MainWindow) -> None:
        card = first_card(window)
        card.apply(make_outcome(card.spec_name, CheckStatus.PASSED))
        assert card.outcome is not None
        assert not card.is_expanded

    @pytest.mark.parametrize("status", [CheckStatus.WARNED, CheckStatus.FAILED])
    def test_problem_statuses_auto_expand(
        self,
        window: MainWindow,
        status: CheckStatus,
    ) -> None:
        card = first_card(window)
        card.apply(make_outcome(card.spec_name, status, warnings=["出问题了"]))
        assert card.is_expanded

    def test_toggle(self, window: MainWindow) -> None:
        card = first_card(window)
        assert not card.is_expanded
        card.toggle()
        assert card.is_expanded
        card.toggle()
        assert not card.is_expanded

    def test_reset_restores_initial_state(self, window: MainWindow) -> None:
        card = first_card(window)
        card.apply(make_outcome(card.spec_name, CheckStatus.FAILED, warnings=["w"]))
        card.reset()
        assert card.outcome is None
        assert not card.is_expanded

    def test_mark_running(self, window: MainWindow) -> None:
        card = first_card(window)
        card.mark_running()
        assert card.outcome is None
        assert "检查" in card._summary.text()

    def test_suggestions_and_warnings_visibility(self, window: MainWindow) -> None:
        card = first_card(window)
        card.apply(make_outcome(card.spec_name, CheckStatus.WARNED))
        assert not card._suggestion_box.isVisibleTo(card)
        assert not card._warning_box.isVisibleTo(card)

        card.apply(
            make_outcome(
                card.spec_name,
                CheckStatus.WARNED,
                warnings=["w"],
                suggestions=["s"],
            )
        )
        assert card._suggestion_body.text() == "• s"
        assert card._warning_body.text() == "• w"

    def test_header_text_is_populated(self, window: MainWindow) -> None:
        """回归：标题行曾经因为布局遮挡渲染成空白。"""
        card = first_card(window)
        card.apply(make_outcome(card.spec_name, CheckStatus.PASSED))
        assert card._name.text() == card.spec_name
        assert card._summary.text() == f"{card.spec_name} 的结论"
        assert card._icon.text()

    def test_details_fall_back_to_placeholder(self, window: MainWindow) -> None:
        card = first_card(window)
        outcome = make_outcome(card.spec_name, CheckStatus.PASSED)
        outcome.details = ""
        card.apply(outcome)
        assert card._details.toPlainText() == "（无详细日志）"


class TestMainWindow:
    def test_window_metadata(self, window: MainWindow) -> None:
        assert "HDU Net Debugger" in window.windowTitle()
        assert not window.is_running

    def test_progress_updates_matching_card(self, window: MainWindow) -> None:
        spec = window._cards[2]._spec
        target = spec.name
        window._on_progress(
            ProgressEvent(
                spec=spec,
                index=2,
                total=8,
                outcome=make_outcome(target, CheckStatus.PASSED),
            )
        )
        assert window._cards[2].outcome is not None

    def test_unknown_progress_is_ignored(self, window: MainWindow) -> None:
        """结果里的名字不在卡片列表里时不应抛异常。"""
        spec = window._cards[0]._spec
        window._on_progress(
            ProgressEvent(
                spec=spec,
                index=0,
                total=1,
                outcome=make_outcome("不存在的检查项", CheckStatus.PASSED),
            )
        )

    def test_finished_enables_export(self, window: MainWindow) -> None:
        outcomes = [make_outcome(card.spec_name, CheckStatus.PASSED) for card in window._cards]
        window._on_finished(RunResult(outcomes=outcomes, duration_s=1.0))
        assert window._export_button.isEnabled()
        assert f"共 {len(outcomes)} 项" in window._summary.text()

    def test_export_without_result_shows_message(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            QMessageBox,
            "information",
            staticmethod(lambda *args, **kwargs: calls.append(str(args[2]))),
        )
        window.export_results()
        assert len(calls) == 1
        assert "请先执行一次检查" in calls[0]

    def test_export_writes_markdown(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        outcomes = [make_outcome(card.spec_name, CheckStatus.PASSED) for card in window._cards]
        window._on_finished(RunResult(outcomes=outcomes, started_at=CHECKED_AT))

        target = tmp_path / "out.md"
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            staticmethod(lambda *args, **kwargs: (str(target), "Markdown 报告 (*.md)")),
        )
        monkeypatch.setattr(QMessageBox, "information", staticmethod(noop_dialog))
        window.export_results()
        assert target.exists()
        assert "HDU Net Debugger 检查结果报告" in target.read_text(encoding="utf-8")

    def test_export_writes_json(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        outcomes = [make_outcome(card.spec_name, CheckStatus.FAILED) for card in window._cards]
        window._on_finished(RunResult(outcomes=outcomes, started_at=CHECKED_AT))

        target = tmp_path / "out.json"
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            staticmethod(lambda *args, **kwargs: (str(target), "JSON 数据 (*.json)")),
        )
        monkeypatch.setattr(QMessageBox, "information", staticmethod(noop_dialog))
        window.export_results()
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["summary"]["failed"] == len(outcomes)

    def test_export_cancelled_does_nothing(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window._on_finished(RunResult(outcomes=[make_outcome("x", CheckStatus.PASSED)]))
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            staticmethod(lambda *args, **kwargs: ("", "")),
        )
        window.export_results()

    def test_failure_shows_dialog(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            QMessageBox,
            "critical",
            staticmethod(lambda *args, **kwargs: seen.append(str(args[2]))),
        )
        window._on_failed("RuntimeError: 炸了")
        assert len(seen) == 1
        assert "炸了" in seen[0]

    def test_render_to_pixmap(self, window: MainWindow) -> None:
        """确认整窗能渲染出非空位图（QSS / 字体链路正常）。"""
        window.resize(900, 700)
        pixmap = window.grab()
        assert not pixmap.isNull()
        assert pixmap.width() > 0
