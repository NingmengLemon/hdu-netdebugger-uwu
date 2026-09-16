"""把子进程的控制台输出解码成文本.

Windows 的 ``ping`` / ``tracert`` 往管道里写的是**OEM 代码页**（中文系统通常是 936），
不是 UTF-8；Linux / macOS 则跟随 locale。冻结后的程序改不动这个行为，所以只能按
平台选对编码再解。

原先这个函数藏在 :mod:`hdu_netdebugger.net.ping` 里，``net.trace`` 也要用，
于是提出来共用（同一条约定只留一处实现）。
"""

from __future__ import annotations

import locale
import sys

__all__ = ["decode_console_output"]


def decode_console_output(data: bytes) -> str:
    """按当前平台的控制台编码解码子进程输出，失败时用替换字符兜底。"""
    encoding = "oem" if sys.platform == "win32" else locale.getpreferredencoding(do_setlocale=False)
    try:
        return data.decode(encoding, errors="replace")
    except LookupError:  # pragma: no cover - 编码名不可用时退回 UTF-8
        return data.decode("utf-8", errors="replace")
