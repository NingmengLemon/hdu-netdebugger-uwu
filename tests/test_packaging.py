"""打包资产与仓库声明的一致性闸门.

``packaging/`` 下的东西不在 ruff / ty 的检查范围内（``.spec`` 不是 Python 模块，
入口脚本也不算包的一部分），可它们一旦写错，代价是"构建到一半才炸"或者
"构建出一个版本号不对、或者根本没打包元数据的产物而没人发现"。
README 里那些会过期的数字同理。所以这里单独立几道闸门。

注意：这些用例**不能**导入 PyInstaller —— 它只在 ``build`` 依赖组里，
只同步了 dev 组的人应该照样能跑完整测试套件（否则又变成"缺依赖 = 静默跳过"）。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
README = ROOT / "README.md"


def project_version() -> str:
    """读 pyproject 里的 version（版本的唯一事实来源）。"""
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def version_quad(version: str) -> tuple[int, int, int, int]:
    """把 ``0.1.0`` 之类的版本号补齐成 PE 要求的四项元组。"""
    numbers = [int(part) for part in re.findall(r"\d+", version)][:4]
    padded = [*numbers, 0, 0, 0, 0]
    return (padded[0], padded[1], padded[2], padded[3])


def test_version_info_matches_pyproject() -> None:
    """PE 版本资源必须与 pyproject 一致，否则"属性里"和"包里"是两套版本号。"""
    version = project_version()
    expected = version_quad(version)
    text = (PACKAGING / "version_info.txt").read_text(encoding="utf-8")

    for label in ("filevers", "prodvers"):
        match = re.search(rf"{label}=\(([^)]*)\)", text)
        assert match is not None, f"version_info.txt 里找不到 {label}"
        numbers = tuple(int(item.strip()) for item in match.group(1).split(","))
        assert numbers == expected, f"{label}={numbers} 与 pyproject 的 {version} 不一致"

    for label in ("FileVersion", "ProductVersion"):
        match = re.search(rf"StringStruct\('{label}', '([^']*)'\)", text)
        assert match is not None, f"version_info.txt 里找不到 {label}"
        assert match.group(1) == version


def test_version_quad_pads_and_truncates() -> None:
    """自检：补齐/截断逻辑本身要正确，否则闸门只是摆设。"""
    assert version_quad("0.1.0") == (0, 1, 0, 0)
    assert version_quad("1.2.3.4") == (1, 2, 3, 4)
    assert version_quad("1.2.3.4.5") == (1, 2, 3, 4)


def test_version_info_is_syntactically_valid() -> None:
    """PyInstaller 会 eval 这个文件；语法错要到构建中期才暴露，这里先拦住。"""
    text = (PACKAGING / "version_info.txt").read_text(encoding="utf-8")
    compile(text, str(PACKAGING / "version_info.txt"), "exec")


def test_spec_entries_exist_and_metadata_is_collected() -> None:
    """spec 引用的入口脚本要真的存在；且必须收集 dist-info。"""
    text = (PACKAGING / "hnd.spec").read_text(encoding="utf-8")

    entries = re.findall(r'bundle\("([^"]+)"', text)
    assert entries, "没解析到任何 bundle() 调用"
    for entry in entries:
        assert (ROOT / entry).is_file(), f"spec 引用的入口不存在: {entry}"

    # 少了它，冻结后的 __version__ 会退化成 "0.0.0.dev0"（PackageNotFoundError 分支）
    assert 'copy_metadata("hdu-netdebugger-uwu")' in text


def test_spec_builds_console_and_windowed_variants() -> None:
    """CLI 要控制台、GUI 不要控制台；CLI 必须排除 Qt 才谈得上"又小又快"。"""
    text = (PACKAGING / "hnd.spec").read_text(encoding="utf-8")

    assert 'bundle("packaging/entry_cli.py", "hnd", console=True' in text
    assert 'bundle("packaging/entry_gui.py", "HDU-NetDebugger", console=False' in text
    assert "excludes=QT_MODULES" in text


def test_spec_pins_python_openssl() -> None:
    """OpenSSL 必须钉回 CPython 自带的那份.

    回归：Qt 的 TLS 插件依赖 ``libssl-3-x64.dll`` / ``libcrypto-3-x64.dll`` 但自己
    目录里没有，PyInstaller 会顺着 PATH 捞 —— 本机 PATH 上有 OpenJ9 的 Java，
    于是把 Java 那份收进包、挤掉了 CPython 的，冻结的 GUI 一发起 HTTPS 就
    ``DLL load failed while importing _ssl``（还会被兜成一句普通的"失败"结论）。
    """
    text = (PACKAGING / "hnd.spec").read_text(encoding="utf-8")
    assert "pin_python_openssl(analysis)" in text, "spec 里没有调用 OpenSSL 钉回逻辑"
    assert "OPENSSL_DIR" in text
    assert "libcrypto-3-x64.dll" in text


def test_entries_delegate_to_package_entry_points() -> None:
    """入口脚本是薄壳，真正的入口在包里 —— 免得复制一份逻辑出来各自漂移。"""
    cli = (PACKAGING / "entry_cli.py").read_text(encoding="utf-8")
    gui = (PACKAGING / "entry_gui.py").read_text(encoding="utf-8")

    assert "from hdu_netdebugger.cli import main" in cli
    assert "from hdu_netdebugger.gui import launch" in gui


def collected_test_count() -> int:
    """跑一次 ``pytest --collect-only`` 拿到真实用例数。

    收集阶段不执行用例体，所以这一次子进程只花一两秒；用 ``sys.executable``
    而不是 ``pytest`` 命令，保证跑的是当前解释器里那套。
    """
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-cov",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    match = re.search(r"(\d+) tests? collected", done.stdout)
    assert match is not None, f"没能解析出用例数:\n{done.stdout[-2000:]}"
    return int(match.group(1))


def test_readme_test_count_matches_reality() -> None:
    """README 里的用例数必须等于实际收集到的数量.

    这个数字已经漂过两次（380 → 470 → 487），所以做成闸门而不是靠"记得改"：
    凡是 README 里出现的"<数字> [pytest] 用例"都必须一致且正确。
    """
    actual = collected_test_count()
    stated = {
        int(item)
        for item in re.findall(
            r"(\d+)\s*个\s*(?:pytest\s*)?用例", README.read_text(encoding="utf-8")
        )
    }

    assert stated, "README 里没找到'<数字> 个用例'的说法（是不是改了措辞？）"
    assert stated == {actual}, f"README 写的是 {sorted(stated)}，实际收集到 {actual}"
