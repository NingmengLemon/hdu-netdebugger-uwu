# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本：一次构建出三个产物.

==================  ======  ====================================================
产物                子系统  用途
==================  ======  ====================================================
``hnd``             控制台  CLI（``hnd run`` 等）；排除 PySide6，又小又快
``HDU-NetDebugger`` 窗口    GUI，不带控制台窗口
``hnd-gui-smoke``   控制台  只给 CI 用：冻结 ``scripts/gui_smoke.py`` 跑一轮，
                            证明打进去的 Qt 插件 / QSS / QThread 真的能工作
==================  ======  ====================================================

用法（**必须在项目根目录**执行）::

    uv run --group build pyinstaller --clean --noconfirm packaging/hnd.spec

为什么强调工作目录：PyInstaller 5.4+ 在 Windows 上若从含 ``__init__.py`` 的目录
启动，会静默弄坏 Qt6 的 DLL 解析（冻结后 ``import QtWidgets`` 报 DLL load failed）。
项目根目录没有 ``__init__.py``，源码在 ``src/`` 下，正好避开。

技术选择说明：

* **onedir 而非 onefile**：onefile 每次启动都要解压到临时目录（几秒），
  还会触发杀软"自解压 + 执行"的启发式；本工具会被反复调用，不该付这个税。
* **不用 UPX**：省下的体积不到 5 MB，而 UPX 会弄坏 Qt 插件与开了 CFG 的 DLL。
* **不设 runtime_options**：skill 里写的 ``runtime_options=['utf8_mode=1']``
  在 PyInstaller 6.22.3 里并不存在（已改名 ``--python-option``）。冻结后非 ASCII
  输出的问题改在应用层兜住：``cli.main()`` 里的 ``_make_stdio_robust()``
  把 stdout/stderr 的错误策略放宽成 replace。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

#: PyInstaller 注入的变量，指向本 spec 所在目录（<repo>/packaging）
ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH 由 PyInstaller 注入

# ``__init__.py`` 用 importlib.metadata.version("hdu-netdebugger-uwu") 取版本号；
# 冻结后 dist-info 不会自动带上，必须显式收集，否则 __version__ 会退化成
# "0.0.0.dev0"（那条 except PackageNotFoundError 分支）。
DATAS = copy_metadata("hdu-netdebugger-uwu")

VERSION_FILE = ROOT / "packaging" / "version_info.txt"

#: GUI 产物需要，CLI 产物要排掉（gui/__init__.py 只在 launch() 里导入 Qt，安全）
QT_MODULES = ["PySide6", "shiboken6"]


def bundle(entry: str, name: str, *, console: bool, excludes: list[str]) -> None:
    """分析一个入口并产出 ``dist/<name>/`` 目录式产物。"""
    analysis = Analysis(
        [str(ROOT / entry)],
        pathex=[str(ROOT / "src")],
        datas=DATAS,
        excludes=excludes,
    )
    pyz = PYZ(analysis.pure)
    exe = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        upx=False,
        icon=None,
        version=str(VERSION_FILE) if sys.platform == "win32" else None,
    )
    COLLECT(exe, analysis.binaries, analysis.datas, name=name)


bundle("packaging/entry_cli.py", "hnd", console=True, excludes=QT_MODULES)
bundle("packaging/entry_gui.py", "HDU-NetDebugger", console=False, excludes=[])
bundle("scripts/gui_smoke.py", "hnd-gui-smoke", console=True, excludes=[])
