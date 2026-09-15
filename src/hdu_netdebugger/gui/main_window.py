"""主窗口.

布局对应原版 ``Views/MainView.axaml``：顶部标题 + 「开始测试」「导出结果」两个按钮，
下方是可滚动的检查项卡片列表。

线程约定见 :mod:`hdu_netdebugger.gui.worker`：窗口只负责收发信号，
所有网络工作都发生在 ``CheckWorker`` 所属的 ``QThread`` 里。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from hdu_netdebugger import __version__
from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.gui.widgets.check_card import CheckCard
from hdu_netdebugger.gui.worker import CheckWorker
from hdu_netdebugger.registry import all_checkers
from hdu_netdebugger.report import build_json, build_markdown, summarize

if TYPE_CHECKING:
    from datetime import datetime

    from PySide6.QtGui import QCloseEvent

    from hdu_netdebugger.runner import ProgressEvent, RunResult

__all__ = ["MainWindow"]


class MainWindow(QMainWindow):
    """HDU Net Debugger 主窗口。"""

    def __init__(self, *, verbose: bool = False) -> None:
        super().__init__()
        self.setWindowTitle(f"HDU Net Debugger v{__version__}")
        self.resize(920, 720)
        self.setMinimumSize(600, 420)

        self._verbose = verbose
        self._result: RunResult | None = None
        self._thread: QThread | None = None
        self._worker: CheckWorker | None = None
        #: 已请求关闭、正在等后台线程收尾
        self._closing = False

        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_scroll_area(), 1)

        self.statusBar().showMessage("就绪")

    # -- 构建 -------------------------------------------------------------

    def _build_header(self) -> QWidget:
        bar = QFrame(self)
        bar.setObjectName("HeaderBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("HDU Net Debugger", bar)
        title.setObjectName("AppTitle")
        subtitle = QLabel("杭州电子科技大学校园网诊断工具", bar)
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        layout.addLayout(titles)
        layout.addStretch(1)

        self._summary = QLabel("", bar)
        self._summary.setObjectName("SummaryLabel")
        layout.addWidget(self._summary)

        self._run_button = QPushButton("开始测试", bar)
        self._run_button.clicked.connect(self.start_checks)
        layout.addWidget(self._run_button)

        self._export_button = QPushButton("导出结果", bar)
        self._export_button.setObjectName("SecondaryButton")
        self._export_button.setEnabled(False)
        self._export_button.clicked.connect(self.export_results)
        layout.addWidget(self._export_button)

        return bar

    def _build_scroll_area(self) -> QScrollArea:
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget(area)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._cards: list[CheckCard] = []
        for spec in all_checkers():
            card = CheckCard(spec, container)
            layout.addWidget(card)
            self._cards.append(card)
        layout.addStretch(1)

        area.setWidget(container)
        return area

    # -- 运行 -------------------------------------------------------------

    def start_checks(self) -> None:
        """启动一轮检查（按钮回调）。"""
        if self._thread is not None:
            return

        self._result = None
        self._export_button.setEnabled(False)
        self._run_button.setEnabled(False)
        self._summary.setText("正在检查…")
        self.statusBar().showMessage("正在检查…")
        for card in self._cards:
            card.reset()
            card.mark_running()
        self._cards[0].set_expanded(True)

        worker = CheckWorker(
            config=RunConfig(),
            verbose=self._verbose,
        )
        thread = QThread(self)
        worker.moveToThread(thread)  # 必须先搬家再连接信号
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)

        self._worker = worker
        self._thread = thread
        thread.start()

    def _on_progress(self, event: ProgressEvent) -> None:
        """单个检查项完成（运行在 GUI 线程）。"""
        card = self._card_for(event.spec.name)
        if card is not None:
            card.apply(event.outcome)
        self.statusBar().showMessage(f"[{event.index + 1}/{event.total}] {event.spec.name} 完成")

    def _card_for(self, name: str) -> CheckCard | None:
        """按检查项名称取对应卡片。"""
        for card in self._cards:
            if card.spec_name == name:
                return card
        return None

    def _on_finished(self, result: RunResult) -> None:
        """全部完成。"""
        self._result = result
        stats = summarize(result.outcomes)
        self._summary.setText(stats.headline)
        self.statusBar().showMessage(f"检查完成，总耗时 {result.duration_s:.2f}s")
        self._export_button.setEnabled(bool(result.outcomes))

    def _on_failed(self, message: str) -> None:
        """运行期异常。"""
        self.statusBar().showMessage("检查失败")
        self._summary.setText("检查失败")
        QMessageBox.critical(self, "检查失败", f"检查过程中发生错误：\n\n{message}")

    def _on_thread_finished(self) -> None:
        """线程退出后清理引用，允许再次运行（若正在关窗则把窗口关掉）。"""
        if self._thread is not None:
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None
        self._run_button.setEnabled(True)
        if self._closing:
            self.close()

    @property
    def is_running(self) -> bool:
        """是否正在检查。"""
        return self._thread is not None

    # -- 导出 -------------------------------------------------------------

    def export_results(self) -> None:
        """把当前结果导出为 Markdown 或 JSON。"""
        if self._result is None or not self._result.outcomes:
            QMessageBox.information(self, "导出结果", "请先执行一次检查。")
            return

        path_text, selected = QFileDialog.getSaveFileName(
            self,
            "检查结果导出",
            "ResultExport.md",
            "Markdown 报告 (*.md);;JSON 数据 (*.json)",
        )
        if not path_text:
            return

        target = Path(path_text)
        as_json = "json" in selected.lower() or target.suffix.lower() == ".json"
        if not target.suffix:
            target = target.with_suffix(".json" if as_json else ".md")

        checked_at: datetime = self._result.started_at
        payload = (
            build_json(self._result.outcomes, checked_at=checked_at)
            if as_json
            else build_markdown(self._result.outcomes, checked_at=checked_at)
        )
        try:
            target.write_text(payload, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", f"无法写入 {target}：\n\n{exc}")
            return

        self.statusBar().showMessage(f"已导出到 {target}")
        QMessageBox.information(self, "导出结果", f"已导出到：\n{target}")

    # -- 生命周期 ---------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭窗口前先协作式取消，等后台线程真的退出再关。

        以前是 ``thread.wait(15000)`` 超时后 ``terminate()``：既会把消息泵饿死
        十几秒（Windows 上直接显示"无响应"），又可能 hard-kill 一个正在跑
        ``asyncio`` 的线程 —— 它持有 GIL，于是窗口没了进程还留着，正是这段代码
        想避免的结果。现在不阻塞：请求取消 → 忽略这次关闭 →
        线程真正结束后由 :meth:`_on_thread_finished` 再关一次。

        等待是有界的：``run_checks`` 只会在检查项之间检查取消标志，而每个检查项
        的网络调用都带超时（最坏约十几秒），所以不会无限等下去。
        """
        if self._thread is not None and self._thread.isRunning():
            if not self._closing:
                self._closing = True
                if self._worker is not None:
                    self._worker.cancel()
                self.statusBar().showMessage("正在取消当前检查…")
                self._run_button.setEnabled(False)
                self._export_button.setEnabled(False)
            event.ignore()
            return
        super().closeEvent(event)
