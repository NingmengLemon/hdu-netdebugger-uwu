"""可折叠的单项检查卡片.

对应原版 ``Components/CheckItemView.axaml``：标题行是「展开箭头 + 状态图标 + 名称 + 总览」，
展开后依次显示建议、警告与详细日志。

标题行刻意用 ``QFrame`` + 一组 ``QLabel`` 自己拼，而不是给 ``QToolButton`` 叠一层布局：
后者会让按钮自绘的文字被覆盖层的不透明背景挡住，表现为"标题空白"。

所有外观都靠 ``objectName`` 命中 ``style.APP_QSS``，控件本身不调用 ``setStyleSheet``
（只有随状态变化的图标颜色例外，那种情况下会连同 ``color`` 一起写全）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from hdu_netdebugger.gui.style import status_color, status_icon
from hdu_netdebugger.models.enums import CheckStatus

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent

    from hdu_netdebugger.models.result import CheckOutcome
    from hdu_netdebugger.registry import CheckerSpec

__all__ = ["CheckCard"]

_UNCHECKED_DETAIL = "此检查尚未进行"


class _HeaderBar(QFrame):
    """整行可点击的标题栏。"""

    activated = Signal()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.activated.emit()
        super().mouseReleaseEvent(event)


class CheckCard(QFrame):
    """单个检查项的展示卡片。"""

    def __init__(self, spec: CheckerSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CheckCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        self._spec = spec
        self._outcome: CheckOutcome | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = self._build_header()
        root.addWidget(self._header)

        self._body = self._build_body()
        self._body.setVisible(False)
        root.addWidget(self._body)

        self.reset()

    # -- 构建 -------------------------------------------------------------

    def _build_header(self) -> _HeaderBar:
        header = _HeaderBar(self)
        header.setObjectName("CheckCardHeader")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header.activated.connect(self.toggle)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(8, 8, 12, 8)
        layout.setSpacing(8)

        self._toggle = QToolButton(header)
        self._toggle.setObjectName("CardToggle")
        self._toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle.setAutoRaise(True)
        self._toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._toggle.clicked.connect(self.toggle)
        layout.addWidget(self._toggle)

        self._icon = QLabel(header)
        self._icon.setObjectName("CheckCardStatusIcon")
        self._icon.setFixedWidth(20)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._icon)

        self._name = QLabel(self._spec.name, header)
        self._name.setObjectName("CheckCardName")
        layout.addWidget(self._name)

        self._summary = QLabel("", header)
        self._summary.setObjectName("CheckCardSummary")
        self._summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._summary, 1)

        return header

    def _build_body(self) -> QWidget:
        body = QWidget(self)
        body.setObjectName("BodyArea")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 0, 14, 12)
        layout.setSpacing(8)

        self._suggestion_box, self._suggestion_body = self._build_box(
            body, "InfoBox", "建议：", "💡"
        )
        self._warning_box, self._warning_body = self._build_box(body, "WarnBox", "警告：", "⚠")
        layout.addWidget(self._suggestion_box)
        layout.addWidget(self._warning_box)

        detail_label = QLabel("详细信息：", body)
        detail_label.setObjectName("SectionLabel")
        layout.addWidget(detail_label)

        self._details = QPlainTextEdit(body)
        self._details.setObjectName("DetailText")
        self._details.setReadOnly(True)
        self._details.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._details.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._details)

        return body

    @staticmethod
    def _build_box(
        parent: QWidget,
        object_name: str,
        title: str,
        glyph: str,
    ) -> tuple[QWidget, QLabel]:
        """构造一个带标题的信息框，返回 ``(容器, 正文标签)``。"""
        box = QFrame(parent)
        box.setObjectName(object_name)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        heading = QLabel(f"{glyph} {title}", box)
        heading.setObjectName("SectionLabel")
        layout.addWidget(heading)

        body = QLabel(box)
        body.setObjectName("BoxBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)
        return box, body

    # -- 展开 / 收起 -------------------------------------------------------

    def toggle(self) -> None:
        """切换展开状态。"""
        self.set_expanded(not self._body.isVisible())

    def set_expanded(self, expanded: bool) -> None:
        """展开/收起正文（失败与警告项会自动展开，与原版一致）。"""
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._body.setVisible(expanded)

    @property
    def is_expanded(self) -> bool:
        """当前是否展开。"""
        return self._body.isVisible()

    # -- 状态更新 ---------------------------------------------------------

    def reset(self) -> None:
        """回到「未检查」状态。"""
        self._outcome = None
        self._summary.setText("")
        self._apply_status(CheckStatus.UNCHECKED)
        self._suggestion_box.setVisible(False)
        self._warning_box.setVisible(False)
        self._details.setPlainText(_UNCHECKED_DETAIL)
        self._fit_details_height()
        self.set_expanded(False)

    def mark_running(self) -> None:
        """标记为「检查中」。"""
        self._summary.setText("正在检查…")
        self._apply_status(CheckStatus.CHECKING)

    def apply(self, outcome: CheckOutcome) -> None:
        """展示一次检查结果。"""
        self._outcome = outcome
        self._summary.setText(outcome.summary)
        self._apply_status(outcome.status)

        self._suggestion_body.setText("\n".join(f"• {item}" for item in outcome.suggestions))
        self._suggestion_box.setVisible(bool(outcome.suggestions))

        self._warning_body.setText("\n".join(f"• {item}" for item in outcome.warnings))
        self._warning_box.setVisible(bool(outcome.warnings))

        self._details.setPlainText(outcome.details or "（无详细日志）")
        self._fit_details_height()
        if outcome.is_problem:
            self.set_expanded(True)

    def _apply_status(self, status: CheckStatus) -> None:
        """更新状态图标与其颜色。

        这里显式带上 ``color``：Qt 的样式表不做级联，只写 font-size 会让前景色
        回落到未定义值（Windows 深色模式下就是白字白底）。
        """
        self._icon.setText(status_icon(status))
        self._icon.setStyleSheet(
            f"color: {status_color(status)}; font-size: 15px; background: transparent;"
        )

    def _fit_details_height(self) -> None:
        """按内容行数调整详细日志高度，避免卡片过长或过短。"""
        lines = self._details.toPlainText().count("\n") + 1
        line_height = self._details.fontMetrics().lineSpacing()
        self._details.setFixedHeight(min(max(lines, 3), 16) * line_height + 18)

    # -- 只读属性 ---------------------------------------------------------

    @property
    def spec_name(self) -> str:
        """对应的检查项名称。"""
        return self._spec.name

    @property
    def outcome(self) -> CheckOutcome | None:
        """当前展示的结果；尚未检查时为 ``None``。"""
        return self._outcome
