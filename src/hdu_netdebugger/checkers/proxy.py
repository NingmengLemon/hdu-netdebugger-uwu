"""检查 2：代理状态.

对应原版 ``Checkers/ProxyCheck.cs``。原版探测两条路径：

* ``HttpClientHandler`` 解析到的默认代理（即操作系统代理设置）；
* ``HTTP_PROXY`` / ``HTTPS_PROXY`` 等环境变量。

Python 生态没有「系统代理」的统一 API，因此这里按平台读取真实的系统代理配置：
Windows 读 WinINET 注册表项，macOS 调 ``scutil --proxy``，其余平台跳过并说明原因。

环境变量这一路要服从 ``--no-env-proxy``（:attr:`RunConfig.trust_env_proxy`）：
显式忽略时只记录不判定，否则会出现"请求确实绕开了代理，检查却判失败"的矛盾。
系统代理不受该开关影响（它本来也不参与本工具自己的 HTTP 请求）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from hdu_netdebugger.checkers.base import Checker
from hdu_netdebugger.registry import checker

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["ProxyCheck", "ProxyHit", "detect_env_proxy", "detect_system_proxy"]


class ProxyHit:
    """一处检测到的代理配置。"""

    __slots__ = ("detail", "source")

    def __init__(self, source: str, detail: str) -> None:
        self.source = source
        self.detail = detail


@checker("检查代理状态", order=5)
class ProxyCheck(Checker):
    """检测系统代理与环境变量代理。"""

    async def check(self) -> None:
        self.log('--使用方法 "系统代理设置" 进行代理检测--')
        system_hits = await asyncio.to_thread(detect_system_proxy)
        self._log_hits(system_hits)

        self.log()
        self.log('--使用方法 "环境变量代理" 进行代理检测--')
        env_hits = detect_env_proxy(os.environ)
        self._log_hits(env_hits)
        if env_hits and not self.config.trust_env_proxy:
            # 用户显式要求忽略环境变量代理，那就只记录、不判为问题 ——
            # 否则 --no-env-proxy 一边生效（HTTP 请求确实绕开代理）一边把检查判失败。
            self.log("本次运行指定了 --no-env-proxy，环境变量代理不参与判定。")
            env_hits = []

        hits = [*system_hits, *env_hits]
        if hits:
            self.add_warning("检测到代理配置，可能会影响连接。")
            self.add_suggestion("请检查系统代理设置并关闭代理，或取消相关环境变量的设置。")
            for hit in hits:
                self.log(f"  - [{hit.source}] {hit.detail}")
            self.failed("检测到代理配置")
        else:
            self.passed("未检测到代理配置")

        if self.context.has_tunnel_interface:
            self.add_warning("检测到 Tunnel 类型网络接口")
            self.add_suggestion("建议关闭 VPN 或其他隧道接口")

    def _log_hits(self, hits: Iterable[ProxyHit]) -> None:
        """把检测结果写进详细日志。"""
        materialized = list(hits)
        if materialized:
            self.log("检测到代理配置。")
            for hit in materialized:
                self.log(f"  - [{hit.source}] {hit.detail}")
        else:
            self.log("未检测到代理配置。")


def detect_env_proxy(environ: object) -> list[ProxyHit]:
    """检测 ``HTTP_PROXY`` / ``HTTPS_PROXY`` 等环境变量（大小写都查）。"""
    getter = getattr(environ, "get", None)
    if getter is None:  # pragma: no cover - 防御性
        return []

    hits: list[ProxyHit] = []
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        value = getter(name)
        if value:
            hits.append(ProxyHit(source=name, detail=str(value)))
    return hits


def detect_system_proxy() -> list[ProxyHit]:
    """按平台读取系统代理设置。"""
    if sys.platform == "win32":
        return _windows_system_proxy()
    if sys.platform == "darwin":
        return _macos_system_proxy()
    return []


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _is_loopback_proxy(value: str) -> bool:
    """判断代理地址是否指向本机（本地代理通常是用户自己开的加速器）。"""
    candidate = value.split("=", 1)[-1]
    parsed = urlsplit(candidate if "//" in candidate else f"//{candidate}")
    return (parsed.hostname or "") in _LOOPBACK_HOSTS


def _windows_system_proxy() -> list[ProxyHit]:
    """读取 WinINET 的 ``ProxyEnable`` / ``ProxyServer`` 注册表项。"""
    try:
        import winreg  # noqa: PLC0415 - 仅 Windows 可用
    except ImportError:  # pragma: no cover - 仅在非 Windows 上触发
        return []

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enabled = _read_registry_value(key, "ProxyEnable", winreg)
            server = _read_registry_value(key, "ProxyServer", winreg)
            auto_config = _read_registry_value(key, "AutoConfigURL", winreg)
    except OSError:
        return []

    hits: list[ProxyHit] = []
    if enabled and server:
        hits.extend(
            ProxyHit(source="系统代理", detail=entry.strip())
            for entry in str(server).split(";")
            if entry.strip() and not _is_loopback_proxy(entry)
        )
    if auto_config:
        hits.append(ProxyHit(source="PAC 自动配置", detail=str(auto_config)))
    return hits


def _read_registry_value(key: object, name: str, winreg: object) -> object:
    """读注册表值；不存在时返回 ``None``。"""
    try:
        return winreg.QueryValueEx(key, name)[0]  # ty: ignore[unresolved-attribute]
    except OSError:
        return None


def _macos_system_proxy() -> list[ProxyHit]:
    """解析 ``scutil --proxy`` 的输出。"""
    import subprocess  # noqa: PLC0415 - 仅 macOS 分支需要

    try:
        completed = subprocess.run(
            ["/usr/sbin/scutil", "--proxy"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    enabled_fields = [
        stripped.split(":", 1)[0].strip()
        for stripped in (line.strip() for line in completed.stdout.splitlines())
        if stripped.endswith(": 1")
    ]
    return [
        ProxyHit(source="系统代理", detail=field.removesuffix("Enable"))
        for field in enabled_fields
        if field.endswith("Enable")
    ]
