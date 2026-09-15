r"""GUI 主题：全局 QSS 与状态配色.

样式集中在 :data:`APP_QSS` 里，通过 ``objectName`` 选择器命中控件，
**不在控件上调用 setStyleSheet**。Qt 的样式表不像 CSS 那样级联：
一旦某个控件被单独设置样式，它整棵子树都会与全局样式"断开"
（即所谓 style firewall），在 Windows 深色模式下极易出现"白底白字"。

两条踩过坑的硬性约束：

1. **不要写 ``QWidget { background-color: ... }``**。Qt 会把这条规则应用到每一个
   QWidget（包括 QLabel），于是所有标签都变成不透明色块，把父容器背景和
   QToolButton 自绘的文字整块盖住（卡片标题会直接"消失"）。背景色只给真正的容器。
2. **每个设了 font-\* / padding 的选择器都显式带 ``color:``**。缺省前景色在
   Windows 深色模式下会变成白色，落到浅色底上就是不可见。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hdu_netdebugger.theme import CHECK_STATUS_COLORS, UNICODE_ICONS

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication, QWidget

    from hdu_netdebugger.models.enums import CheckStatus

__all__ = [
    "ACCENT",
    "APP_QSS",
    "BORDER",
    "CARD_BG",
    "STATUS_COLORS",
    "TEXT",
    "TEXT_MUTED",
    "WINDOW_BG",
    "apply_theme",
    "fonts_available",
    "repolish",
    "status_color",
    "status_icon",
]

WINDOW_BG = "#f5f6f7"
CARD_BG = "#ffffff"
TEXT = "#202124"
TEXT_MUTED = "#5f6368"
BORDER = "#dadce0"
ACCENT = "#1a73e8"

#: 状态 → 颜色（与 CLI / Markdown 报告保持同一套语义）
STATUS_COLORS: dict[CheckStatus, str] = dict(CHECK_STATUS_COLORS)


def status_color(status: CheckStatus) -> str:
    """取状态对应的十六进制颜色。"""
    return STATUS_COLORS[status]


def status_icon(status: CheckStatus) -> str:
    """取状态对应的符号。"""
    return UNICODE_ICONS[status]


def fonts_available() -> bool:
    """当前 Qt 是否有可用字体。

    ``offscreen`` 平台插件不接系统字体库，而 PySide6 6.11 起又不再随附字体
    （``QFontDatabase: Cannot find font directory .../PySide6/lib/fonts``）——
    这时截图里的每个字都是豆腐块。那种截图只能核对布局与配色，不能核对文字，
    所以两个截图脚本会据此给出显式告警。
    """
    from PySide6.QtGui import QFontDatabase  # noqa: PLC0415 - 只在需要时导入 Qt

    return bool(QFontDatabase.families())


APP_QSS = f"""
QWidget {{
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "Segoe UI", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {WINDOW_BG};
}}

/* 标签一律透明，让父容器背景透出来 */
QLabel {{
    background: transparent;
    color: {TEXT};
}}

/* ---- 顶部标题栏 ---- */
#HeaderBar {{
    background-color: {CARD_BG};
    border-bottom: 1px solid {BORDER};
}}
#AppTitle {{
    color: {TEXT};
    font-size: 20px;
    font-weight: 600;
}}
#AppSubtitle {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
#SummaryLabel {{
    color: {TEXT_MUTED};
}}

/* ---- 按钮 ---- */
QPushButton {{
    background-color: {ACCENT};
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 18px;
    min-height: 22px;
}}
QPushButton:hover {{ background-color: #1b66c9; }}
QPushButton:pressed {{ background-color: #174ea6; }}
QPushButton:disabled {{ background-color: #c6c9cc; color: #f1f3f4; }}
QPushButton#SecondaryButton {{
    background-color: {CARD_BG};
    color: {ACCENT};
    border: 1px solid {BORDER};
}}
QPushButton#SecondaryButton:hover {{ background-color: #e8f0fe; }}
QPushButton#SecondaryButton:disabled {{ color: #9aa0a6; background-color: #f1f3f4; }}

/* ---- 滚动区：viewport 必须显式给背景，否则部分平台会渲染成黑色 ---- */
QScrollArea {{
    border: none;
    background-color: {WINDOW_BG};
}}
QScrollArea > QWidget > QWidget {{
    background-color: {WINDOW_BG};
}}

/* ---- 检查项卡片 ---- */
#CheckCard {{
    background-color: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
#CheckCardHeader {{
    background-color: {CARD_BG};
    border: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
#CheckCardHeader:hover {{
    background-color: #eef1f4;
}}
#CardToggle {{
    background: transparent;
    border: none;
    padding: 0px;
}}
#CheckCardStatusIcon {{
    background: transparent;
    color: {TEXT_MUTED};
    font-size: 15px;
}}
#CheckCardName {{
    color: {TEXT};
    font-size: 14px;
    font-weight: 600;
}}
#CheckCardSummary {{
    color: {TEXT_MUTED};
}}

/* ---- 卡片正文 ---- */
#BodyArea {{
    background: transparent;
}}
#SectionLabel {{
    color: {TEXT};
    font-weight: 600;
}}
#DetailText {{
    background-color: #f8f9fa;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 12px;
}}
#InfoBox {{
    background-color: #e8f0fe;
    border: 1px solid #aecbfa;
    border-radius: 4px;
}}
#WarnBox {{
    background-color: #fef7e0;
    border: 1px solid #fdd663;
    border-radius: 4px;
}}
#BoxBody {{
    color: {TEXT};
}}

QStatusBar {{
    background-color: {CARD_BG};
    color: {TEXT_MUTED};
}}
QStatusBar::item {{ border: none; }}
"""


def apply_theme(app: QApplication) -> None:
    """强制浅色主题并应用全局样式表。

    用 ``Fusion`` 风格 + 显式调色板，而不是靠样式表给所有控件刷背景色 ——
    前者是 Qt 官方的主题控制方式，不会产生"每个 QLabel 都变成不透明色块"的副作用。
    """
    from PySide6.QtGui import QColor, QPalette  # noqa: PLC0415 - 仅此处需要

    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(WINDOW_BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(CARD_BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(WINDOW_BG))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(CARD_BG))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(CARD_BG))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(APP_QSS)


def repolish(widget: QWidget) -> None:
    """动态属性改变后重新求值样式表。

    Qt 不会因为 Python 侧改了属性就自动重算样式 —— 必须手动 unpolish/polish。
    """
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
