# 与 C# 原版的差异

原版：[HDU-ICU/HDU_NetDebugger](https://github.com/HDU-ICU/HDU_NetDebugger)（C# + Avalonia）。

| 原版 | 本复刻 |
|------|--------|
| C# 特性 + 反射扫描注册检查项 | Python 装饰器 `@checker(...)` 注册，显式可读；条件用 `@condition(...)`，且支持子类覆盖 |
| `ConcurrentDictionary<string, object> GlobalFlagList` | 强类型 `CheckContext` 数据类 |
| `System.Net.NetworkInformation.Ping` | 三级后端：`icmplib` 原始套接字 → Win32 `IcmpSendEcho` → 系统 `ping` 命令，自动降级 |
| `DnsClient` | `dnspython` 异步解析器，可指定 DNS 服务器 |
| `HttpClient` | `httpx.AsyncClient`（默认不跟随重定向、不保留 Cookie） |
| MVVM + Avalonia | Typer CLI（主）+ PySide6 可选 GUI |
| 无测试 | 677 个 pytest 用例 + GitHub Actions CI |

检查项本身：原版 8 项全部复刻（`order` 一一对应），另有 3 项新增（路径 MTU / IPv6 /
逐跳路径，`order` 排在原版之后），见 [checks.md](checks.md)。

## 复刻过程中修正（或发现）的原版问题

- `AddWarning("")` 会把结论从"通过"提升为"警告"，却一条警告都没记录 —— 现在空白消息不再
  改变状态。
- 检查项的"命名空间更深者覆盖前者"是隐式行为，现在改成语义明确的 `override=True`，
  重名注册默认直接报错，不会静默失效。
- 复刻时曾把 `IcmpSendEcho` 的目标地址按主机序传入（该 API 要的是网络序），结果 Windows 上
  非管理员用户的保底后端会去 ping 另一个地址，把通的目标全报成超时 —— 现在用
  `net/winaddr.network_order_ipaddr()` 统一转换（并按 packed 字节小端解释），另有真机
  回归用例；这条约定只留一处实现，避免再次散落。
- **运营商网关**：原版三个地址里有两个与 ICU 官方手册冲突（疑似把"出口公网网段"当成了
  网关），现已改成候选列表逐个探测。
- **关窗行为**：原版式的写法是 `thread.wait(15000)` + 超时后 `terminate()`，实测在
  Windows 上会把消息泵饿死十几秒（显示"无响应"），而 `terminate()` 会 hard-kill 一个
  正在跑 `asyncio.run()` 的线程（它持有 GIL），结果是"窗口没了、进程还在"。
  本复刻改成**协作式异步关闭**：`closeEvent` 只请求取消并 `event.ignore()`，
  等线程真正结束再 `self.close()`，等待因此有界（每个网络调用都带超时）。

## 有意保持一致的细节

- 检查项的 `order`、名称与总览文案尽量沿用原版措辞（例如"检测到生活区网络"、
  "深澜认证服务器可达"），方便对照两边的输出。
- 界面上"跳过"用橙色、与"未检查"的灰区分开，也是照原版来的。
- 命令行默认跑全套检查（原版对应"开始测试"按钮）。
