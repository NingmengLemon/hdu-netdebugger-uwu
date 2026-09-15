<#
.SYNOPSIS
    与 Makefile 等价的任务入口（供 Windows / 没有 make 的环境使用）。

.DESCRIPTION
    用法::

        pwsh -File scripts/tasks.ps1 lint
        pwsh -File scripts/tasks.ps1 test
        pwsh -File scripts/tasks.ps1 fmt
        pwsh -File scripts/tasks.ps1 run
        pwsh -File scripts/tasks.ps1 gui

    所有命令都通过 ``uv run`` 在项目虚拟环境里执行，不需要手动激活 venv。
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'dev', 'sync', 'lint', 'fmt', 'type', 'test', 'test-fast', 'build', 'run', 'gui', 'clean')]
    [string]$Task = 'help'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# 纯 cmdlet 的分支（help / clean）不会设置 $LASTEXITCODE，而 StrictMode 下读取
# 未赋值的变量会直接抛错 —— 曾经 `pwsh -File scripts/tasks.ps1 help`（也是默认任务）
# 在打印完帮助后以退出码 1 失败。先给个 0，后面的原生命令会按需覆盖它。
$LASTEXITCODE = 0

$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot
try {
    switch ($Task) {
        'help' {
            Write-Host '可用任务：' -ForegroundColor Cyan
            @(
                @('dev', '安装全部依赖（含 lint/test 工具链与 GUI extra）'),
                @('sync', '按 uv.lock 同步依赖（uv 默认连 dev 组一起装）'),
                @('lint', '只检查不修改：ruff format --check + ruff check + ty check'),
                @('fmt', '自动修复格式与可修复的 lint 问题'),
                @('type', '仅类型检查'),
                @('test', '运行测试（含覆盖率门槛 80%）'),
                @('test-fast', '运行测试，跳过覆盖率统计'),
                @('build', '构建 sdist + wheel'),
                @('run', '运行一次完整检查'),
                @('gui', '启动图形界面'),
                @('clean', '清理构建产物与缓存')
            ) | ForEach-Object { '  {0,-10} {1}' -f $_[0], $_[1] }
        }
        'dev' { uv sync --all-groups --extra gui }
        'sync' { uv sync }
        'lint' {
            uv run ruff format --check .
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
            uv run ruff check .
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
            uv run ty check
        }
        'fmt' {
            uv run ruff check --fix .
            uv run ruff format .
        }
        'type' { uv run ty check }
        'test' { uv run pytest }
        'test-fast' { uv run pytest --no-cov -q }
        'build' { uv build }
        'run' { uv run hnd run -v }
        'gui' { uv run hnd gui }
        'clean' {
            foreach ($path in 'dist', 'build', '.pytest_cache', '.ruff_cache', '.ty_cache', '.coverage', 'htmlcov') {
                Remove-Item -Recurse -Force $path -ErrorAction SilentlyContinue
            }
            Get-ChildItem -Recurse -Directory -Filter '__pycache__' |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
