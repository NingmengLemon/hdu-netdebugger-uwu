"""检查候选符号在目标平台字体里是否有字形.

跨平台 GUI/CLI 最容易踩的坑：某个 unicode 符号在当前字体里没有字形，
渲染出来就是一个"豆腐块"。本脚本用 Qt 的字体度量在**本机**逐个探测。

用法::

    uv run python scripts/check_glyphs.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontMetrics, QRawFont
from PySide6.QtWidgets import QApplication

#: 当前代码里用到、以及候选替换的符号
CANDIDATES: dict[str, str] = {
    "状态-通过": "\u2714",  # ✔
    "状态-失败": "\u2716",  # ✖
    "状态-警告": "\u26a0",  # ⚠
    "状态-跳过": "\u2298",  # ⊘
    "状态-未检查": "\u25cb",  # ○
    "状态-检查中": "\u25cc",  # ◌
    "现有-建议灯泡": "\U0001f4a1",  # 💡 (补充平面)
    "候选-参考标记": "\u203b",  # ※
    "候选-指示手": "\u261e",  # ☞
    "候选-星": "\u2605",  # ★
    "候选-花朵": "\u273f",  # ✿
    "候选-灯泡(基本平面)": "\u263c",  # ☼
    "候选-勾选框": "\u2611",  # ☑
    "候选-圆点": "\u25cf",  # ●
    "候选-向右三角": "\u25b8",  # ▸
}


def main() -> int:
    app = QApplication.instance() or QApplication(["check-glyphs"])
    font = app.font()
    metrics = QFontMetrics(font)
    raw = QRawFont.fromFont(font)

    print(f"平台      : {app.platformName()} ({sys.platform})")
    print(f"默认字体  : {font.family()}")
    print()
    print(f"{'用途':<22} {'码点':<10} {'字符':<4} 有字形")
    print("-" * 52)

    missing: list[str] = []
    for label, char in CANDIDATES.items():
        present = raw.supportsCharacter(ord(char))
        # 再用 QFontMetrics 交叉验证一次（Qt 的字体回退也参与其中）
        width = metrics.horizontalAdvance(char)
        ok = present and width > 0
        if not ok:
            missing.append(label)
        print(f"{label:<22} U+{ord(char):05X}   {char:<4} {'是' if ok else '否'}   (宽度 {width})")

    print()
    if missing:
        print("缺少字形的符号：")
        for label in missing:
            print(f"  - {label}")
        return 1
    print("全部符号都有字形。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
