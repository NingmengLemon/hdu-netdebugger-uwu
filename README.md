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
| 20 | 检查 DNS 可用性 | 向 `210.32.32.1` 查询 `portal.hdu.edu.cn` 的 A 记录并校验返回值；同时用**系统解析器**再查一次，并检测本机 DNS 是否被接管 |
| 25 | 检查认证状态 | 请求深澜 `rad_user_info`，解析在线设备、运营商、错误码；HTTPS 不通时回退明文 HTTP，并记录门户首页的跳转目标 |
| 30 | 检查运营商网关可达性 | 依据上一步解析出的运营商 ID Ping 对应出口网关（联通 / 电信 / 移动） |
| 35 | 检查公网可达性 | Ping 阿里 DNS `223.5.5.5` + HTTP 探测 `generate_204` |

检查之间存在**依赖**：例如 DNS 不可用时会自动改用 IP 直连请求认证接口，运营商 ID 无效时
"运营商网关可达性"会被标记为 `跳过` 而不是失败。

### 比原版多做的诊断

"通不通"之外，本复刻还会把**能定位故障的证据**一并打出来（都在 `-v` 的详细日志里）：

- **探测出站归属**：本工具的 ping 到底从哪张网卡出去。装了代理 / VPN 的机器上，
  TUN 会以更低的 metric 抢走默认路由，于是所有探测都从隧道出去 —— 那时"BAS 可达"
  并不代表校园物理链路可达。检查 1 会逐目标列出出口接口 / 下一跳 / metric，
  并在被隧道接管时明确警告。实测本机：校园 BAS、深澜、阿里 DNS 的出口全是
  `Mihomo（ifIndex 75，metric 0）`，只有局域网网关走物理网卡。
- **IP 配置证据**：每个接口的 DHCP 状态、默认网关、系统配置的 DNS、系统接口索引与 MTU。
  排查校园网的第一步通常就是"这台机器到底拿到 IP 没有、网关和 DNS 是不是校园的"。
- **系统解析器对比**：除了问校园 DNS，还用系统配置的解析器解析同一个域名。
  "能连上认证服务器 IP、却打不开 portal 域名"是最常见的一类故障，
  而只测校园 DNS 回答不了这个问题。
- **DNS 接管检测**：向一个不可路由的地址（RFC 5737 `192.0.2.53`）发同样的查询。
  如果它居然答得出来，说明本机 UDP/53 被本地接管（代理 / TUN 的 fake-IP），
  此时"校园 DNS 可用"这个结论不可能失败，属于假阳性 —— 实测本机正是如此。
- **网卡的链路状态、协商速率与 MAC**：有线链路没建立时也会把该网卡打出来
  （"网线插着但系统说没插"这类故障的唯一现场证据），并判断 MAC 是否为随机化地址
  （IEEE 802 本地管理位）—— 校园网按 MAC 做无感知认证，随机 MAC 会导致频繁掉认证。
- **ping 的回复 TTL 与来源**：由 TTL 估算跳数，并在回复来自非目标地址时告警。
  实测在本机上，`ping -i 1`（TTL=1）对公网地址照样有回复、且 TTL 与正常请求一致 ——
  这类"被中间层接管"的探测里，TTL 与来源是少数还能用的证据
  （`icmplib` 后端拿不到 TTL，Windows `IcmpSendEcho` 与系统 `ping` 都能拿到）。
- **认证接口的明文回退**：校园认证接口同时监听 80（官方给出的认证地址就是明文
  `http://192.168.112.97`），因此 TLS / 代理把 HTTPS 弄坏时改走明文仍能读出认证状态；
  另外还会记录门户首页跳向哪个页面（未认证跳登录页、已认证跳管理页）。

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
| `--no-env-proxy` | 本次运行忽略 `HTTP_PROXY` / `HTTPS_PROXY` 等环境变量代理（系统代理仍会检测并提示） |
| `--dns-server IP` | 覆盖查询用的 DNS 服务器 |

退出码：`0` 全部通过 · `1` 存在警告 · `2` 存在失败 · `3` 工具自身错误（含参数写错等用法错误）。
可直接用于 CI / 脚本：结果码与"调用错误"不撞码。

> 直接敲 `hnd`（一个参数都不给）时，检查结束会**停一下等按键**再退出 —— 双击 exe 的同学
> 不会看到黑窗口一闪而过、连结论都没看清。只要带了任何参数（`hnd --json`、`hnd list`、
> `hnd -h`…）就不会停；stdin 不是终端（管道/重定向）时也不会停。等待上限 120 秒，
> 避免在非交互场景里永久挂住。

### GUI

```bash
uv run hnd gui
# 或
uv run python -m hdu_netdebugger.gui
```

![GUI 截图](docs/assets/screenshot.png)

> 截图取自本机一次真实运行（v0.1.0，生活区网段 + 电信出口）：6 项通过、2 项警告
> —— 警告来自 MIHOMO 隧道网卡与"在路由器后使用"这两个按设计给出的提示。

图形界面复刻原版交互：顶部「开始测试 / 导出结果」，下面是一列可展开的检查卡片，
图标按状态着色（灰=未检查、蓝=进行中、绿=通过、黄=警告、红=失败、橙=跳过，
跳过用橙色是为了与"未检查"的灰区分开，与原版一致），失败与警告项会自动展开。
检查在独立 `QThread` 里运行，界面不会卡死；关闭窗口会协作式取消未开始的检查，
等线程真正退出后再关，不阻塞界面。

无显示环境（CI / 远程）下可以用离屏渲染验证界面：

```bash
uv run python scripts/gui_render_sample.py out.png      # 用固定样本渲染
uv run python scripts/gui_smoke.py out.png              # 真跑一轮检查再截图
```

> `offscreen` 平台插件不接系统字体库，而 PySide6 6.11 起不再随附字体，
> 因此在没有额外装字体的机器上截出来的文字全是豆腐块（脚本会给出显式告警）：
> 那种截图只能核对布局与配色，别用它核对文字。Linux CI 会先装 `fonts-wqy-zenhei`。

## 打包成可执行文件

给不想装 Python 的同学用：CLI 与 GUI 各打成一个**目录式**（onedir）产物。

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

选型与取舍：

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
- **关于"把 typer 换成 click 能不能瘦身"（实测过，别再重复问）**：typer 自身的代码在产物里
  只占 207 KB（压缩后，其中内联的 click 就占 107 KB），换成独立 click 净省约 **100 KB**，
  不值得为此重写 490 行 CLI 并丢掉 Typer 的写法。真正的大头是 typer 的 **rich 帮助渲染**：
  `typer/rich_utils.py` 会 import `rich.markdown`（→ markdown_it）与 `rich.traceback`
  （→ **pygments**），实测把这两个排掉后 onefile 从 **14.30 MB 降到 12.47 MB（省 1.83 MB，13%）**。
  要拿这笔钱只需把 `rich_markup_mode` 关掉（一行），代价是 `--help` 从 rich 面板退化成
  click 风格的纯文本。**当前选择保留 rich 帮助**：1.8 MB 换中文界面的可读性，对"发给同学的工具"划算。

> 未签名的 Windows 产物首次运行可能被 SmartScreen 拦一下（"更多信息 → 仍要运行"）。
> 真要对外发布，建议加代码签名证书，参见 skill `pyside6-desktop` 的 signing / AV 参考。

## 项目结构

```text
.
├── src/hdu_netdebugger/   # 包源码（见下）
├── tests/                 # pytest 用例（470+）
├── scripts/               # 运维脚本：tasks.ps1 / gui_smoke.py / gui_render_sample.py
│                          #           dist_smoke.py（冻结产物自检）/ check_glyphs.py
├── packaging/             # PyInstaller 入口脚本 + hnd.spec + Windows 版本资源
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
├── session.py        # 一次运行共享的资源（配置 / 状态 / HTTP 池 / Ping 实现）
├── theme.py          # 状态 → 图标 / 颜色的唯一映射（CLI 与 GUI 共用）
├── models/           # 数据模型（检查结果、深澜响应、Ping 结果、网卡）
├── net/              # 网络能力（异步 Ping、HTTP、DNS、网卡枚举）
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
make build    # uv build（sdist + wheel）
make exe      # PyInstaller 打包可执行文件（见上一节）
make exe-smoke # 打包 + 冻结产物自检
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

质量工具链：

- **[uv](https://docs.astral.sh/uv/)** — 依赖与虚拟环境管理
- **[ruff](https://docs.astral.sh/ruff/)** — lint + format（替代 flake8 / black / isort），`select = ["ALL"]` + 逐条说明理由的 ignore
- **[ty](https://docs.astral.sh/ty/)** — 类型检查（替代 mypy / pyright）
- **pytest + pytest-asyncio + coverage** — 580 个用例，覆盖率门槛 80%（当前 ~95%）

所有网络调用都通过 `RunSession` 注入，因此**检查项**测试用
`tests/fakes.py` 里的 `FakePinger` / `FakeHttp` 就能在毫秒内跑完 8 个检查项，
不产生任何真实流量，也不需要联网。`net/` 那一层的测试会打本机回环
（ICMP、127.0.0.1 上的临时 HTTP 服务），同样不依赖外网。
GUI 用 Qt 的 `offscreen` 平台插件离屏测试。

CI（`.github/workflows/ci.yml`）在 push / PR 时跑：

- `ruff format --check` + `ruff check` + `ty check`
- Ubuntu / Windows / macOS × Python 3.12 / 3.13 的完整测试矩阵
  （Linux 会先装齐 Qt 所需的系统库；GUI 测试模块在缺依赖时直接报错，不会静默跳过）
- `uv build` 后把 wheel 装进干净虚拟环境，验证 `hnd --version` / `hnd list` 可用
- 打包可执行文件并在三个平台上跑冻结产物自检（`scripts/dist_smoke.py`）

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
  `SOCK_DGRAM` 非特权模式；Windows 上原始套接字要管理员权限，因此普通用户会自动
  降级到 `iphlpapi!IcmpSendEcho`（ctypes 调用，无需任何权限）。

### 已在真机验证

| 平台 | 结果 |
|------|------|
| Windows 10 IoT 企业版 LTSC 19044（本机，杭电校园网生活区，电信出口） | `ruff` / `ty` 全绿；580 个用例中 579 通过、1 项平台跳过（覆盖率 ~95%）。8 项检查逐项可达：本机在路由器后且开着 Mihomo，`获取基本信息` / `检查代理状态` 按设计给出对隧道网卡的警告，其余 6 项通过 |
| Debian 13 trixie x86_64（Python 3.13 宿主 / venv 3.12） | `ruff` / `ty` 全绿，完整测试套件通过（含离屏 GUI 与 QThread）；非 root 用户下 `SOCK_DGRAM` 非特权 ICMP 正常 |

> macOS 分支（`ping -W <ms> -m <ttl>`）按 `ping(8)` 语义实现，参数拼装（含 BSD 的
> 毫秒与 GNU 的秒之差）已用改 `sys.platform` 的方式单测覆盖，但**没有真机跑过**。

## 与 C# 原版的差异

| 原版 | 本复刻 |
|------|--------|
| C# 特性 + 反射扫描注册检查项 | Python 装饰器 `@checker(...)` 注册，显式可读；条件用 `@condition(...)`，且支持子类覆盖 |
| `ConcurrentDictionary<string, object> GlobalFlagList` | 强类型 `CheckContext` 数据类 |
| `System.Net.NetworkInformation.Ping` | 三级后端：`icmplib` 原始套接字 → Win32 `IcmpSendEcho` → 系统 `ping` 命令，自动降级 |
| `DnsClient` | `dnspython` 异步解析器，可指定 DNS 服务器 |
| `HttpClient` | `httpx.AsyncClient`（默认不跟随重定向、不保留 Cookie） |
| MVVM + Avalonia | Typer CLI（主）+ PySide6 可选 GUI |
| 无测试 | 580 个 pytest 用例 + GitHub Actions CI |

实现过程中还修正（或发现）了原版的几处问题：

- `AddWarning("")` 会把结论从"通过"提升为"警告"，却一条警告都没记录 —— 现在空白消息不再改变状态。
- 检查项的"命名空间更深者覆盖前者"是隐式行为，现在改成语义明确的 `override=True`，
  重名注册默认直接报错，不会静默失效。
- 复刻时曾把 `IcmpSendEcho` 的目标地址按主机序传入（该 API 要的是网络序），
  结果 Windows 上非管理员用户的保底后端会去 ping 另一个地址，把通的目标全报成超时 ——
  现在用 `_ipaddr_network_order()` 并按 packed 字节小端解释，另有真机回归用例。

## License

MIT
