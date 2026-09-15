"""内置检查项.

导入本包即完成全部检查项的注册（``@checker`` 装饰器在类定义时执行）。
C# 原版靠反射扫描程序集，这里换成显式导入，依赖关系一眼可见，
打包 / 冻结（PyInstaller 等）时也不会因为"看不到引用"而漏掉模块。
"""

from __future__ import annotations

from hdu_netdebugger.checkers.auth import AuthCheck
from hdu_netdebugger.checkers.bas import BASCheck
from hdu_netdebugger.checkers.base import Checker, DetailsLog
from hdu_netdebugger.checkers.dns import DNSCheck
from hdu_netdebugger.checkers.get_info import GetInfoCheck
from hdu_netdebugger.checkers.isp import ISPCheck
from hdu_netdebugger.checkers.proxy import ProxyCheck
from hdu_netdebugger.checkers.public_net import PublicNetCheck
from hdu_netdebugger.checkers.srun_reachability import SrunReachabilityCheck

__all__ = [
    "AuthCheck",
    "BASCheck",
    "Checker",
    "DNSCheck",
    "DetailsLog",
    "GetInfoCheck",
    "ISPCheck",
    "ProxyCheck",
    "PublicNetCheck",
    "SrunReachabilityCheck",
    "load_builtin_checkers",
]


def load_builtin_checkers() -> None:
    """确保内置检查项都已注册。

    正常情况下导入本包就够了；显式提供这个函数是为了让调用方（CLI / GUI / 测试）
    表达"我需要全部内置检查项"的意图，也方便未来接入 entry-point 扩展。
    """
