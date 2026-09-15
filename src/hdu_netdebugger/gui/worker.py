"""后台检查线程.

线程模型遵循 Qt 的硬性约束：

* ``QObject`` 只能被它归属的线程直接访问。因此 worker 先 ``moveToThread``，
  **之后**才连接信号 —— ``AutoConnection`` 的连接类型是在 ``connect()`` 那一刻
  根据双方线程亲和性决定的，先连接再搬家会退化成 Direct 调用，槽函数跑错线程。
* 信号只在 worker 自己的线程里 ``emit``。``on_progress`` 由 ``run_checks`` 在
  worker 线程内回调，所以在这里 emit 是安全的；跨线程投递交给 Qt 的队列连接完成。
* 取消是**协作式**的：``run_checks`` 每个检查项开始前询问一次 ``should_stop``，
  不会硬杀线程。
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot

from hdu_netdebugger.runner import run_checks

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hdu_netdebugger.config import RunConfig
    from hdu_netdebugger.net.ping import Backend
    from hdu_netdebugger.runner import ProgressEvent

__all__ = ["CheckWorker"]


class CheckWorker(QObject):
    """在独立 ``QThread`` 里跑完一轮检查。

    Signals:
        progress: 每完成一个检查项发出一次，携带
            :class:`~hdu_netdebugger.runner.ProgressEvent`。
        finished: 全部完成，携带 :class:`~hdu_netdebugger.runner.RunResult`。
        failed: 运行期抛出未预期异常，携带错误描述。
    """

    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        config: RunConfig,
        only: Sequence[str] | None = None,
        skip: Sequence[str] | None = None,
        verbose: bool = False,
        ping_backend: Backend | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._only = list(only) if only else None
        self._skip = list(skip) if skip else None
        self._verbose = verbose
        self._ping_backend = ping_backend
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """请求取消：当前检查项跑完后不再调度后续项。"""
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        """是否已请求取消。"""
        return self._cancelled.is_set()

    @Slot()
    def run(self) -> None:
        """线程入口：驱动 asyncio 事件循环。"""
        try:
            result = asyncio.run(
                run_checks(
                    config=self._config,
                    only=self._only,
                    skip=self._skip,
                    verbose=self._verbose,
                    ping_backend=self._ping_backend,
                    on_progress=self._emit_progress,
                    should_stop=self._cancelled.is_set,
                )
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(result)

    def _emit_progress(self, event: ProgressEvent) -> None:
        """由 ``run_checks`` 在 worker 线程内调用。"""
        self.progress.emit(event)
