"""命令行界面（主要使用方式）.

命令一览::

    hnd                      # 等价于 hnd run，跑完整套检查
    hnd run --only DNS -v    # 只跑某几项，输出详细日志
    hnd run --format json    # 机器可读输出（适合脚本 / CI）
    hnd run -o report.md     # 导出 Markdown 报告
    hnd list                 # 列出所有已注册检查项
    hnd doctor               # 环境自检
    hnd gui                  # 启动 PySide6 图形界面

退出码：``0`` 全部通过 · ``1`` 存在警告 · ``2`` 存在失败 · ``3`` 工具自身错误
（含参数写错这类用法错误，避免与结果码撞码）。
"""

from __future__ import annotations

import asyncio
import platform
import sys
from ipaddress import AddressValueError, IPv4Address
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from typer import _click  # Typer 0.27 起把 click 内联为 typer._click，用法错误要按它的类型捕获
from typer.core import TyperGroup

from hdu_netdebugger import __version__
from hdu_netdebugger.config import RunConfig
from hdu_netdebugger.console import (
    ConsoleReporter,
    make_console,
    render_checker_table,
    render_doctor,
)
from hdu_netdebugger.constants import HDU_DNS_IP
from hdu_netdebugger.net.ping import Backend, BackendUnavailable
from hdu_netdebugger.registry import all_checkers
from hdu_netdebugger.report import (
    build_json,
    build_markdown,
    exit_code_for,
)
from hdu_netdebugger.runner import run_checks, select_checkers

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hdu_netdebugger.runner import RunResult

__all__ = ["app", "main"]

#: 工具自身出错的退出码
EXIT_TOOL_ERROR = 3

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 100}


class DefaultRunGroup(TyperGroup):
    """不带子命令、或首个参数是 ``run`` 的选项时，默认执行 ``run``。

    这样 ``hnd`` / ``hnd --json`` 与 ``hnd run --json`` 完全等价，
    同时保留 ``hnd list``、``hnd gui`` 等子命令。

    钩子必须挂在 :meth:`parse_args` 而不是 ``resolve_command``：
    后者在选项解析之后才被调用，而 ``--ping-count`` 这类参数只有 ``run`` 认识。
    """

    default_command = "run"

    #: 由顶层直接处理的选项，不能改写成 run 的参数
    passthrough_options = frozenset(
        {"-h", "--help", "--version", "-V", "--install-completion", "--show-completion"}
    )

    def parse_args(self, ctx: _click.Context, args: list[str]) -> list[str]:
        args = list(args)
        if not args:
            args.append(self.default_command)
        elif args[0].startswith("-") and args[0].split("=", 1)[0] not in self.passthrough_options:
            args.insert(0, self.default_command)
        return super().parse_args(ctx, args)

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: object,
    ) -> object:
        """把「用法错误」的退出码收敛到 :data:`EXIT_TOOL_ERROR`。

        typer / click 给参数错误分配的是 ``2``，与「检查有失败」撞码 —— 脚本没法
        区分"我把参数写错了"和"网真的不通"。这里用 ``standalone_mode=False``
        接管退出处理，把 :class:`~typer._click.exceptions.ClickException`
        （含 ``UsageError`` / ``BadParameter``）也归到 ``3``，
        与 README 承诺的退出码约定一致。
        """
        try:
            code = super().main(
                args,
                prog_name,
                complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except _click.exceptions.ClickException as exc:
            exc.show()
            code = EXIT_TOOL_ERROR
        code = code if isinstance(code, int) else 0
        if standalone_mode:
            raise SystemExit(code)
        return code


app = typer.Typer(
    cls=DefaultRunGroup,
    name="hnd",
    help="HDU Net Debugger —— 杭州电子科技大学校园网诊断工具。",
    context_settings=CONTEXT_SETTINGS,
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=True,
    rich_markup_mode="rich",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"hdu-netdebugger-uwu {__version__}")
        raise typer.Exit


@app.callback()
def main_callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="显示版本号并退出。",
        ),
    ] = False,
) -> None:
    """全局选项。"""


@app.command()
def run(
    only: Annotated[
        list[str] | None,
        typer.Option("--only", help="只执行匹配的检查项（名称 / 子串 / 序号），可重复。"),
    ] = None,
    skip: Annotated[
        list[str] | None,
        typer.Option("--skip", help="跳过匹配的检查项，可重复。"),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="输出格式：text / json / md。"),
    ] = "text",
    as_json: Annotated[
        bool,
        typer.Option("--json", help="等价于 --format json。"),
    ] = False,
    as_markdown: Annotated[
        bool,
        typer.Option("--md", "--markdown", help="等价于 --format md。"),
    ] = False,
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="写入文件；用 '-' 表示标准输出。"),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="输出每一项的详细日志。"),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="只输出最终报告，不打印过程。"),
    ] = False,
    ping_count: Annotated[
        int,
        typer.Option("--ping-count", min=1, max=20, help="每个目标 Ping 次数。"),
    ] = 4,
    timeout: Annotated[
        float,
        typer.Option("--timeout", min=0.1, max=30.0, help="单次 Ping 超时（秒）。"),
    ] = 1.0,
    http_timeout: Annotated[
        float,
        typer.Option("--http-timeout", min=0.5, max=60.0, help="HTTP 请求超时（秒）。"),
    ] = 5.0,
    ping_backend: Annotated[
        str,
        typer.Option("--ping-backend", help="Ping 后端：auto / icmplib / windows-icmp / system。"),
    ] = "auto",
    dns_server: Annotated[
        str | None,
        typer.Option("--dns-server", help=f"覆盖 DNS 查询服务器（默认 {HDU_DNS_IP}）。"),
    ] = None,
    auth_host: Annotated[
        str | None,
        typer.Option("--auth-host", help="强制指定认证接口的 host（默认按 DNS 结果自动选择）。"),
    ] = None,
    insecure: Annotated[
        bool,
        typer.Option("--insecure", "-k", help="跳过 TLS 证书校验（校园网自签证书场景）。"),
    ] = False,
    no_env_proxy: Annotated[
        bool,
        typer.Option("--no-env-proxy", help="忽略 HTTP_PROXY / HTTPS_PROXY 环境变量直连。"),
    ] = False,
) -> None:
    """执行校园网检查（默认命令）。"""
    payload_format = _resolve_format(fmt, as_json=as_json, as_markdown=as_markdown)

    try:
        config = _build_config(
            ping_count=ping_count,
            timeout=timeout,
            http_timeout=http_timeout,
            dns_server=dns_server,
            auth_host=auth_host,
            insecure=insecure,
            no_env_proxy=no_env_proxy,
        )
        backend = Backend(ping_backend.lower())
    except (ValueError, AddressValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    try:
        # 提前解析一次：既拿到"共 N 项"的横幅数字，也让 --only / --skip 写错时
        # 报一个干净的用法错误，而不是在 run_checks 内部抛未捕获的 KeyError
        specs = select_checkers(only=only, skip=skip)
    except KeyError as exc:
        raise typer.BadParameter(str(exc).strip("'")) from exc
    if not specs:
        typer.secho("没有匹配到任何检查项。", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_TOOL_ERROR)

    # 载荷要占用 stdout 时，进度信息改走 stderr，避免污染管道
    payload_owns_stdout = payload_format != "text" and output in {None, "-"}
    reporter = ConsoleReporter(
        make_console(stderr=payload_owns_stdout),
        verbose=verbose,
    )
    if not quiet:
        reporter.banner(len(specs))

    try:
        result = asyncio.run(
            run_checks(
                config=config,
                only=only,
                skip=skip,
                verbose=verbose,
                ping_backend=backend,
                on_progress=None if quiet else reporter.on_progress,
            )
        )
    except BackendUnavailable as exc:  # pragma: no cover - 极端环境
        typer.secho(f"无法执行 Ping：{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_TOOL_ERROR) from exc
    except KeyboardInterrupt:  # pragma: no cover - 交互中断
        typer.secho("已中断。", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(EXIT_TOOL_ERROR) from None

    _emit(result, payload_format, output, reporter)
    raise typer.Exit(exit_code_for(result.outcomes))


def _resolve_format(fmt: str, *, as_json: bool, as_markdown: bool) -> str:
    """合并 ``--format`` 与 ``--json`` / ``--md`` 快捷开关。

    Raises:
        typer.BadParameter: 格式名非法，或快捷开关互相冲突。
    """
    if as_json and as_markdown:
        raise typer.BadParameter("--json 与 --md 不能同时使用")
    if as_json:
        return "json"
    if as_markdown:
        return "md"

    normalized = fmt.lower()
    if normalized == "markdown":
        return "md"
    if normalized not in {"text", "json", "md"}:
        raise typer.BadParameter("--format 只支持 text / json / md", param_hint="--format")
    return normalized


def _build_config(
    *,
    ping_count: int,
    timeout: float,
    http_timeout: float,
    dns_server: str | None,
    auth_host: str | None,
    insecure: bool,
    no_env_proxy: bool,
) -> RunConfig:
    """把 CLI 参数翻译成 :class:`RunConfig`。"""
    return RunConfig(
        ping_count=ping_count,
        ping_timeout_s=timeout,
        http_timeout_s=http_timeout,
        dns_timeout_s=max(timeout * 2, 3.0),
        dns_server=IPv4Address(dns_server) if dns_server else HDU_DNS_IP,
        verify_tls=not insecure,
        trust_env_proxy=not no_env_proxy,
        auth_host=auth_host,
    )


def _emit(
    result: RunResult,
    payload_format: str,
    output: str | None,
    reporter: ConsoleReporter,
) -> None:
    """按格式输出结果（终端渲染 + 可选落盘）。"""
    if payload_format == "text":
        reporter.summary(result)
        return

    built = (
        build_json(result.outcomes, checked_at=result.started_at)
        if payload_format == "json"
        else build_markdown(result.outcomes, checked_at=result.started_at)
    )

    if output in {None, "-"}:
        typer.echo(built)
        return

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(built, encoding="utf-8")
    make_console().print(f"[green]✔[/] 报告已写入 [bold]{path}[/]")


@app.command("list")
def list_checkers(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="同时显示每个检查项的模块路径。"),
    ] = False,
) -> None:
    """列出所有已注册的检查项。"""
    console = make_console()
    specs = all_checkers()
    console.print(render_checker_table(specs))
    if not verbose:
        return
    console.print()
    for spec in specs:
        module = f"{spec.cls.__module__}.{spec.cls.__qualname__}"
        console.print(f"[dim]{spec.order:>4}  {module}[/]")


@app.command()
def doctor() -> None:
    """检查运行环境与依赖是否就绪。"""
    console = make_console()
    items: list[tuple[str, str, bool, str]] = []

    items.append(("Python", platform.python_version(), sys.version_info >= (3, 12), ""))
    items.append(
        ("平台", f"{platform.system()} {platform.release()} ({platform.machine()})", True, "")
    )
    items.append(("版本", __version__, True, ""))

    items.extend(_dependency_checks())
    items.extend(_ping_backend_checks())

    console.print(render_doctor(items))
    if any(not ok for _, _, ok, _ in items):
        raise typer.Exit(EXIT_TOOL_ERROR)


def _dependency_checks() -> list[tuple[str, str, bool, str]]:
    """检查运行期依赖。"""
    from importlib.util import find_spec  # noqa: PLC0415 - 仅 doctor 需要

    checks: list[tuple[str, str, bool, str]] = []
    for module, note in (
        ("httpx", "HTTP 探测"),
        ("dns.asyncresolver", "DNS 查询"),
        ("icmplib", "ICMP Ping"),
        ("psutil", "网卡枚举回退"),
        ("rich", "终端渲染"),
        ("typer", "命令行框架"),
    ):
        found = find_spec(module) is not None
        checks.append(
            (f"依赖 {module}", "已安装" if found else "缺失", found, "" if found else note)
        )
    gui = find_spec("PySide6") is not None
    checks.append(
        (
            "可选依赖 PySide6",
            "已安装" if gui else "未安装",
            True,
            "hnd gui 需要它；uv sync --extra gui",
        )
    )
    return checks


def _ping_backend_checks() -> list[tuple[str, str, bool, str]]:
    """真实探测每个 Ping 后端是否可用。"""
    from hdu_netdebugger.net.ping import (  # noqa: PLC0415 - 仅 doctor 需要
        PingOptions,
        ping_multiple,
    )

    checks: list[tuple[str, str, bool, str]] = []
    for backend in (Backend.ICMPLIB, Backend.WINDOWS_ICMP, Backend.SYSTEM):
        if backend is Backend.WINDOWS_ICMP and sys.platform != "win32":
            checks.append((f"Ping 后端 {backend.value}", "不适用", True, "仅 Windows"))
            continue
        try:
            result = asyncio.run(
                ping_multiple("127.0.0.1", PingOptions(count=1, timeout_s=0.5), backend=backend)
            )
        except BackendUnavailable as exc:
            checks.append((f"Ping 后端 {backend.value}", "不可用", False, str(exc)))
            continue
        except Exception as exc:
            checks.append((f"Ping 后端 {backend.value}", "不可用", False, repr(exc)))
            continue
        ok = result.success_count > 0
        checks.append(
            (
                f"Ping 后端 {backend.value}",
                f"{result.success_count}/{result.total_count} 回环可达" if ok else "回环不可达",
                ok,
                f"平均 {result.avg_rtt_ms:.1f} ms" if ok else "",
            )
        )
    return checks


@app.command()
def gui(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="在终端同步打印检查过程。"),
    ] = False,
) -> None:
    """启动 PySide6 图形界面（需要 `uv sync --extra gui`）。"""
    try:
        from hdu_netdebugger.gui import launch  # noqa: PLC0415 - 可选依赖，延迟导入
    except ImportError as exc:
        typer.secho(
            f"缺少 GUI 依赖：{exc}\n请先执行： uv sync --extra gui",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(EXIT_TOOL_ERROR) from exc

    raise typer.Exit(launch(verbose=verbose))


@app.command()
def version() -> None:
    """显示版本号。"""
    typer.echo(f"hdu-netdebugger-uwu {__version__}")
    typer.echo(f"Python {platform.python_version()} on {platform.system()}")


def main() -> None:
    """``hnd`` / ``hdu-netdebugger`` 的入口点。"""
    from hdu_netdebugger.checkers import load_builtin_checkers  # noqa: PLC0415

    load_builtin_checkers()
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
