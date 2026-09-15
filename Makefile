.DEFAULT_GOAL := help
.PHONY: help dev sync lint fmt type test test-fast cov build clean run gui

UV ?= uv
PY ?= $(UV) run python

help: ## 显示本帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

dev: ## 安装全部依赖（含 lint/test 工具链与 GUI 额外项）
	$(UV) sync --all-groups --extra gui

sync: ## 按 uv.lock 同步依赖（uv 默认连 dev 组一起装）
	$(UV) sync

lint: ## 只检查、不修改：格式 + lint + 类型
	$(UV) run ruff format --check .
	$(UV) run ruff check .
	$(UV) run ty check

fmt: ## 自动修复格式与可修复的 lint 问题
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

type: ## 仅类型检查
	$(UV) run ty check

test: ## 运行测试（含覆盖率门槛）
	$(UV) run pytest

test-fast: ## 运行测试，跳过覆盖率统计
	$(UV) run pytest --no-cov -q

build: ## 构建 sdist + wheel
	$(UV) build

run: ## 运行一次完整检查
	$(UV) run hnd run -v

gui: ## 启动图形界面
	$(UV) run hnd gui

clean: ## 清理构建产物与缓存
	rm -rf dist build .pytest_cache .ruff_cache .ty_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
