"""命令行界面：默认命令、过滤、输出格式、退出码。"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from hdu_netdebugger import cli as cli_module
from hdu_netdebugger.cli import EXIT_TOOL_ERROR, app
from hdu_netdebugger.models.enums import CheckStatus
from hdu_netdebugger.models.result import CheckOutcome
from hdu_netdebugger.net.ping import BackendUnavailable
from hdu_netdebugger.runner import RunResult

runner = CliRunner()

PASS_OUTCOMES = [
    CheckOutcome(
        name="检查DNS可用性",
        order=20,
        status=CheckStatus.PASSED,
        summary="DNS 正常",
        details="DNS 查询成功",
    )
]
WARN_OUTCOMES = [
    CheckOutcome(
        name="获取基本信息",
        order=-1,
        status=CheckStatus.WARNED,
        summary="未检测到校园网地址",
        warnings=["未检测到校园网地址"],
        suggestions=["请检查网络连接"],
    )
]
FAIL_OUTCOMES = [
    CheckOutcome(
        name="检查公网可达性",
        order=35,
        status=CheckStatus.FAILED,
        summary="公网不可达",
        warnings=["阿里主DNS不可达。"],
    )
]


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch):
    """替换掉真正的跑检查过程，让 CLI 测试只关心参数与输出。"""

    def install(outcomes: list[CheckOutcome]):
        captured: dict[str, object] = {}

        async def fake_run_checks(**kwargs: object) -> RunResult:
            captured.update(kwargs)
            on_progress = kwargs.get("on_progress")
            for index, outcome in enumerate(outcomes):
                if callable(on_progress):
                    event = type(
                        "ProgressEvent",
                        (),
                        {
                            "spec": type("S", (), {"name": outcome.name})(),
                            "index": index,
                            "total": len(outcomes),
                            "outcome": outcome,
                        },
                    )()
                    on_progress(event)
            return RunResult(outcomes=list(outcomes), duration_s=0.42, session=None)

        monkeypatch.setattr(cli_module, "run_checks", fake_run_checks)
        return captured

    return install


# ---------------------------------------------------------------------------
# 基础命令
# ---------------------------------------------------------------------------


class TestBasicCommands:
    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "HDU Net Debugger" in result.stdout

    def test_short_help_flag(self) -> None:
        result = runner.invoke(app, ["-h"])
        assert result.exit_code == 0
        assert "Commands" in result.stdout

    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "hdu-netdebugger-uwu" in result.stdout

    def test_version_command(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "hdu-netdebugger-uwu" in result.stdout
        assert "Python" in result.stdout

    def test_list_command(self) -> None:
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "检查DNS可用性" in result.stdout
        assert "获取基本信息" in result.stdout

    def test_list_verbose_shows_modules(self) -> None:
        result = runner.invoke(app, ["list", "-v"])
        assert result.exit_code == 0
        assert "hdu_netdebugger.checkers" in result.stdout

    def test_unknown_command_fails(self) -> None:
        result = runner.invoke(app, ["nope"])
        assert result.exit_code != 0


class TestDefaultCommand:
    def test_bare_invocation_runs_checks(self, fake_run) -> None:
        captured = fake_run(PASS_OUTCOMES)
        result = runner.invoke(app, ["--ping-count", "1"])
        assert result.exit_code == 0
        assert captured, "run_checks 应当被调用"
        assert "检查DNS可用性" in result.stdout

    def test_options_before_subcommand_are_forwarded(self, fake_run) -> None:
        captured = fake_run(PASS_OUTCOMES)
        runner.invoke(app, ["--only", "DNS", "--ping-count", "2"])
        assert captured["only"] == ["DNS"]

    def test_explicit_run_subcommand(self, fake_run) -> None:
        captured = fake_run(PASS_OUTCOMES)
        result = runner.invoke(app, ["run", "--only", "DNS"])
        assert result.exit_code == 0
        assert captured["only"] == ["DNS"]


# ---------------------------------------------------------------------------
# 过滤
# ---------------------------------------------------------------------------


class TestFiltering:
    def test_only_is_forwarded(self, fake_run) -> None:
        captured = fake_run(PASS_OUTCOMES)
        runner.invoke(app, ["run", "--only", "DNS", "--only", "认证"])
        assert captured["only"] == ["DNS", "认证"]

    def test_skip_is_forwarded(self, fake_run) -> None:
        captured = fake_run(PASS_OUTCOMES)
        runner.invoke(app, ["run", "--skip", "公网"])
        assert captured["skip"] == ["公网"]

    def test_unknown_filter_errors(self) -> None:
        result = runner.invoke(app, ["run", "--only", "根本不存在的检查项"])
        assert result.exit_code == EXIT_TOOL_ERROR
        assert "没有匹配" in result.output

    def test_unknown_skip_filter_errors(self) -> None:
        """回归：``--skip`` 写到不存在的名字时曾抛未捕获的 KeyError（traceback + 退出码 1）。"""
        result = runner.invoke(app, ["run", "--skip", "根本不存在的检查项"])
        assert result.exit_code == EXIT_TOOL_ERROR
        assert "没有匹配" in result.output
        assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# 输出格式
# ---------------------------------------------------------------------------


class TestOutputFormats:
    def test_json_flag(self, fake_run) -> None:
        fake_run(PASS_OUTCOMES)
        result = runner.invoke(app, ["run", "--json"])
        payload = json.loads(result.stdout)
        assert payload["summary"]["passed"] == 1
        assert payload["checks"][0]["name"] == "检查DNS可用性"

    def test_format_json(self, fake_run) -> None:
        fake_run(PASS_OUTCOMES)
        result = runner.invoke(app, ["run", "--format", "json"])
        assert json.loads(result.stdout)["summary"]["total"] == 1

    def test_markdown_flags(self, fake_run) -> None:
        fake_run(PASS_OUTCOMES)
        for flag in ("--md", "--markdown"):
            result = runner.invoke(app, ["run", flag])
            assert result.stdout.startswith("# HDU Net Debugger 检查结果报告")

    def test_markdown_alias_for_format(self, fake_run) -> None:
        fake_run(PASS_OUTCOMES)
        result = runner.invoke(app, ["run", "--format", "markdown"])
        assert result.stdout.startswith("# HDU Net Debugger 检查结果报告")

    def test_invalid_format_rejected(self, fake_run) -> None:
        fake_run(PASS_OUTCOMES)
        result = runner.invoke(app, ["run", "--format", "xml"])
        assert result.exit_code != 0
        assert "--format 只支持" in result.output

    def test_conflicting_shortcuts_rejected(self, fake_run) -> None:
        fake_run(PASS_OUTCOMES)
        result = runner.invoke(app, ["run", "--json", "--md"])
        assert result.exit_code != 0
        assert "不能同时使用" in result.output

    def test_output_to_file(self, fake_run, tmp_path) -> None:
        fake_run(FAIL_OUTCOMES)
        target = tmp_path / "nested" / "report.md"
        result = runner.invoke(app, ["run", "--md", "-o", str(target)])
        assert target.exists()
        assert "检查公网可达性" in target.read_text(encoding="utf-8")
        assert "报告已写入" in result.stdout

    def test_output_dash_is_stdout(self, fake_run) -> None:
        fake_run(PASS_OUTCOMES)
        result = runner.invoke(app, ["run", "--json", "-o", "-"])
        assert json.loads(result.stdout)["summary"]["total"] == 1


# ---------------------------------------------------------------------------
# 退出码
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_pass_is_zero(self, fake_run) -> None:
        fake_run(PASS_OUTCOMES)
        assert runner.invoke(app, ["run"]).exit_code == 0

    def test_warning_is_one(self, fake_run) -> None:
        fake_run(WARN_OUTCOMES)
        assert runner.invoke(app, ["run"]).exit_code == 1

    def test_failure_is_two(self, fake_run) -> None:
        fake_run(FAIL_OUTCOMES)
        assert runner.invoke(app, ["run"]).exit_code == 2

    def test_quiet_still_prints_summary(self, fake_run) -> None:
        fake_run(FAIL_OUTCOMES)
        result = runner.invoke(app, ["run", "-q"])
        assert result.exit_code == 2
        assert "检查结论" in result.stdout
        assert "检查公网可达性" in result.stdout


class TestUsageErrorExitCode:
    """用法错误必须归到「工具自身错误」(3)。

    click 默认给参数错误分配 ``2``，与「检查有失败」撞码，脚本就没法区分
    "参数写错了"和"网真的不通"。这里把整条用法错误路径钉住。
    """

    @pytest.mark.parametrize(
        "argv",
        [
            ["run", "--only", "没有这个检查项"],
            ["run", "--skip", "没有这个检查项"],
            ["run", "--format", "xml"],
            ["run", "--json", "--md"],
            ["run", "--ping-backend", "magic"],
            ["run", "--dns-server", "not-an-ip"],
            ["run", "--ping-count", "abc"],
            ["run", "--timeout", "0"],
            ["nope"],
        ],
    )
    def test_usage_errors_exit_three(self, argv: list[str]) -> None:
        result = runner.invoke(app, argv)
        assert result.exit_code == EXIT_TOOL_ERROR, result.output
        assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# 配置参数
# ---------------------------------------------------------------------------


class TestConfigOptions:
    def test_ping_and_timeout_forwarded(self, fake_run) -> None:
        captured = fake_run(PASS_OUTCOMES)
        runner.invoke(
            app,
            ["run", "--ping-count", "3", "--timeout", "2.5", "--http-timeout", "9"],
        )
        config = captured["config"]
        assert config.ping_count == 3
        assert config.ping_timeout_s == 2.5
        assert config.http_timeout_s == 9

    def test_ping_backend_forwarded(self, fake_run) -> None:
        captured = fake_run(PASS_OUTCOMES)
        runner.invoke(app, ["run", "--ping-backend", "system"])
        assert str(captured["ping_backend"]) == "system"

    def test_invalid_ping_backend_rejected(self, fake_run) -> None:
        fake_run(PASS_OUTCOMES)
        result = runner.invoke(app, ["run", "--ping-backend", "magic"])
        assert result.exit_code != 0

    def test_dns_server_forwarded(self, fake_run) -> None:
        captured = fake_run(PASS_OUTCOMES)
        runner.invoke(app, ["run", "--dns-server", "223.5.5.5"])
        assert str(captured["config"].dns_server) == "223.5.5.5"

    def test_invalid_dns_server_rejected(self, fake_run) -> None:
        fake_run(PASS_OUTCOMES)
        result = runner.invoke(app, ["run", "--dns-server", "not-an-ip"])
        assert result.exit_code != 0

    def test_insecure_and_no_env_proxy(self, fake_run) -> None:
        captured = fake_run(PASS_OUTCOMES)
        runner.invoke(app, ["run", "--insecure", "--no-env-proxy"])
        config = captured["config"]
        assert config.verify_tls is False
        assert config.trust_env_proxy is False

    def test_auth_host_forwarded(self, fake_run) -> None:
        captured = fake_run(PASS_OUTCOMES)
        runner.invoke(app, ["run", "--auth-host", "portal.example.com"])
        assert captured["config"].auth_host == "portal.example.com"

    def test_verbose_progress_is_rendered(self, fake_run) -> None:
        fake_run(WARN_OUTCOMES)
        result = runner.invoke(app, ["run", "-v"])
        assert "未检测到校园网地址" in result.stdout
        assert "请检查网络连接" in result.stdout


# ---------------------------------------------------------------------------
# 错误处理
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_backend_unavailable_is_reported(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def boom(**_kwargs: object) -> RunResult:
            raise BackendUnavailable("没有可用的 Ping 后端")

        monkeypatch.setattr(cli_module, "run_checks", boom)
        result = runner.invoke(app, ["run"])
        assert result.exit_code == EXIT_TOOL_ERROR

    def test_keyboard_interrupt_is_reported(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def boom(**_kwargs: object) -> RunResult:
            raise KeyboardInterrupt

        monkeypatch.setattr(cli_module, "run_checks", boom)
        result = runner.invoke(app, ["run"])
        assert result.exit_code == EXIT_TOOL_ERROR


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


class TestDoctor:
    def test_reports_dependencies_and_backends(self) -> None:
        result = runner.invoke(app, ["doctor"])
        assert "运行环境自检" in result.stdout
        assert "Python" in result.stdout
        assert "依赖 httpx" in result.stdout
        assert "Ping 后端" in result.stdout


# ---------------------------------------------------------------------------
# gui 子命令
# ---------------------------------------------------------------------------


class TestGuiCommand:
    def test_gui_command_is_registered(self) -> None:
        result = runner.invoke(app, ["gui", "--help"])
        assert result.exit_code == 0
        assert "PySide6" in result.stdout
