# 打包成可执行文件

给不想装 Python 的同学用。CLI 与 GUI 各打成一个产物。

```bash
uv run --group build pyinstaller --clean --noconfirm packaging/hnd.spec
# 或
make exe                                      # Windows： pwsh -File scripts/tasks.ps1 exe
```

| 产物 | 子系统 | 形态 | 说明 |
|------|--------|------|------|
| `dist/hnd.exe`（Linux/macOS 下是 `dist/hnd`） | 控制台 | onefile | CLI；排除了 PySide6；**单文件 14 MB**，发一个文件就能用 |
| `dist/HDU-NetDebugger/` | 窗口 | onedir | GUI，带 Qt，约 121 MB |
| `dist/hnd-gui-smoke/` | 控制台 | onedir | 只给 CI 用：冻结 `scripts/gui_smoke.py`，证明打进包里的 Qt 能用 |

打完**必须**跑一遍产物自检（CI 的 `exe` 作业就是「打包 + 自检」这两步）：

```bash
uv run python scripts/dist_smoke.py     # CLI --version/list/doctor + 离屏跑 GUI 并截图
```

自检会专门断言"新增的检查项真的出现在冻结产物的 `list` 里" —— 冻结包里少一个检查项是
最容易悄悄发生的事。

## 选型与取舍

- **形态按产物分**：CLI 用 **onefile**（"发一个文件就能用"是它最重要的属性，实测本机
  `hnd --version` 每次启动 1.4~1.7s，其中绝大部分是解压 29 MB 内容到临时目录；
  `hnd run` 本身要几秒，所以这点开销无所谓）；GUI 用 **onedir**（121 MB 的包每次启动都
  解压一遍完全不能接受，而且窗口程序本来就要连目录一起发）。
- **不用 UPX**：体积省不到 5 MB，却会弄坏 Qt 插件与开了 CFG 的 DLL。
- **选 PyInstaller 而不是 Nuitka**：Nuitka 官方文档把 Windows 杀软误报的解法放在**付费**的
  Commercial 计划里且"不保证有效"；本项目是免费校园工具，PyInstaller 那条缓解路线
  （onedir / 不用 UPX / 写版本资源 / 需要时再签名）全部公开且免费。Nuitka 编译期还长得多。
- **不跨平台编译**：PyInstaller 只能在目标平台上构建，所以 CI 用
  windows / ubuntu 矩阵出产物（macOS 标为实验性：能构建、也跑同一套冒烟，但维护者没有机器验证）。
- 冻结后 `PYTHONUTF8` / `PYTHONIOENCODING` 会失效，因此 CLI 入口显式把
  stdout / stderr 固定成 UTF-8 + `errors="replace"`（`cli._make_stdio_robust`）：
  重定向出来的 Markdown 与 `--json` 都是合法 UTF-8，界面符号即使编码不支持也只退化成一个 `?`。
- **冻结产物里的 OpenSSL 必须钉住**：`_ssl` 加载的是构建 CPython 时链接的那份 OpenSSL，
  而打包时 Qt 的 SSL 后端会顺手把系统上另一份 `libssl-3-x64.dll` / `libcrypto-3-x64.dll`
  拉进来（本机曾经拉进了 JRE 自带的那份），两者版本不匹配就会在启动时报
  `ImportError: DLL load failed while importing _ssl`。`packaging/hnd.spec` 里的
  `pin_python_openssl()` 会把非 CPython 的那几份剔除、只留 `_ssl.__file__` 同目录的，
  并有单测断言这条规则还在。`hnd doctor` 会打印实际加载到的 OpenSSL 版本。

## 关于"把 typer 换成 click 能不能瘦身"（实测过，别再重复问）

typer 自身的代码在产物里只占 207 KB（压缩后，其中内联的 click 就占 107 KB），换成独立
click 净省约 **100 KB**，不值得为此重写 490 行 CLI 并丢掉 Typer 的写法。真正的大头是
typer 的 **rich 帮助渲染**：`typer/rich_utils.py` 会 import `rich.markdown`（→ markdown_it）
与 `rich.traceback`（→ **pygments**），实测把这两个排掉后 onefile 从 **14.30 MB 降到
12.47 MB（省 1.83 MB，13%）**。要拿这笔钱只需把 `rich_markup_mode` 关掉（一行），
代价是 `--help` 从 rich 面板退化成 click 风格的纯文本。**当前选择保留 rich 帮助**：
1.8 MB 换中文界面的可读性，对"发给同学的工具"划算。

> 未签名的 Windows 产物首次运行可能被 SmartScreen 拦一下（"更多信息 → 仍要运行"）。
> 真要对外发布，建议加代码签名证书，参见 skill `pyside6-desktop` 的 signing / AV 参考。
