"""一次检查运行所共享的运行时资源.

把「配置」「跨检查项状态」「HTTP 连接池」收在一个对象里交给检查项，
避免每个检查项各建一个连接池，也避免用全局变量传资源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.context import CheckContext
from hdu_netdebugger.net.http import HttpClient
from hdu_netdebugger.net.ping import Backend, PingOptions, ping_multiple

if TYPE_CHECKING:
    from hdu_netdebugger.models.ping import MultiPingResult

__all__ = ["RunSession"]


@dataclass(slots=True)
class RunSession:
    """检查项运行上下文。"""

    config: RunConfig = field(default_factory=RunConfig)
    context: CheckContext = field(default_factory=CheckContext)
    #: 是否输出详细过程（CLI ``-v``）
    verbose: bool = False
    #: 强制使用的 Ping 后端
    ping_backend: Backend = Backend.AUTO
    _http: HttpClient | None = field(default=None, repr=False)

    @property
    def http(self) -> HttpClient:
        """共享的 HTTP 客户端（惰性创建）。"""
        if self._http is None:
            self._http = HttpClient(
                timeout_s=self.config.http_timeout_s,
                verify_tls=self.config.verify_tls,
                trust_env_proxy=self.config.trust_env_proxy,
            )
        return self._http

    @property
    def ping_options(self) -> PingOptions:
        """由配置派生的 Ping 参数。"""
        return PingOptions(
            count=self.config.ping_count,
            timeout_s=self.config.ping_timeout_s,
            payload_size=self.config.ping_payload_size,
            ttl=self.config.ping_ttl,
            interval_s=self.config.ping_interval_s,
        )

    async def ping(self, target: object) -> MultiPingResult:
        """按当前配置 Ping 一个目标。"""
        return await ping_multiple(
            target,  # ty: ignore[invalid-argument-type]
            self.ping_options,
            backend=self.ping_backend,
        )

    async def aclose(self) -> None:
        """释放连接池。"""
        if self._http is not None:
            await self._http.aclose()
            self._http = None
