---
name: pyside6-desktop
description: PySide6/PyQt6 desktop app rules — QSS styling bugs, QThread safety, PyInstaller packaging, Windows code signing, AV false-positive mitigation. Use when editing Qt code, stylesheets, PyInstaller .spec files, or desktop build/sign workflows.
filePattern:
  - "**/*.qss"
  - "**/*.ui"
  - "**/*.spec"
  - "**/BUILD.bat"
  - "**/build*.bat"
  - "**/pages/*.py"
  - "**/widgets/*.py"
  - "**/windows/*.py"
  - "**/dialogs/*.py"
  - "**/main_window*.py"
  - "**/app.py"
  - "**/qt_app*.py"
bashPattern:
  - "pyinstaller"
  - "pyside6"
  - "PySide6"
  - "signtool"
  - "windeployqt"
user-invocable: true
allowed-tools:
  - Read
  - Edit
  - Write
  - Bash
  - Grep
  - Glob
---

# PySide6 Desktop — Production Rules

Battle-tested rules from GUDE Deploy, a PySide6 desktop app deployed to 120+ devices. Every rule exists because we hit the bug in production.

This SKILL.md keeps the **high-frequency critical rules** inline. Deeper content is split into reference files — **read only the ones you need**:

| For work on… | Read this |
|---|---|
| QSS stylesheets, "invisible text", QLabel/QFrame/QPushButton styling | `references/qss.md` |
| setVisible(), widget state, page reset | `references/widgets.md` |
| QThread, ThreadPoolExecutor, signals/slots, QObject cleanup, lambdas, `moveToThread` | `references/threading-and-cleanup.md` |
| PyInstaller .spec files, hiddenimports, UTF-8, UPX, `__init__.py` bug | `references/pyinstaller.md` |
| Windows-specific: netifaces leak, ipconfig parsing, DPI scaling, ARM64 | `references/platform-gotchas.md` |
| Code signing, signtool, SmartScreen, Azure Trusted Signing, AV false positives, Inno Setup installer | `references/signing-and-av.md` |
| Wizard page flow, Pydantic v2, force_field, subnet scanning policy | `references/architecture.md` |

Pairs with:
- `network-device-discovery` — HTTP/UDP discovery, parallel probing, vendor protocols
- `mass-deploy-ux` — UX patterns for tracking 50+ parallel device operations
- `windows-release-pipeline` — GitHub Actions CI/CD for the signing pipeline

---

## Critical rules (inline — these are the ones that bite first)

### Rule 1: Every inline `setStyleSheet` MUST include `color:`

When you call `setStyleSheet()` on a widget, Qt overrides the global QSS for that widget. On Windows dark mode, the inherited color can become white — invisible on a white background.

```python
# WRONG — text invisible on Windows dark mode
title.setStyleSheet("font-size: 20px; font-weight: bold;")

# RIGHT — explicit color always visible
title.setStyleSheet("font-size: 20px; font-weight: bold; color: #202124;")
```

If you set font-size, font-weight, font-family, or padding inline, you MUST also set color. **This is the #1 most-violated rule** — see `references/qss.md` for the deeper "style firewall" mechanism and the other 6 QSS rules.

### Rule 9: NEVER emit Qt signals from ThreadPoolExecutor workers

Signals must be emitted from the thread that owns the QObject. Emitting from a pool thread silently corrupts Qt state or crashes.

```python
# WRONG — crashes or corrupts Qt state
def _upload(self, device):
    result = do_work(device)
    self.progress.emit(result)  # CRASH — pool thread is not a Qt thread

# RIGHT — drain futures on the QThread, emit from there
for future in as_completed(futures):
    result = future.result()       # back on QThread
    self.progress.emit(result)     # safe
```

**Also wrong:** `pool.submit(..., callback=lambda r: self.sig.emit(r))` — the lambda still runs on the pool thread. See `references/threading-and-cleanup.md` for the full threading rule set (cancellation, `moveToThread` ordering, `deleteLater`, lambda GC leaks).

### Rule 10: Each parallel worker MUST create its own HTTP client

`requests.Session` is not thread-safe. Share = race. Always create + close in the worker.

```python
def _deploy_one(self, device, item):
    client = ApiClient(host=device.ip_address)
    try:
        client.configure(item)
    finally:
        client.close()     # always close, even on exception
```

### Rule 28: Use `deleteLater()` not `del` for QObject cleanup

Python's `del` decrements refcount; Qt needs the event loop to safely destroy the QObject. `del` leaves stale pointers that crash later.

```python
# WRONG
widget = layout.takeAt(0).widget()
del widget

# RIGHT
widget = layout.takeAt(0).widget()
if widget:
    widget.deleteLater()
```

**Also:** a QObject with any connected signal is pinned in memory by Qt's signal table — Python GC can't collect it. Disconnect signals before `deleteLater()` when cleaning up dynamic widgets. See `references/threading-and-cleanup.md`.

### Rule 39: Sign Windows .exe with SHA-256 + RFC 3161 timestamp

Unsigned executables trigger SmartScreen and Defender. Signed-but-not-timestamped executables expire with the cert. Both problems are avoided by signing properly:

```bat
signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 dist\MyApp\MyApp.exe
signtool verify /pa /v dist\MyApp\MyApp.exe
```

For all signing decisions (OV vs EV vs Azure Trusted Signing, SmartScreen reputation, YubiKey/HSM requirements post-CA/B Forum June 2023), see `references/signing-and-av.md`.

### Rule 43: Don't run PyInstaller from a directory containing `__init__.py`

PyInstaller 5.4+ on Windows silently corrupts Qt6 DLL path resolution when invoked from a directory with `__init__.py`. Frozen app crashes at runtime:

```
ImportError: DLL load failed while importing QtWidgets
```

Keep PyInstaller invocation at the **project root**, put sources under `src/`. Full PyInstaller rule set (hiddenimports, UTF-8 mode, UPX, spec patterns) in `references/pyinstaller.md`.

### Rule 16: PySide6 wheels don't exist for ARM64 Linux

You cannot build the app on a DGX Spark / Raspberry Pi / other ARM64 Linux box. Use `python -m py_compile` for syntax checking only; the real build must happen on Windows. PyInstaller does not cross-compile.

---

## Version sync (mandatory before tagging a release)

Three files must agree on the version string:

```python
# src/gude_deploy/__init__.py
__version__ = "0.6.0"

# pyproject.toml
version = "0.6.0"

# BUILD.bat
echo  v0.6.0
```

Bumping one without the others means the zip contains one version and the running app reports another.

---

## When in doubt, read a reference file

The inline rules above cover ~80% of day-to-day work. For anything else — QSS edge cases, deep threading, PyInstaller spec tuning, signing strategy, AV mitigation tiers, wizard architecture — invoke this skill and then `Read` the specific reference file. Don't try to hold all 52 rules in your head.
