PYTHON ?= python3
RUFF ?= env UV_TOOL_DIR="$(CURDIR)/.backbone/tools" uvx ruff

.PHONY: check test smoke benchmark
check:
	$(RUFF) check tmux_workspaces tests experiments/workspace_viewer scripts run ghostty preview
	$(RUFF) format --check tmux_workspaces tests experiments/workspace_viewer scripts run ghostty preview
	sh -n tmux-workspaces.tmux
	$(MAKE) test

test:
	$(PYTHON) -m unittest discover -s tests/unit -v
	$(PYTHON) -m unittest discover -s scripts/tests -v

smoke:
	$(PYTHON) -m tests.integration.smoke_inline_rename
	$(PYTHON) -m tests.integration.smoke_ghostty_launch
	$(PYTHON) -m tests.integration.smoke_shortcuts
	$(PYTHON) -m tests.integration.smoke_standalone
	$(PYTHON) -m tests.integration.smoke_environment
	$(PYTHON) -m tests.integration.smoke
	$(PYTHON) -m tests.integration.smoke_windows
	$(PYTHON) -m tests.integration.smoke_plugin

benchmark:
	@$(PYTHON) -m scripts.benchmark $(BENCHMARK_ARGS)
