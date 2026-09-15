"""用固定样本渲染主窗口，用于人工检查排版（生成截图）。

用法::

    uv run python scripts/gui_render_sample.py [输出路径]

不需要真实网络：直接构造 :class:`CheckOutcome` 灌进卡片，
覆盖「通过 / 警告 / 跳过」三种状态，方便确认配色与折叠行为。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hdu_netdebugger.checkers import load_builtin_checkers
from hdu_netdebugger.gui.main_window import MainWindow
from hdu_netdebugger.gui.style import apply_theme, fonts_available
from hdu_netdebugger.models.enums import CheckStatus
from hdu_netdebugger.models.result import CheckOutcome

SAMPLES: list[CheckOutcome] = [
    CheckOutcome(
        name="获取基本信息",
        order=-1,
        status=CheckStatus.WARNED,
        summary="未检测到校园网地址",
        details=(
            "接口名称: WLAN\n"
            "描述: Realtek 8852CE WiFi 6E PCI-E NIC\n"
            "IPv4 地址: 10.150.0.100\n"
            "IPv6 地址: 2409:8929:b57d:45a1:253e:7aa8:cca1:40b5\n"
            "\n"
            "检测到活动的 propVirtual 类型网络接口，可能为 VPN 或其他隧道接口，详情如下：\n"
            "接口名称: Mihomo\n"
            "描述: Meta Tunnel\n"
            "IPv4 地址: 198.18.0.1"
        ),
        warnings=["检测到 propVirtual 类型网络接口", "未检测到校园网地址"],
        suggestions=[
            "建议关闭 VPN 或其他隧道接口",
            "请检查网络连接，确保已连接到校园网，如果在路由器下使用，请忽略",
        ],
        duration_s=0.02,
    ),
    CheckOutcome(
        name="检查代理状态",
        order=5,
        status=CheckStatus.PASSED,
        summary="未检测到代理配置",
        details='--使用方法 "系统代理设置" 进行代理检测--\n未检测到代理配置。',
        duration_s=0.0,
    ),
    CheckOutcome(
        name="检查BAS可达性",
        order=10,
        status=CheckStatus.PASSED,
        summary="BAS 可达",
        details=(
            "开始对 10.150.0.1 进行 4 次 Ping 测试...\n"
            "Ping 10.150.0.1 第 1 次: 状态:成功, 时间:2ms\n"
            "Ping 测试完成: 成功次数:4/4, 平均时间:2ms, 最小时间:1ms, 最大时间:3ms"
        ),
        duration_s=0.61,
    ),
    CheckOutcome(
        name="检查生活区深澜认证可达性",
        order=15,
        status=CheckStatus.PASSED,
        summary="深澜认证服务器可达",
        details="Ping 测试完成: 成功次数:4/4, 平均时间:2ms",
        duration_s=0.61,
    ),
    CheckOutcome(
        name="检查DNS可用性",
        order=20,
        status=CheckStatus.PASSED,
        summary="DNS 查询成功，返回了正确的 A 记录",
        details="DNS 查询成功，返回结果如下：\nportal.hdu.edu.cn. 1656 IN A 192.168.112.97",
        duration_s=0.01,
    ),
    CheckOutcome(
        name="检查认证状态",
        order=25,
        status=CheckStatus.PASSED,
        summary="认证状态正常",
        details=(
            "DNS 可用，使用域名进行认证状态请求。\n"
            "请求 URL: https://portal.hdu.edu.cn/cgi-bin/rad_user_info?callback=jQuery\n"
            "认证响应内容:\n运营商: 电信\n认证方式: mac-auth\n错误信息: ok"
        ),
        duration_s=0.34,
    ),
    CheckOutcome(
        name="检查运营商网关可达性",
        order=30,
        status=CheckStatus.SKIPPED,
        summary="检查被跳过",
        details='条件 "运营商ID非空且有效" 未满足，跳过此检查',
        skipped_reason="运营商ID非空且有效",
        duration_s=0.0,
    ),
    CheckOutcome(
        name="检查公网可达性",
        order=35,
        status=CheckStatus.FAILED,
        summary="公网不可达",
        details="Ping 223.5.5.5: 失败 4/4\n请求 http://connect.rom.miui.com/generate_204 失败: ConnectTimeout",
        warnings=["阿里主DNS不可达。", "无法通过HTTP访问公网地址。"],
        suggestions=["请检查IP配置或尝试重新进行物理连接"],
        duration_s=5.4,
    ),
]


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("gui_sample.png")
    load_builtin_checkers()

    app = QApplication.instance() or QApplication(["gui_render_sample"])
    apply_theme(app)

    if not fonts_available():
        print(
            "警告: 当前 Qt 没有可用字体（offscreen 插件不接系统字体库），"
            "截图里的文字会是豆腐块 —— 只能用来核对布局与配色。",
            file=sys.stderr,
        )

    window = MainWindow()
    window.resize(1000, 1000)

    by_name = {outcome.name: outcome for outcome in SAMPLES}
    for card in window._cards:  # noqa: SLF001 - 渲染脚本读取界面对象
        outcome = by_name.get(card.spec_name)
        if outcome is not None:
            card.apply(outcome)
    window._summary.setText("共 8 项 · 通过 4 · 警告 1 · 失败 1 · 跳过 1")  # noqa: SLF001
    window._export_button.setEnabled(True)  # noqa: SLF001

    # 展开一个「通过」项，确认正常项的折叠 + 展开排版
    for card in window._cards:  # noqa: SLF001
        if card.spec_name == "检查DNS可用性":
            card.set_expanded(True)

    window.grab().save(str(output))
    print(f"screenshot -> {output}")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
