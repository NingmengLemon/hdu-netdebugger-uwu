# 开发与验证

## 依赖组

- `uv sync --no-default-groups` — 只要 CLI 运行环境（不装开发工具链，也不装 PySide6）
- `uv sync --extra gui` — 额外装 PySide6，用 GUI
- `uv sync --all-groups` — 开发用：lint / 类型检查 / 完整测试套件

> `test` 依赖组里**包含 PySide6** —— 完整测试套件要跑离屏 GUI 用例，缺了它覆盖率门槛会以
> "门槛不达标"的假象失败（曾经如此）。GUI 测试模块在缺依赖时**直接报错**而不是静默跳过，
> 免得 CI 看着绿、其实一条 GUI 用例都没跑。

## 常用命令

```bash
make dev        # uv sync --all-groups --extra gui
make lint       # ruff format --check + ruff check + ty check
make fmt        # ruff check --fix + ruff format
make test       # pytest（含覆盖率门槛 80%）
make test-fast  # pytest --no-cov
make build      # uv build（sdist + wheel）
make exe        # PyInstaller 打包可执行文件（见 docs/packaging.md）
make exe-smoke  # 打包 + 冻结产物自检
```

Windows 上没装 `make` 时，`scripts/tasks.ps1` 提供完全等价的任务：

```powershell
pwsh -File scripts/tasks.ps1 lint
pwsh -File scripts/tasks.ps1 test
pwsh -File scripts/tasks.ps1 fmt
pwsh -File scripts/tasks.ps1 exe-smoke
```

直接调用底层命令也可以：

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```

## 质量工具链

- **[uv](https://docs.astral.sh/uv/)** — 依赖与虚拟环境管理
- **[ruff](https://docs.astral.sh/ruff/)** — lint + format（替代 flake8 / black / isort），`select = ["ALL"]` + 逐条说明理由的 ignore
- **[ty](https://docs.astral.sh/ty/)** — 类型检查（替代 mypy / pyright）
- **pytest + pytest-asyncio + coverage** — 668 个用例，覆盖率门槛 80%（当前 ~93%）

所有网络调用都通过 `RunSession` 注入，因此**检查项**测试用 `tests/fakes.py` 里的
`FakePinger` / `FakeHttp` / `FakeRouter` / `FakeTracer` 就能在毫秒内跑完全部检查项，
不产生任何真实流量，也不需要联网。`net/` 那一层的测试会打本机回环
（ICMP、`127.0.0.1` 上的临时 HTTP 服务），同样不依赖外网。GUI 用 Qt 的 `offscreen`
平台插件离屏测试。

CI（`.github/workflows/ci.yml`）在 push / PR 时跑：

- `ruff format --check` + `ruff check` + `ty check`
- Ubuntu / Windows / macOS × Python 3.12 / 3.13 的完整测试矩阵
  （Linux 会先装齐 Qt 所需的系统库；GUI 测试模块在缺依赖时直接报错，不会静默跳过）
- `uv build` 后把 wheel 装进干净虚拟环境，验证 `hnd --version` / `hnd list` 可用
- 打包可执行文件并在三个平台上跑冻结产物自检（`scripts/dist_smoke.py`）

## 项目结构

```text
.
├── src/hdu_netdebugger/   # 包源码（见下）
├── tests/                 # pytest 用例
├── scripts/               # 运维脚本：tasks.ps1 / gui_smoke.py / gui_render_sample.py
│                          #           dist_smoke.py（冻结产物自检）/ check_glyphs.py
├── packaging/             # PyInstaller 入口脚本 + hnd.spec + Windows 版本资源
├── docs/                  # 文档（本目录）
└── .github/workflows/     # CI（lint / test / build wheel / exe）
```

```text
src/hdu_netdebugger/
├── cli.py            # Typer CLI 入口
├── config.py         # 一次运行的参数（Ping / HTTP / DNS / TLS / 代理开关）
├── console.py        # 终端渲染（横幅、逐项输出、结论面板、doctor 表格）
├── constants.py      # 杭电网络常量（网段 / 服务器 IP / 深澜错误码 / 运营商映射）
├── context.py        # 检查间共享状态（取代原版 GlobalFlagList）
├── registry.py       # @checker / @condition 装饰器与检查项注册表
├── runner.py         # 编排：条件判定 → 执行 → 汇总裁剪
├── report.py         # Markdown / JSON 报告生成
├── session.py        # 一次运行共享的资源（配置 / 状态 / HTTP 池 / Ping 实现 / 路由 / 逐跳）
├── theme.py          # 状态 → 图标 / 颜色的唯一映射（CLI 与 GUI 共用）
├── models/           # 数据模型（检查结果、深澜响应、Ping 结果、网卡）
├── net/              # 网络能力（异步 Ping、HTTP、DNS、网卡枚举、路由、MTU、逐跳、控制台编码）
├── checkers/         # 检查项实现（原版 8 项 + 本复刻新增 3 项）
└── gui/              # PySide6 可选图形界面
```

## 跨平台说明

- **类型检查用 `python-platform = "all"`**。项目里有大量按 `sys.platform` 分流的代码
  （`winreg` / `ctypes.WinDLL` / `get_last_error`）。ty 默认只按当前运行平台解析符号，
  会导致同一份代码在 Windows 上通过、在 Linux 上报"`winreg` 没有 `OpenKey`"这类假阳性。
  设为 `all` 后 ty 不预设目标平台，与运行时守卫的写法一致。
- **界面上的固定符号只用基本多文种平面（BMP）内的字符**。曾经用过 `💡`（U+1F4A1）：
  Windows 有 Segoe UI Emoji 回退所以看着正常，Debian 默认的 WQY 字体没有该字形，
  会直接渲染成豆腐块。需要换符号时先跑 `scripts/check_glyphs.py` 在目标平台确认字形；
  测试里也有断言锁死"界面符号码点 ≤ U+FFFF"。
- **Linux / macOS 的原始 ICMP 套接字无需 root**：`icmplib` 在 Unix 上用
  `SOCK_DGRAM` 非特权模式；Windows 上原始套接字要管理员权限，因此普通用户会自动
  降级到 `iphlpapi!IcmpSendEcho`（ctypes 调用，无需任何权限）。
- **路径 MTU 与逐跳探测依赖平台能力**：Windows 用 `IcmpSendEcho`（可设 DF、可按 TTL 探测）；
  Linux / macOS 走系统 `ping -M do` 与 `traceroute`（`traceroute` 没装时检查项会明确说
  "不可用"并给安装建议，不会假装成功）。

## GUI 的无显示环境验证

`offscreen` 平台插件不接系统字体库，而 PySide6 6.11 起不再随附字体，因此在没有额外装字体的
机器上截出来的文字全是豆腐块（脚本会给出显式告警）：那种截图只能核对布局与配色，
别用它核对文字。Linux CI 会先装 `fonts-wqy-zenhei`。

```bash
uv run python scripts/gui_render_sample.py out.png      # 用固定样本渲染
uv run python scripts/gui_smoke.py out.png              # 真跑一轮检查再截图
```

## 已在真机验证

| 平台 | 结果 |
|------|------|
| Windows 10 IoT 企业版 LTSC 19044（宿舍区，电信出口） | `ruff` / `ty` 全绿；完整用例通过、1 项平台跳过（覆盖率 ~93%）。11 项检查逐项可达：在路由器后且开着代理时，`获取基本信息` / `检查代理状态` 按设计给出隧道网卡警告；关掉代理后 `检查路径MTU` / `检查IPv6` / `检查逐跳路径` 的实测结论见 [checks.md](checks.md) |
| Debian 13 trixie x86_64（Python 3.13 宿主 / venv 3.12） | `ruff` / `ty` 全绿，完整测试套件通过（含离屏 GUI 与 QThread）；非 root 用户下 `SOCK_DGRAM` 非特权 ICMP 正常 |

尚未覆盖（**别当成已验证**）：

- macOS 分支（`ping -W <ms> -m <ttl>`）按 `ping(8)` 语义实现，参数拼装（含 BSD 的毫秒与
  GNU 的秒之差）已用改 `sys.platform` 的方式单测覆盖，但**没有真机跑过**；
- IPv6 检查的"有 v6 地址"正向分支（现有网络没有全局 IPv6 与校园 v6 出口）；
- 教学区的 BAS / 认证测试点与 Linux 上的路径 MTU / 逐跳实测。
