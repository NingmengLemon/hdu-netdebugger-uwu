"""后台线程与 Qt 入口。

线程是 GUI 最容易出错的部分，这里既验证信号内容，也真起一个 ``QThread`` 走完整链路。
缺 PySide6 时在收集期直接报错（不静默跳过）。
"""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PySide6  # noqa: F401 - 只为探测依赖是否就绪
except ImportError as exc:  # pragma: no cover - 环境不完整时才会触发
    raise RuntimeError(
        "GUI 测试需要 PySide6；请执行 uv sync（test 依赖组已包含它）或 uv sync --extra gui。"
        "若已安装却导入失败，多半是缺系统库（Linux 上的 libGL / libEGL / xkbcommon）"
    ) from exc

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QMainWindow

from hdu_netdebugger.checkers import load_builtin_checkers
from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.gui import app as app_module
from hdu_netdebugger.gui import launch
from hdu_netdebugger.gui import worker as worker_module
from hdu_netdebugger.gui.style import apply_theme
from hdu_netdebugger.gui.worker import CheckWorker
from hdu_netdebugger.models.enums import CheckStatus
from hdu_netdebugger.models.result import CheckOutcome
from hdu_netdebugger.registry import get_checker
from hdu_netdebugger.runner import ProgressEvent, RunResult

if TYPE_CHECKING:
    from collections.abc import Iterator

SAMPLE = CheckOutcome(
    name="检查DNS可用性",
    order=20,
    status=CheckStatus.PASSED,
    summary="DNS 正常",
    details="DNS 查询成功",
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    load_builtin_checkers()
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(["pytest-gui"])
    apply_theme(app)
    return app


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把 worker 内部调用的 run_checks 换成立即返回的替身。"""
    captured: dict[str, Any] = {"thread_name": None}

    async def fake_run_checks(**kwargs: Any) -> RunResult:
        captured.update(kwargs)
        captured["thread_name"] = threading.current_thread().name
        on_progress = kwargs.get("on_progress")
        if callable(on_progress):
            on_progress(
                ProgressEvent(
                    spec=get_checker("检查DNS可用性"),
                    index=0,
                    total=1,
                    outcome=SAMPLE,
                )
            )
        should_stop = kwargs.get("should_stop")
        captured["stopped"] = bool(should_stop()) if callable(should_stop) else None
        return RunResult(outcomes=[SAMPLE], duration_s=0.1)

    monkeypatch.setattr(worker_module, "run_checks", fake_run_checks)
    return captured


def pump_events(app: QApplication, thread: QThread, timeout_s: float = 10.0) -> bool:
    """在等待线程结束的同时持续处理事件（排队信号需要事件循环投递）。"""
    deadline = time.monotonic() + timeout_s
    while thread.isRunning() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return not thread.isRunning()


class TestCheckWorker:
    def test_run_emits_progress_and_finished(self, fake_runner: dict[str, Any]) -> None:
        worker = CheckWorker(config=RunConfig(ping_count=1))
        progress: list[ProgressEvent] = []
        finished: list[RunResult] = []
        failed: list[str] = []
        worker.progress.connect(progress.append)
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        worker.run()

        assert failed == []
        assert [event.outcome.name for event in progress] == ["检查DNS可用性"]
        assert len(finished) == 1
        assert finished[0].outcomes[0].status is CheckStatus.PASSED

    def test_config_and_filters_are_forwarded(self, fake_runner: dict[str, Any]) -> None:
        config = RunConfig(ping_count=7)
        worker = CheckWorker(config=config, only=["DNS"], skip=["公网"], verbose=True)
        worker.run()
        assert fake_runner["config"] is config
        assert fake_runner["only"] == ["DNS"]
        assert fake_runner["skip"] == ["公网"]
        assert fake_runner["verbose"] is True

    def test_none_filters_are_normalised_to_none(self, fake_runner: dict[str, Any]) -> None:
        CheckWorker(config=RunConfig()).run()
        assert fake_runner["only"] is None
        assert fake_runner["skip"] is None

    def test_cancel_sets_stop_flag(self, fake_runner: dict[str, Any]) -> None:
        worker = CheckWorker(config=RunConfig())
        assert worker.cancelled is False
        worker.cancel()
        assert worker.cancelled is True
        worker.run()
        assert fake_runner["stopped"] is True

    def test_exception_becomes_failed_signal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def boom(**_kwargs: Any) -> RunResult:
            raise RuntimeError("模拟崩溃")

        monkeypatch.setattr(worker_module, "run_checks", boom)
        worker = CheckWorker(config=RunConfig())
        failed: list[str] = []
        finished: list[RunResult] = []
        worker.failed.connect(failed.append)
        worker.finished.connect(finished.append)

        worker.run()

        assert finished == []
        assert len(failed) == 1
        assert "模拟崩溃" in failed[0]


class TestWorkerInThread:
    def test_full_thread_roundtrip(
        self,
        qapp: QApplication,
        fake_runner: dict[str, Any],
    ) -> None:
        """真起一个 QThread：moveToThread 之后连接，验证跨线程投递。"""
        worker = CheckWorker(config=RunConfig(ping_count=1))
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        progress: list[ProgressEvent] = []
        finished: list[RunResult] = []
        worker.progress.connect(progress.append)
        worker.finished.connect(finished.append)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        thread.start()
        assert pump_events(qapp, thread), "后台线程未在超时前结束"

        assert len(finished) == 1
        assert [event.outcome.name for event in progress] == ["检查DNS可用性"]
        # 证明工作确实发生在别的线程里
        assert fake_runner["thread_name"] != threading.current_thread().name

    def test_cancel_before_start(
        self,
        qapp: QApplication,
        fake_runner: dict[str, Any],
    ) -> None:
        worker = CheckWorker(config=RunConfig())
        worker.cancel()
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        thread.finished.connect(thread.deleteLater)
        worker.finished.connect(thread.quit)

        thread.start()
        assert pump_events(qapp, thread)
        assert fake_runner["stopped"] is True


class TestGuiEntryPoint:
    def test_run_app_executes_event_loop(
        self,
        qapp: QApplication,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        shown: list[bool] = []
        monkeypatch.setattr(app_module, "_version", lambda: "0.0.0-test")
        monkeypatch.setattr(QMainWindow, "show", lambda self: shown.append(True))
        monkeypatch.setattr(QApplication, "exec", lambda self: 7)

        assert app_module.run_app(verbose=False) == 7
        assert shown == [True]

    def test_launch_delegates_to_run_app(
        self,
        qapp: QApplication,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: dict[str, Any] = {}

        def fake_run_app(*, verbose: bool = False, argv: Any = None) -> int:
            seen["verbose"] = verbose
            seen["argv"] = argv
            return 0

        monkeypatch.setattr(app_module, "run_app", fake_run_app)
        assert launch(verbose=True) == 0
        assert seen == {"verbose": True, "argv": None}


@pytest.fixture(autouse=True)
def _cleanup_threads() -> Iterator[None]:
    yield
    for thread in QThread().findChildren(QThread):  # pragma: no cover - 兜底
        del thread
