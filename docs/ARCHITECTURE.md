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
- `layout_validation.py`: bounded validation of durable records before model
  recursion or terminal creation, including identifiers and attachment references.
- `persistence.py`: version-2 SQLite storage, migration and transactions. `Store`
  combines shared arrangements with each viewer's independent navigation state.
- `tmux.py`: explicit-socket subprocess calls and the child environment policy.
- `shells.py`: creation, cwd capture and explicit deletion of ordinary shell
  sessions on a library's persistent shell server. Each new shell explicitly
  sets/unsets the creating viewer's SSH-agent and XDG context, preventing stale
  server-global paths from leaking across reconnects or concurrent viewers.
- `display.py`: split geometry, focus and disposable attachment clients on one
  viewer's private display server. It never moves or owns external panes.
- `attachments.py`: external attachment clients and recursive-host protection;
  the leaf helper imports no curses controller, SQLite store or metadata adapter.
  Attachment uses tmux's `-E` flag to preserve external session environments.
- `sidebar.py`: curses UI controller, menu state, drawing and user actions. It
  composes the model, store, source and display; it does not launch the application.
- `controls.py`: stable action identifiers/default codes and acknowledged action
  transport. `keymap.py` validates optional TOML maps and generates effective
  shortcut labels and terminal profiles. `name_editor.py` owns inline name input;
  `targets.py` keeps exact tmux targets separate from labels.
- `bootstrap.py`: an early Python floor guard shared by public and compatibility
  launchers, before application imports. `preflight.py`: serverless tmux version
  and minimum terminal/curses checks before opening a library.
- `cli.py`: argument defaults, validation surface and dispatch. `application.py`
  composes dependencies and owns launch, demo and viewer cleanup. Action dispatch
  imports neither the application nor the attachment client.
- `discovery.py`: the read-only `Provider.read() -> Snapshot` contract, with explicit
  stale/error/last-successful-observation fields and shared item parsing.
- `source.py`: provider polling and snapshot composition, with no integration
  imports, configuration reads or provider-specific branches. The UI receives
  independent copies of the current observation.
- `adapters/tmux.py`: generic session discovery on one explicit socket.
  `adapters/backbone.py`: opt-in loopback HTTP/config metadata and stale cache.
  `adapters/demo.py`: isolated fixture reads and stale cache. `application.make_source`
  selects these providers with lazy imports; ordinary launch never imports the
  Backbone or demo provider.
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
The application closes both when the curses controller exits. Startup registers
each resource as soon as it is created, so partial setup closes earlier resources
and reports an error to the launcher. Cleanup attempts every callback while
retaining the original failure; shared shell servers are never teardown targets.

The private display server has one session. It uses `exit-unattached` to retire
the entire server, with `destroy-unattached` off so session/window destruction
does not run separately during the detach handshake. Normal exit requests detach;
the launcher also cleans up after its attached client returns. Unexpected server
loss retains its nonzero exit status.

The persisted schema, historical `agent` attachment field, library defaults and
socket naming are unchanged. Updating code does not migrate user libraries or
restart running terminals. Normal launch never reads Backbone configuration or
calls its API; importing the package alone loads no application or adapter.

## Input ordering

Configuration is selected and validated before runtime resources are created.
The launcher transports a complete immutable keymap snapshot to the controller;
Ghostty also freezes it in its new-surface command to match the instance's profile.
Only the private display server's prefix table and direct CSI bindings change.
The prefix table is rebuilt from that map so disabling a viewer key cannot expose
an underlying native tmux command. Escape cancels a prefix and a doubled prefix
passes through literally. User maps contain validated keys and action identifiers,
never shell fragments. No config file is reread during navigation.

The private tmux server holds each client's command queue while action helpers
wait for the controller's acknowledgement. The controller applies the action,
sets focus and draws before replying. Both shortcut sequences and navigation-panel
left-button downs use this path, so text from the same terminal read cannot run
before navigation finishes. Content mouse input keeps tmux's native behavior.
The private viewer disables `assume-paste-time`: tmux's timing-based paste guess
can otherwise bypass shortcut bindings after rapid text, notably on tmux 3.4.
Explicit bracketed paste remains supported and is tested against an ordinary
interactive shell. External servers and terminal configurations are unchanged.

Every physical left-button down matters: tmux names rapid subsequent downs
`SecondClick1Pane` and `TripleClick1Pane`. Its later `DoubleClick1Pane` notification
is a duplicate, so the navigation panel ignores it and uses its existing
coordinate/time check for inline renaming. Forwarding that delayed notification
again can reopen an editor after immediate typing has already completed it.

## Navigation reads and attachment startup

A controller action, input event or periodic poll opens a short display snapshot
scope. Its first read collects pane IDs, active/last focus, dead status, geometry
and window size together. Later reads in that same event reuse the result. Focus
and layout mutations invalidate it, including `select_sidebar()`, and scope exit
always discards it, even on failure. No pane-state snapshot survives to the next
event; resize, pane death and other-client changes are read freshly.

Layout completion selects focus and writes pane metadata in one tmux command
batch before acknowledging input. Ordinary sessions have already been ensured by
the shell owner, so their first attachment attempts to connect directly. It still
checks recursive-host protection when host information exists, and retains the
offline/reconnection loop after the client returns. External sessions retain their
preflight and host checks. The leaf entry path does not import the action transport.

Changing tabs can reuse the private display's pane containers when the effective
visible split directions and ratios, window dimensions, exact owned pane IDs and
all pane rectangles still match the last completed layout. Every content pane must
also be alive. Leaf traversal order maps the old containers to the new targets.
One command batch selects the navigation panel, restarts each attachment wrapper,
writes target metadata and selects the final focus. Persistent shells and external
sessions keep running; no inactive attachment clients are parked, and no living
wrapper is retargeted with a stale recovery destination.

Target commands are validated before respawning anything. A planning or partial
batch failure clears the reuse state and returns typing focus to the navigation
panel; the next render rebuilds the layout. Resize, topology/ratio changes, missing
or dead panes and empty tabs also use the full rebuild. The rectangle baseline is
captured after a full rebuild and is never promoted by ratio capture or a name-only
update: a border drag conservatively costs one rebuild on subsequent navigation.
Reuse itself needs no additional post-render pane query.

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
servers. `make smoke` runs the full PTY scenarios as modules under
`tests/integration`. Shared PTY process, mouse, persistence and wait helpers live
in `tests/integration/support.py`. Its `FixtureResources` owns freshly allocated
libraries, explicitly qualified servers and clients through failure and interruption.
Runtime manifests never grant cleanup authority. `ssh_support.py` owns a disposable
authenticated loopback daemon and client keys; `smoke_ssh.py` verifies the remote
PTY lifecycle. No test imports from the old experiment directory. See
[test setup and platform scope](TESTING.md).

For relocation verification, copy tracked files to a fresh directory whose name
contains spaces, without `.git` or development memory, then run `make check` and
`make smoke` there. All fixtures create their own libraries and private tmux sockets.
The module, source symlink and compatibility helpers must work from an unrelated
working directory. Package installation does not substitute for these PTY checks.

## Read-only provider contract

A provider exposes only `read() -> Snapshot`: it discovers sessions or enriches
metadata, and has no start/stop/rename/message operation for external sessions.
The source owns polling; application composition supplies a primary discovery
provider and zero or more metadata overlays. Adding a provider does not require a
branch in Source or the model, persistence, display or input modules.

Primary discovery determines online attachment availability. Overlay names may
remain as offline references; an API's agent-state or online field cannot make a
missing tmux session attachable. A failed Backbone/demo read returns its last good
metadata with `stale=True` and a fixed error message, preserving its last successful
`observed_at` Unix timestamp. No successful observation means `observed_at=None`.
Generic tmux discovery failures report unavailable sessions instead of retaining
potentially false online claims. A missing external server is a successful empty
observation, so a fresh installation needs no external sessions.

The combined observation is stale if any provider is stale. Its timestamp is the
oldest constituent observation, or None if any provider has never succeeded.
Errors are joined without including credentials, response bodies or transport
exception text. Source never merges overlay availability claims, and another
provider's stale result cannot remove live generic sessions.

Backbone construction reads nothing. Only an explicitly selected provider's `read`
resolves the named API secret and existing SQLite settings, using a read-only
connection. It performs loopback HTTP GET requests with no proxies or redirects.
Demo construction similarly does not read its fixture until requested. Demo
composition uses a nonpersistent source socket reference so recreated fixtures can
reconnect; ordinary attachments retain their exact source socket. Neither path
changes the historical saved `agent` field or version-2 layout schema.

Source also isolates unexpected provider exceptions and malformed snapshot values.
One provider cannot stop polling or prevent other providers from publishing.
Fallbacks retain that provider's last good observation with a fixed, sanitized
error; unexpected discovery failure makes cached names explicitly offline.
A subsequent successful read clears the stale error. Cancellation exceptions derived
from BaseException are deliberately allowed to propagate.

## Layout validation

Layout loading validates existing records before schema writes. Launch preflights
the library before creating any private tmux server. Existing-current-record errors
also block refresh/save, rather than falling back to older tables or overwriting
the original bytes. See [recovery](RECOVERY.md) for explicit backup/copy workflows.
