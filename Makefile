PYTHON ?= python3
RUFF ?= env UV_TOOL_DIR="$(CURDIR)/.backbone/tools" uvx ruff

.PHONY: check test smoke
check:
	$(RUFF) check experiments/workspace_viewer scripts run ghostty preview
	$(RUFF) format --check experiments/workspace_viewer scripts run ghostty preview
	sh -n tmux-workspaces.tmux
	$(MAKE) test

test:
	$(PYTHON) -m unittest discover -s experiments/workspace_viewer/tests -v

smoke:
	$(PYTHON) experiments/workspace_viewer/smoke_inline_rename.py
	$(PYTHON) experiments/workspace_viewer/smoke_ghostty_launch.py
	$(PYTHON) experiments/workspace_viewer/smoke_shortcuts.py
	$(PYTHON) experiments/workspace_viewer/smoke_standalone.py
	$(PYTHON) experiments/workspace_viewer/smoke.py
	$(PYTHON) experiments/workspace_viewer/smoke_windows.py
	$(PYTHON) experiments/workspace_viewer/smoke_plugin.py
