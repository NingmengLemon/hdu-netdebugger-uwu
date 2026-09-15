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

import _ssl
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

#: CPython 自带的 OpenSSL：与 ``_ssl.pyd`` 同目录的兄弟文件
OPENSSL_NAMES = frozenset({"libssl-3-x64.dll", "libcrypto-3-x64.dll"})
OPENSSL_DIR = Path(_ssl.__file__).parent if _ssl.__file__ else None


def pin_python_openssl(analysis: Analysis) -> None:
    """把 OpenSSL 钉回 CPython 自带的那一份.

    踩过的坑：``_ssl.pyd`` 依赖 ``libssl-3-x64.dll`` / ``libcrypto-3-x64.dll``，
    Qt 的 TLS 插件（``PySide6/plugins/tls/qopensslbackend.dll``）依赖**同名**的 DLL，
    但插件自己目录里没有，于是 PyInstaller 顺着 PATH 到处找 —— 本机 PATH 上有
    OpenJ9 的 Java（``jdk-*/bin/libcrypto-3-x64.dll``，5.2 MB，与 CPython 那份 8.0 MB
    不同），于是**那个**被收进了包，把 CPython 自己那份挤掉。症状是冻结的 GUI
    一发起 HTTPS 就 ``ImportError: DLL load failed while importing _ssl:
    找不到指定的程序`` —— 而且会被检查项的异常兜底吞成一个普通的"失败"结论，
    看起来像"网不通"，实际是打包问题；CLI 产物因为不含 Qt 反而没事。

    这里做两件事：丢掉一切不来自 CPython 的 OpenSSL，再显式补上 CPython 的。
    这样产物与 PATH 上装了什么无关（可复现），Qt 的 TLS 插件也照样能加载。
    """
    if OPENSSL_DIR is None:  # pragma: no cover - 静态链接的解释器
        return

    analysis.binaries = [
        entry
        for entry in analysis.binaries
        if Path(entry[0]).name.lower() not in OPENSSL_NAMES
        or Path(entry[1]).parent == OPENSSL_DIR
    ]

    present = {Path(entry[0]).name.lower() for entry in analysis.binaries}
    for name in sorted(OPENSSL_NAMES - present):
        source = OPENSSL_DIR / name
        if source.is_file():
            analysis.binaries.append((name, str(source), "BINARY"))


def bundle(entry: str, name: str, *, console: bool, excludes: list[str]) -> None:
    """分析一个入口并产出 ``dist/<name>/`` 目录式产物。"""
    analysis = Analysis(
        [str(ROOT / entry)],
        pathex=[str(ROOT / "src")],
        datas=DATAS,
        excludes=excludes,
    )
    pin_python_openssl(analysis)
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
