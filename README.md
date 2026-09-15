# HDU NetDebugger (Uwu)

> 杭州电子科技大学校园网诊断工具 —— [HDU-ICU/HDU_NetDebugger](https://github.com/HDU-ICU/HDU_NetDebugger) 的 Python 复刻版。
> **CLI 优先**，另提供 PySide6 可选 GUI。

一键跑完 8 项网络体检，逐项给出 **状态 / 总览 / 警告 / 建议 / 详细日志**，并可导出 Markdown 或 JSON 报告，
方便直接贴给 ICU（网络中心）排查。

```text
$ hnd
╭──────────────────────── HDU Net Debugger ────────────────────────╮
│ 检查时间 2025-01-01 12:00:00            共 8 项 · 通过 6 · 警告 1 · 失败 1 │
╰──────────────────────────────────────────────────────────────────╯
 ✔ 获取基本信息              检测到生活区网络
 ✔ 检查代理状态              未检测到代理配置
 ✔ 检查BAS可达性             BAS 可达
 ✔ 检查生活区深澜认证可达性    深澜认证服务器可达
 ✔ 检查DNS可用性             DNS 查询成功，返回了正确的 A 记录
 ✔ 检查认证状态              认证状态正常
 ✔ 检查运营商网关可达性        中国电信网关可达
 ✖ 检查公网可达性             公网不可达
```

## 功能

复刻自 C#/Avalonia 原版的全部检查项，按 `order` 顺序执行：

| # | 检查项 | 说明 |
|---|--------|------|
| -1 | 获取基本信息 | 枚举活动网卡，识别**生活区**（`10.150.0.0/16`）/ **教学区**（`10.249.0.0/16`）网段，并嗅探 VPN / Tunnel 网卡 |
| 5 | 检查代理状态 | 检测系统代理与 `HTTP_PROXY` / `HTTPS_PROXY` 等环境变量代理 |
| 10 | 检查 BAS 可达性 | Ping `10.150.0.1`，不可达时进一步判断是否根本不在校园网 |
| 15 | 检查生活区深澜认证可达性 | Ping 深澜认证服务器 `192.168.112.97` |
| 20 | 检查 DNS 可用性 | 向 `210.32.32.1` 查询 `portal.hdu.edu.cn` 的 A 记录并校验返回值 |
| 25 | 检查认证状态 | 请求深澜 `rad_user_info`，解析在线设备、运营商、错误码 |
| 30 | 检查运营商网关可达性 | 依据上一步解析出的运营商 ID Ping 对应出口网关（联通 / 电信 / 移动） |
| 35 | 检查公网可达性 | Ping 阿里 DNS `223.5.5.5` + HTTP 探测 `generate_204` |

检查之间存在**依赖**：例如 DNS 不可用时会自动改用 IP 直连请求认证接口，运营商 ID 无效时
"运营商网关可达性"会被标记为 `跳过` 而不是失败。

## 安装

需要 Python ≥ 3.12 与 [uv](https://docs.astral.sh/uv/)。

```bash
git clone <repo> && cd Hdu_netDebugger_Uwu

# 只要 CLI 运行环境：不装 dev 工具链，也不装 PySide6
uv sync --no-default-groups

# 想直接用 GUI
uv sync --extra gui

# 开发：lint / 类型检查 / 完整测试套件
uv sync --all-groups
```

> `test` 依赖组里包含 PySide6 —— 完整测试套件要跑离屏 GUI 用例，缺了它覆盖率门槛
> 会以"门槛不达标"的假象失败，所以让它跟着 `uv sync` 一起装上；只要 CLI 运行环境时
> 用上面的 `--no-default-groups`。

## 使用

### CLI

```bash
uv run hnd                      # 跑完整套检查（等价于原版「开始测试」）
uv run hnd run                  # 同上，显式子命令
uv run hnd run --json           # 机器可读输出（适合脚本 / CI）
uv run hnd run -o report.md     # 导出 Markdown 报告
uv run hnd run -v               # 打印每一项的详细日志
uv run hnd run --only 检查DNS可用性 --only 检查认证状态
uv run hnd run --skip 检查公网可达性
uv run hnd list                 # 列出所有已注册的检查项
uv run hnd doctor               # 自检：运行环境、依赖、权限
uv run hnd gui                  # 启动 PySide6 图形界面
```

`hdu-netdebugger` 是 `hnd` 的等价长命令。

常用参数：

| 参数 | 说明 |
|------|------|
| `--only` / `--skip` | 按名称（支持前缀/模糊匹配）筛选检查项，可重复 |
| `-f, --format {text,json,md}` | 输出格式，默认 `text` |
| `-o, --output PATH` | 写入文件；`-` 表示 stdout |
| `-v, --verbose` | 输出每一项的详细日志 |
| `--timeout SEC` | 单次 Ping 超时（默认 `1.0`） |
| `--ping-count N` | 每个目标 Ping 次数（默认 `4`） |
| `--insecure` | 跳过 TLS 证书校验（校园网自签证书场景） |
| `--no-env-proxy` | 忽略 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量直连 |
| `--dns-server IP` | 覆盖查询用的 DNS 服务器 |

退出码：`0` 全部通过 · `1` 存在警告 · `2` 存在失败 · `3` 工具自身错误（含参数写错等用法错误）。
可直接用于 CI / 脚本：结果码与"调用错误"不撞码。

### GUI

```bash
uv run hnd gui
# 或
uv run python -m hdu_netdebugger.gui
```

![GUI 截图](docs/screenshot.png)

图形界面复刻原版交互：顶部「开始测试 / 导出结果」，下面是一列可展开的检查卡片，
图标按状态着色（灰=未检查、蓝=进行中、绿=通过、黄=警告、红=失败、灰=跳过），
失败与警告项会自动展开。检查在独立 `QThread` 里运行，界面不会卡死；
关闭窗口会协作式取消未开始的检查并等待线程退出。

无显示环境（CI / 远程）下可以用离屏渲染验证界面：

```bash
uv run python scripts/gui_render_sample.py out.png      # 用固定样本渲染
uv run python scripts/gui_smoke.py out.png              # 真跑一轮检查再截图
```

## 项目结构

```text
src/hdu_netdebugger/
├── cli.py            # Typer CLI 入口
├── constants.py      # 杭电网络常量（网段 / 服务器 IP / 深澜错误码 / 运营商映射）
├── context.py        # 检查间共享状态（取代原版 GlobalFlagList）
├── registry.py       # @checker / @condition 装饰器与检查项注册表
├── runner.py         # 编排：条件判定 → 执行 → 汇总裁剪
├── report.py         # Markdown / JSON 报告生成
├── models/           # 数据模型（检查结果、深澜响应、Ping 结果）
├── net/              # 网络能力（异步 Ping、HTTP、DNS）
├── checkers/         # 8 个检查项实现
└── gui/              # PySide6 可选图形界面
```

## 开发

```bash
make dev      # uv sync --all-groups --extra gui
make lint     # ruff format --check + ruff check + ty check
make fmt      # ruff check --fix + ruff format
make test     # pytest（含覆盖率门槛 80%）
make test-fast # pytest --no-cov
make build    # uv build
```

Windows 上没装 `make` 时，`scripts/tasks.ps1` 提供完全等价的任务：

```powershell
pwsh -File scripts/tasks.ps1 lint
pwsh -File scripts/tasks.ps1 test
pwsh -File scripts/tasks.ps1 fmt
```

直接调用底层命令也可以：

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```

质量工具链：

- **[uv](https://docs.astral.sh/uv/)** — 依赖与虚拟环境管理
- **[ruff](https://docs.astral.sh/ruff/)** — lint + format（替代 flake8 / black / isort），`select = ["ALL"]` + 逐条说明理由的 ignore
- **[ty](https://docs.astral.sh/ty/)** — 类型检查（替代 mypy / pyright）
- **pytest + pytest-asyncio + coverage** — 380 个用例，覆盖率门槛 80%（当前 ~90%）

所有网络调用都通过 `RunSession` 注入，因此测试用
`tests/fakes.py` 里的 `FakePinger` / `FakeHttp` 就能在毫秒内跑完 8 个检查项，
不产生任何真实流量，也不需要联网。GUI 用 Qt 的 `offscreen` 平台插件离屏测试。

CI（`.github/workflows/ci.yml`）在 push / PR 时跑：

- `ruff format --check` + `ruff check` + `ty check`
- Ubuntu / Windows / macOS × Python 3.12 / 3.13 的完整测试矩阵
  （Linux 会先装齐 Qt 所需的系统库，避免 `importorskip` 静默跳过 GUI 测试）
- `uv build` 后把 wheel 装进干净虚拟环境，验证 `hnd --version` / `hnd list` 可用

### 跨平台说明

- **类型检查用 `python-platform = "all"`**。项目里有大量按 `sys.platform` 分流的代码
  （`winreg` / `ctypes.WinDLL` / `get_last_error`）。ty 默认只按当前运行平台解析符号，
  会导致同一份代码在 Windows 上通过、在 Linux 上报"`winreg` 没有 `OpenKey`"这类假阳性。
  设为 `all` 后 ty 不预设目标平台，与运行时守卫的写法一致。
- **界面上的固定符号只用基本多文种平面（BMP）内的字符**。曾经用过 `💡`（U+1F4A1）：
  Windows 有 Segoe UI Emoji 回退所以看着正常，Debian 默认的 WQY 字体没有该字形，
  会直接渲染成豆腐块。需要换符号时先跑 `scripts/check_glyphs.py` 在目标平台确认字形；
  测试里也有断言锁死"界面符号码点 ≤ U+FFFF"。
- **Linux / macOS 的原始 ICMP 套接字无需 root**：`icmplib` 在 Unix 上用
  `SOCK_DGRAM` 非特权模式；Windows 上则退回 Win32 `IcmpSendEcho2`（无固定计时误差问题见下）。

### 已在真机验证

| 平台 | 结果 |
|------|------|
| Windows 11（本机，杭电校园网，电信出口） | `ruff` / `ty` 全绿，389 用例通过，8 项检查全部通过，GUI 正常 |
| Debian 13 trixie x86_64（Python 3.13 宿主 / venv 3.12） | `ruff` / `ty` 全绿，389 用例通过（含离屏 GUI 与 QThread），8 项检查全部通过；非 root 用户下 `SOCK_DGRAM` 非特权 ICMP 正常 |

> macOS 分支（`ping -W <ms> -m <ttl>`）已按 `ping(8)` 语义实现并有单测覆盖参数拼装，
> 但没有真机跑过。

## 与 C# 原版的差异

| 原版 | 本复刻 |
|------|--------|
| C# 特性 + 反射扫描注册检查项 | Python 装饰器 `@checker(...)` 注册，显式可读；条件用 `@condition(...)`，且支持子类覆盖 |
| `ConcurrentDictionary<string, object> GlobalFlagList` | 强类型 `CheckContext` 数据类 |
| `System.Net.NetworkInformation.Ping` | 三级后端：`icmplib` 原始套接字 → Win32 `IcmpSendEcho2` → 系统 `ping` 命令，自动降级 |
| `DnsClient` | `dnspython` 异步解析器，可指定 DNS 服务器 |
| `HttpClient` | `httpx.AsyncClient`（默认不跟随重定向、不保留 Cookie） |
| MVVM + Avalonia | Typer CLI（主）+ PySide6 可选 GUI |
| 无测试 | 380 个 pytest 用例 + GitHub Actions CI |

实现过程中还修正了原版的两处小问题：

- `AddWarning("")` 会把结论从"通过"提升为"警告"，却一条警告都没记录 —— 现在空白消息不再改变状态。
- 检查项的"命名空间更深者覆盖前者"是隐式行为，现在改成语义明确的 `override=True`，
  重名注册默认直接报错，不会静默失效。

## License

MIT
