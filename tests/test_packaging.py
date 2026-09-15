"""打包资产的一致性闸门.

``packaging/`` 下的东西不在 ruff / ty 的检查范围内（``.spec`` 不是 Python 模块，
入口脚本也不算包的一部分），可它们一旦写错，代价是"构建到一半才炸"或者
"构建出一个版本号不对、或者根本没打包元数据的产物而没人发现"。
所以这里单独立几道闸门。

注意：这些用例**不能**导入 PyInstaller —— 它只在 ``build`` 依赖组里，
只同步了 dev 组的人应该照样能跑完整测试套件（否则又变成"缺依赖 = 静默跳过"）。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"


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


def test_entries_delegate_to_package_entry_points() -> None:
    """入口脚本是薄壳，真正的入口在包里 —— 免得复制一份逻辑出来各自漂移。"""
    cli = (PACKAGING / "entry_cli.py").read_text(encoding="utf-8")
    gui = (PACKAGING / "entry_gui.py").read_text(encoding="utf-8")

    assert "from hdu_netdebugger.cli import main" in cli
    assert "from hdu_netdebugger.gui import launch" in gui
