PYTHON ?= python3
RUFF ?= env UV_TOOL_DIR="$(CURDIR)/.backbone/tools" uvx ruff

.PHONY: check test smoke smoke-ssh package-smoke benchmark
check:
	$(RUFF) check tmux_workspaces tests experiments/workspace_viewer scripts run ghostty preview
	$(RUFF) format --check tmux_workspaces tests experiments/workspace_viewer scripts run ghostty preview
	sh -n tmux-workspaces.tmux
	$(MAKE) test

test:
	$(PYTHON) -m unittest discover -s tests/unit -v
	$(PYTHON) -m unittest discover -s scripts/tests -v

smoke:
	$(PYTHON) -m tests.integration.smoke_split_drag
	$(PYTHON) -m tests.integration.smoke_attachment_windows
	$(PYTHON) -m tests.integration.smoke_attachment_palette
	$(PYTHON) -m tests.integration.smoke_attachment_readiness
	$(PYTHON) -m tests.integration.smoke_attachment_identity
	$(PYTHON) -m tests.integration.smoke_theme_refresh
	$(PYTHON) -m tests.integration.smoke_selection
	$(PYTHON) -m tests.integration.smoke_json_settings
	$(PYTHON) -m tests.integration.smoke_themes
	$(PYTHON) -m tests.integration.smoke_refresh
	$(PYTHON) -m tests.integration.smoke_preflight
	$(PYTHON) -m tests.integration.smoke_keymaps
	$(PYTHON) -m tests.integration.smoke_ssh
	$(PYTHON) -m tests.integration.smoke_inline_rename
	$(PYTHON) -m tests.integration.smoke_ghostty_launch
	$(PYTHON) -m tests.integration.smoke_shortcuts
	$(PYTHON) -m tests.integration.smoke_menus
	$(PYTHON) -m tests.integration.smoke_new_tab
	$(PYTHON) -m tests.integration.smoke_gutters
	$(PYTHON) -m tests.integration.smoke_standalone
	$(PYTHON) -m tests.integration.smoke_environment
	$(PYTHON) -m tests.integration.smoke
	$(PYTHON) -m tests.integration.smoke_windows
	$(PYTHON) -m tests.integration.smoke_plugin

smoke-ssh:
	$(PYTHON) -m tests.integration.smoke_ssh --required

benchmark:
	@$(PYTHON) -m scripts.benchmark $(BENCHMARK_ARGS)

# Tests the committed HEAD archive, so commit the packaging prototype first.
package-smoke:
	$(PYTHON) -m scripts.check_package
