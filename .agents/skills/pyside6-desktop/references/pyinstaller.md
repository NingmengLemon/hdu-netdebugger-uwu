# PyInstaller Packaging

Everything about bundling PySide6 apps into a Windows .exe.

## Rule 12: `collect_submodules()` for auto-discovery

PyInstaller can't trace all imports statically. This catches dynamic and conditional imports:

```python
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ...
    hiddenimports=[
        # explicit problematic packages
    ] + collect_submodules("your_package"),
)
```

## Rule 13: Explicit hidden imports for problematic packages

```python
hiddenimports=[
    # Pydantic v2 Rust extension — PyInstaller cannot trace automatically
    "pydantic",
    "pydantic_core",
    "pydantic_core._pydantic_core",
    "pydantic.json_schema",
    # netifaces: pip package is netifaces2, but import name is netifaces
    "netifaces",
]
```

## Rule 14: Data files for bundled assets

```python
datas=[
    (str(project_root / "assets" / "firmware"), "firmware"),
]
```

At runtime, resolve with `sys._MEIPASS` for frozen apps:

```python
def _get_firmware_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "firmware"
    return Path(__file__).parent.parent.parent.parent / "assets" / "firmware"
```

## Rule 15: Version sync across files

`__init__.py`, `pyproject.toml`, and `BUILD.bat` must all have the same version. Check all three when bumping:

```python
# __init__.py
__version__ = "0.5.0"

# pyproject.toml
version = "0.5.0"

# BUILD.bat
echo  v0.5.0
```

## Rule 37: PySide6 6.7.0+ requires additional hidden imports

PySide6 6.7.0+ added modules that PyInstaller can't auto-detect:

```python
hiddenimports=[
    "pydantic_core._pydantic_core",  # Pydantic v2 Rust extension
    "PySide6.QtOpenGL",              # Required by Qt3DRender in 6.7.0+
    "PySide6.QtGraphsWidgets",       # New in 6.8.0
]
```

Check PyInstaller release notes when upgrading PySide6 — new hooks are added regularly.

## Rule 43: Qt6 DLL load fails silently if `__init__.py` sits in the PyInstaller run directory (5.4+)

PyInstaller 5.4+ (and all 6.x) has a Windows-only regression: if the directory from which you invoke PyInstaller contains an `__init__.py`, the frozen app fails at runtime with:

```
ImportError: DLL load failed while importing QtWidgets: The specified module could not be found.
```

The DLLs are present — the module path resolution is corrupted. Only affects PySide6/PyQt6 on Windows; Linux/macOS unaffected.

**Fix:** keep PyInstaller invocation out of package roots. Put sources under `src/` and run PyInstaller from the project root:

```
gude-deploy/
├── BUILD.bat               ← run pyinstaller from here
├── gude-deploy.spec
└── src/
    └── gude_deploy/
        ├── __init__.py     ← fine, not in run directory
        └── app.py
```

Tracked as [PyInstaller #7155](https://github.com/pyinstaller/pyinstaller/issues/7155), fix merged in PR #7181 — but verify your version has it.

## Rule 44: `PYTHONUTF8` env var is ignored in PyInstaller 6.0+ — set it in the spec instead

PyInstaller 6.0 broke `PYTHONUTF8=1` environment-variable support for frozen apps. Users setting the env var on their system get no effect. For apps handling non-ASCII device names, paths, or CSV content (GUDE PDU hostnames can contain umlauts, etc.), set it inside the spec:

```python
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GudeDeploy",
    runtime_options=['utf8_mode=1'],   # ← mandatory for non-ASCII text
    ...
)
```

Or via CLI: `--runtime-option=utf8_mode=1`. Without this, non-ASCII filenames/paths may cause `UnicodeEncodeError` at random points.

Source: [PyInstaller 6.0.0 Changelog](https://pyinstaller.org/en/v6.0.0/CHANGES.html).

## Rule 45: `requests` 2.28+ needs `charset_normalizer` in hiddenimports (NOT `chardet`)

Modern `requests` (2.28+) replaced `chardet` with `charset_normalizer`. Stale advice on Stack Overflow still says "add chardet to hiddenimports" — that's wrong now. Missing `charset_normalizer` causes silent encoding failures or `ContentDecodingError` on HTTP responses.

Current known-good set for requests in a frozen app:

```python
hiddenimports=[
    'urllib3',
    'charset_normalizer',      # ← replaces chardet
    'idna',
    'requests',
]
```

Source: [psf/requests #6331](https://github.com/psf/requests/issues/6331).

## Rule 46: Don't broad-exclude DLLs from UPX — PyInstaller 6.x already handles Qt and CFG DLLs

PyInstaller 6.x auto-excludes Qt5/Qt6 plugins and Control Flow Guard–enabled DLLs from UPX compression (UPX corrupts both). Broad patterns like `upx_exclude=["*.dll"]` or `--upx-exclude "*.dll"` disable all compression and defeat the feature.

Only use `upx_exclude` for custom non-Qt DLLs you specifically know have CFG. Default: omit the flag and let PyInstaller do the right thing.

If UPX isn't saving meaningful size (< 5 MB), disable it entirely with `--noupx` — you trade minimal size reduction for avoiding an entire class of obscure "DLL not found" runtime failures.
