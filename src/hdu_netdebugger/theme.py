"""状态 → 图标 / 颜色 的展示映射.

CLI 与 GUI 共用这一份定义，保证同一个状态在两处呈现一致
（对应原版 ``Converters/StatusToIconConverter.cs`` 与 ``StatusToIconColorConverter.cs``）。
"""

from __future__ import annotations

from types import MappingProxyType

from hdu_netdebugger.models.enums import CheckStatus

__all__ = [
    "CHECK_STATUS_COLORS",
    "CHECK_STATUS_EMOJI",
    "CHECK_STATUS_STYLES",
    "UNICODE_ICONS",
    "icon_for",
]

#: 终端 / GUI 使用的符号（宽字符集，绝大多数终端字体都能显示）
UNICODE_ICONS: MappingProxyType[CheckStatus, str] = MappingProxyType(
    {
        CheckStatus.UNCHECKED: "○",
        CheckStatus.CHECKING: "◌",
        CheckStatus.PASSED: "✔",
        CheckStatus.WARNED: "⚠",
        CheckStatus.FAILED: "✖",
        CheckStatus.SKIPPED: "⊘",
    }
)

#: 纯文本环境下的降级符号（ASCII）
_ASCII_ICONS: MappingProxyType[CheckStatus, str] = MappingProxyType(
    {
        CheckStatus.UNCHECKED: "-",
        CheckStatus.CHECKING: "~",
        CheckStatus.PASSED: "OK",
        CheckStatus.WARNED: "!!",
        CheckStatus.FAILED: "XX",
        CheckStatus.SKIPPED: "--",
    }
)

#: Markdown 报告使用的 emoji
CHECK_STATUS_EMOJI: MappingProxyType[CheckStatus, str] = MappingProxyType(
    {
        CheckStatus.UNCHECKED: "⬜",
        CheckStatus.CHECKING: "🔄",
        CheckStatus.PASSED: "✅",
        CheckStatus.WARNED: "⚠️",
        CheckStatus.FAILED: "❌",
        CheckStatus.SKIPPED: "⏭️",
    }
)

#: rich 终端样式
CHECK_STATUS_STYLES: MappingProxyType[CheckStatus, str] = MappingProxyType(
    {
        CheckStatus.UNCHECKED: "dim",
        CheckStatus.CHECKING: "bold cyan",
        CheckStatus.PASSED: "bold green",
        CheckStatus.WARNED: "bold yellow",
        CheckStatus.FAILED: "bold red",
        CheckStatus.SKIPPED: "dim",
    }
)

#: GUI (Qt) 颜色
CHECK_STATUS_COLORS: MappingProxyType[CheckStatus, str] = MappingProxyType(
    {
        CheckStatus.UNCHECKED: "#9e9e9e",
        CheckStatus.CHECKING: "#2196f3",
        CheckStatus.PASSED: "#4caf50",
        CheckStatus.WARNED: "#f9a825",
        CheckStatus.FAILED: "#e53935",
        CheckStatus.SKIPPED: "#757575",
    }
)


def icon_for(status: CheckStatus, *, ascii_only: bool = False) -> str:
    """取状态对应的图标符号。"""
    return _ASCII_ICONS[status] if ascii_only else UNICODE_ICONS[status]
