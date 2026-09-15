"""冻结产物冒烟检查：把 ``dist/`` 下打好的可执行文件真的跑一遍.

用法::

    uv run python scripts/dist_smoke.py

先 ``uv run --group build pyinstaller --clean --noconfirm packaging/hnd.spec``，
再跑本脚本。CI 的 ``dist`` 作业就是这两步。

检查的是"**产物**能不能用"，不是"源码能不能用"（后者由 pytest 负责）：

1. ``hnd --version``：退出码 0，且版本号与 pyproject 一致 ——
   如果打印出 ``0.0.0.dev0``，说明 dist-info 没被打进去（``copy_metadata`` 失效）；
2. ``hnd list``：退出码 0，8 个检查项都在；
3. ``hnd doctor``：退出码 0 —— 这一步会在冻结环境里真的走一遍
   ctypes ``iphlpapi``、psutil 网卡枚举与三个 Ping 后端；
4. ``hnd-gui-smoke``：离屏跑一轮检查并截图，退出码 0 且 PNG 非空 ——
   证明打进包里的 Qt 插件、Python 里写的 QSS、QThread 链路都能用。

任何一项不满足 → 非 0 退出。发布前别绕过它。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

#: 冻结产物里的可执行文件名（Windows 带 .exe）
EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""


def project_version() -> str:
    """从 pyproject 读版本号，作为 ``--version`` 的期望值。"""
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def run_frozen(
    executable: Path, *args: str, timeout: float = 300.0
) -> subprocess.CompletedProcess[str]:
    """跑一个冻结产物，按 UTF-8 解码输出。

    冻结后的程序会把 stdout 固定成 UTF-8（见 ``cli._make_stdio_robust``），
    所以这里用 UTF-8 解码是**有意为之** —— 解不出来就说明那个兜底失效了。
    """
    return subprocess.run(
        [str(executable), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def check(condition: bool, message: str, failures: list[str]) -> None:
    """记一条断言结果（失败只记录，最后统一汇报，方便一次看全）。"""
    print(f"  {'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    version = project_version()
    cli = DIST / "hnd" / f"hnd{EXE_SUFFIX}"
    gui_smoke = DIST / "hnd-gui-smoke" / f"hnd-gui-smoke{EXE_SUFFIX}"

    print(f"产物目录: {DIST}")
    for executable in (cli, gui_smoke):
        if not executable.is_file():
            print(f"缺少产物: {executable}（先跑 pyinstaller）", file=sys.stderr)
            return 1

    print("[1/4] hnd --version")
    done = run_frozen(cli, "--version")
    check(done.returncode == 0, f"退出码为 0（实际 {done.returncode}）", failures)
    check(
        version in done.stdout,
        f"输出里带版本号 {version}（实际 {done.stdout.strip()!r}）",
        failures,
    )
    check(
        "0.0.0.dev0" not in done.stdout,
        "版本号不是兜底值 0.0.0.dev0（dist-info 已打进包）",
        failures,
    )

    print("[2/4] hnd list")
    done = run_frozen(cli, "list")
    check(done.returncode == 0, f"退出码为 0（实际 {done.returncode}）", failures)
    check("检查DNS可用性" in done.stdout, "能列出检查项（UTF-8 中文正常）", failures)
    check("检查公网可达性" in done.stdout, "8 项都在", failures)

    print("[3/4] hnd doctor（ctypes / psutil / 三个 Ping 后端）")
    done = run_frozen(cli, "doctor")
    check(done.returncode == 0, f"退出码为 0（实际 {done.returncode}）", failures)
    if done.returncode != 0:
        print(done.stdout[-2000:], file=sys.stderr)

    print("[4/4] hnd-gui-smoke（离屏跑一轮检查并截图）")
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "gui_smoke.png"
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        done = subprocess.run(
            [str(gui_smoke), str(png)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300.0,
            check=False,
            env=environment,
        )
        check(done.returncode == 0, f"退出码为 0（实际 {done.returncode}）", failures)
        check(png.is_file() and png.stat().st_size > 0, "截图非空", failures)
        if done.returncode != 0:
            print(done.stdout[-2000:], file=sys.stderr)
            print(done.stderr[-2000:], file=sys.stderr)

    if failures:
        print(f"\n冻结产物冒烟失败（{len(failures)} 项）:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("\n冻结产物冒烟全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
