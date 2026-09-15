# Threading + QObject Cleanup

Qt threading is full of silent corruption traps. Every rule here comes from a production crash.

## Rule 9: NEVER emit Qt signals from ThreadPoolExecutor workers

PySide6 signals must be emitted from the thread that owns the QObject. ThreadPoolExecutor callbacks run on pool threads that Qt doesn't manage — emitting signals from them causes crashes or silent corruption.

```python
# WRONG — crashes or corrupts Qt state
def _firmware_phase(self):
    with ThreadPoolExecutor(max_workers=8) as pool:
        for device in devices:
            pool.submit(self._upload, device,
                on_done=lambda r: self.progress.emit(r))  # pool thread!

# RIGHT — drain futures on QThread, emit from there
def _firmware_phase(self):
    futures = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, device in enumerate(devices):
            future = pool.submit(self._upload, device)
            futures[future] = i

        for future in as_completed(futures):
            idx = futures[future]
            result = future.result()        # back on QThread
            self._on_progress(idx, result)  # safe to emit signals
```

## Rule 10: Each parallel worker MUST create its own resources

`requests.Session` is NOT thread-safe for concurrent use. Each worker needs its own HTTP client, closed in a finally block.

```python
def _deploy_one(self, index, device, item):
    client = ApiClient(host=device.ip_address)
    try:
        if self._credentials:
            client.set_credentials(*self._credentials)
        return result
    finally:
        client.close()
```

## Rule 11: QTimer for periodic background checks

Use QTimer for connection monitoring. Store as instance variable to prevent garbage collection.

```python
def _start_connection_monitor(self):
    self._conn_failures = 0
    if not self._conn_timer:
        self._conn_timer = QTimer()
        self._conn_timer.timeout.connect(self._check_connection)
    self._conn_timer.start(10000)  # 10s interval
```

Production settings: 10s interval, 5s HTTP timeout, 3 consecutive failures before "lost".

## Rule 28: Use `deleteLater()` not `del` for QObject cleanup

When removing dynamic widgets at runtime (clearing tabs, removing rows), use `widget.deleteLater()` instead of `del widget`. Python's `del` only decrements the reference count; Qt needs the event loop to safely destroy the QObject.

```python
# WRONG — leaves stale pointers, possible crash
widget = layout.takeAt(0).widget()
del widget

# RIGHT — safe deletion through Qt event loop
widget = layout.takeAt(0).widget()
if widget:
    widget.deleteLater()
```

If another reference exists (signal connection, parent's child list), `del` leaves the widget in memory with stale pointers. `deleteLater()` schedules deletion when the event loop next runs.

## Rule 29: Disconnect signals before deleting QObjects

A QObject with ANY connected signal is NOT garbage-collected by Python's GC — Qt keeps an internal reference in the signal table. This silently causes memory leaks in dynamic UIs.

```python
# WRONG — widget stays in memory forever
widget.some_signal.connect(self._handler)
widget.deleteLater()  # signal still connected = never GC'd

# RIGHT — disconnect first
widget.some_signal.disconnect(self._handler)
widget.deleteLater()
```

Matters most in pages that dynamically create/destroy widgets (config sections, table rows, tab pages).

## Rule 30: QTableWidget performance cliff at ~100k rows

`QTableWidget` creates a `QTableWidgetItem` for every cell in memory. At 100k rows × 6 columns = 600k items = 200MB+ RAM, and `selectAll()` hangs the UI.

For the worklist table (~500 devices max), QTableWidget is fine. For anything larger, use `QTableView` with a custom `QAbstractTableModel` and lazy loading via `canFetchMore()`/`fetchMore()`.

## Rule 31: NEVER emit signals from ThreadPoolExecutor — explicit anti-pattern

Strengthens Rule 9 with the explicit "do NOT" pattern developers commonly try:

```python
# CATASTROPHICALLY WRONG — will crash or corrupt Qt state
def _process(self, device):
    """Runs on ThreadPoolExecutor pool thread"""
    result = do_work(device)
    self.progress.emit(result)  # CRASH — pool thread is not a Qt thread

# ALSO WRONG — lambda doesn't change the thread
pool.submit(self._process, device,
    callback=lambda r: self.progress.emit(r))  # still pool thread!

# THE ONLY CORRECT PATTERN:
for future in as_completed(futures):
    result = future.result()   # back on QThread
    self.progress.emit(result) # SAFE
```

## Rule 32: `connect()` is thread-safe but `emit()` is NOT

`QObject.connect()` can be called from any thread safely. However, `emit()` must ONLY be called from the thread that owns the QObject (where it was created, or where it was moved with `moveToThread()`).

Common misconception: "if connect() is thread-safe, I can emit from anywhere." Wrong.

## Rule 33: `BlockingQueuedConnection` causes deadlock

Never use `Qt.ConnectionType.BlockingQueuedConnection` between threads in the same application. If Thread A blocks waiting for Thread B to process a slot, and Thread B waits for Thread A, instant deadlock. Use `Qt.ConnectionType.QueuedConnection` (non-blocking) instead.

```python
# WRONG — potential deadlock
signal.connect(slot, Qt.ConnectionType.BlockingQueuedConnection)

# RIGHT — non-blocking, safe
signal.connect(slot, Qt.ConnectionType.QueuedConnection)
# Or just use the default (auto-detected):
signal.connect(slot)
```

## Rule 47: AutoConnection captures thread affinity at `connect()` time, not `emit()` time

`Qt.AutoConnection` is often described as "picks Direct or Queued based on the thread relationship" — but crucially, that decision is made **when you call `connect()`**, not when you emit. If you create a worker in the main thread, `connect()` its signals, and THEN `moveToThread()` it, the connections still use Direct dispatch. Slots execute in the wrong thread silently.

```python
# WRONG — connection type captured while worker is still in main thread
worker = ConfigWorker()
worker.progress.connect(self.on_progress)    # AutoConnection → Direct
worker.moveToThread(self.thread)             # too late; still Direct
self.thread.start()                          # on_progress runs on wrong thread

# RIGHT — move first, connect second
worker = ConfigWorker()
worker.moveToThread(self.thread)
worker.progress.connect(self.on_progress)    # AutoConnection → Queued correctly
self.thread.start()
```

Or force `Qt.ConnectionType.QueuedConnection` explicitly — immune to the ordering bug.

## Rule 48: `QThreadPool.start(fn)` accepts bare callables since PySide6 6.2

Most tutorials still show QRunnable subclassing:

```python
class Worker(QRunnable):          # unnecessary for simple cases
    def run(self):
        expensive_http_call()

pool.start(Worker())
```

Since PySide6 6.2, `QThreadPool.start()` accepts any Python callable directly:

```python
pool.start(expensive_http_call)
pool.start(lambda: upload(device_id))
pool.start(self._config_one, priority=QThread.HighPriority)
```

Reserve QRunnable subclassing for workers that need internal state, signal emission, or cancellation callbacks.

## Rule 49: `Future.cancel()` only stops tasks that haven't started — use `threading.Event`

`concurrent.futures.Future.cancel()` returns `False` if the task is already running. For "Cancel" mid-deploy, use **cooperative cancellation**: workers poll a shared `threading.Event`.

```python
self._cancel_flag = threading.Event()

def _upload_firmware(self, device_id, chunks):
    for chunk in chunks:
        if self._cancel_flag.is_set():
            return                                    # cooperative bail-out
        self._send_chunk(device_id, chunk)

def on_cancel_clicked(self):
    self._cancel_flag.set()

def closeEvent(self, event):
    self._cancel_flag.set()
    self._executor.shutdown(wait=True)                # block until threads exit
    super().closeEvent(event)
```

Without the explicit flag + `shutdown(wait=True)` in `closeEvent`, pool threads can outlive the window and prevent Python from exiting.

## Rule 38: PySide6 > 6.7.2 breaks UniqueConnection on free functions

`Qt.ConnectionType.UniqueConnection` is forbidden on free-standing functions in PySide6 > 6.7.2 (only allowed on QObject methods). Will raise `RuntimeError` at runtime. Fix: use QObject slot methods, or remove the UniqueConnection flag.
