# Architecture and ownership

The application is the `tmux_workspaces` Python package. The source checkout runs
without installation or third-party runtime dependencies: `./run` and
`python3 -m tmux_workspaces` enter the same CLI. A standard wheel exposes the
`tmux-workspaces` console command. Ghostty, preview and TPM launchers remain source
checkout conveniences; installing the wheel does not install a terminal profile
or edit tmux configuration.

## Module responsibilities

- `model.py`: workspace/tab/split state and pure layout reconciliation. No database,
  tmux or integration imports. New leaves capture the caller's current directory.
- `persistence.py`: version-2 SQLite storage, migration and transactions. `Store`
  combines shared arrangements with each viewer's independent navigation state.
- `tmux.py`: explicit-socket subprocess calls and the child environment policy.
- `shells.py`: creation, cwd capture and explicit deletion of ordinary shell
  sessions on a library's persistent shell server.
- `display.py`: split geometry, focus and disposable attachment clients on one
  viewer's private display server. It never moves or owns external panes.
- `attachments.py`: external attachment clients and recursive-host protection;
  the leaf helper imports no curses controller, SQLite store or metadata adapter.
- `sidebar.py`: curses UI controller, menu state, drawing and user actions. It
  composes the model, store, source and display; it does not launch the application.
- `controls.py` and `name_editor.py`: action transport/key mappings and inline
  name-editing input. `targets.py` keeps exact tmux targets separate from labels.
- `cli.py`: argument defaults, validation surface and dispatch. `application.py`
  composes dependencies and owns launch, demo and viewer cleanup. Action dispatch
  imports neither the application nor the attachment client.
- `source.py`: optional session metadata polling plus generic discovery. The
  existing opt-in Backbone implementation remains here pending its separate
  adapter extraction; this refactor does not claim that separation is complete.
- `entrypoints.py`: quoted helper commands that work after tmux clears launcher
  environment variables. `ghostty_launcher.py`, `tmux_plugin.py` and `ui_preview.py`
  are optional entry-point integrations.

This remains a small standard-library application. There is no service framework,
container or generic plugin registry. Extract further UI collaborators when an
independent behavior needs one, instead of adding forwarding layers for each method.

## Process and data lifetimes

Each viewer launch owns its display server, action socket and runtime directory.
Closing that viewer destroys those resources and attachment clients. It preserves
the library's shell server and every external tmux server. Only explicit pane/tab
close ends that pane's ordinary shell; attached processes always remain external.
The `Store` owns its SQLite connection, and `Source` owns its polling thread.
The application closes both when the curses controller exits.

The persisted schema, historical `agent` attachment field, library defaults and
socket naming are unchanged. Updating code does not migrate user libraries or
restart running terminals. Normal launch never reads Backbone configuration or
calls its API; importing the package alone loads no application or adapter.

## Existing terminals and entry paths

Already-running viewers embed absolute commands to
`experiments/workspace_viewer/viewer.py`; loaded TPM bindings can embed
`scripts/tmux_plugin.py`. Keep those small executable shims while such viewers may
still be running. The old Ghostty and preview command paths are retained too.
Only these legacy file shims add their known checkout root to `sys.path`, because
Python starts direct script execution in the legacy directory. Runtime modules and
tests use normal package imports, with no path mutation.

New source helpers invoke the absolute `run` launcher. Installed helpers invoke
`python -m tmux_workspaces` with the same interpreter that launched the application.
This works from another directory without relying on `PYTHONPATH`, which tmux's
child environment intentionally excludes. The root launcher also works through a
symlink, because Python resolves the script's actual location for import lookup.

## Verification

`make check` runs lint/format checks, TPM shell syntax and the unit suites under
`tests/unit`. Some focused adapter and input regressions use disposable real tmux
servers. `make smoke` runs the seven full PTY scenarios as modules under
`tests/integration`. Shared PTY process, mouse, persistence and wait helpers live
in `tests/integration/support.py`; no test imports from the old experiment directory.

For relocation verification, copy tracked files to a fresh directory whose name
contains spaces, without `.git` or development memory, then run `make check` and
`make smoke` there. All fixtures create their own libraries and private tmux sockets.
The module, source symlink and compatibility helpers must work from an unrelated
working directory. Package installation does not substitute for these PTY checks.
