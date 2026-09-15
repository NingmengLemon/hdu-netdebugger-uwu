"""冻结入口：控制台版 CLI.

单独放一个入口文件，而不是把 ``cli.py`` 本身当脚本塞进 Analysis：

* PyInstaller 需要明确的起点，且不希望把包内模块当 ``__main__`` 跑两遍；
* CLI 与 GUI 的入口分开放，spec 里一眼能看出哪个产物对应哪个入口。
"""

from __future__ import annotations

from hdu_netdebugger.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
