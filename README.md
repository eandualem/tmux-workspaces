# tmux-workspaces

**A workspace layer for tmux.**

The terminal used to be where you did your work. Now it is also where work
happens without you: agents keep building, testing and investigating while you
turn to something else. Checking in is a browsing posture, and terminals were
built for typing.

tmux-workspaces gives that work an arrangement you can return to. Group terminals
by purpose, name their tabs, and keep a complete split layout in each tab. Use
keyboard shortcuts or the mouse to move between purposes and inspect running
sessions. Every new tab or split starts as an ordinary interactive shell.

**Pull, not push** is the attention principle: check in when you choose. The
workspace layer adds no notifications, rings or interruptions. Session availability
is glanceable; richer agent state comes only from an optional adapter.

The arrangement persists: what is grouped, what it is named, and where it sits.
tmux keeps the underlying processes running. Workspaces represent purposes such as
development, review or operations, rather than particular screen sizes.

## Start in your terminal

Requirements: **Python 3.11+**, **tmux 3.3+**, and a Unix terminal. There are no
third-party Python runtime dependencies. Run from the top of your checkout:

```sh
./run
```

Use **Ctrl-g**, release it, then:

- **t**: new tab; **v / h**: split right / below.
- **n / p**: next / previous tab; **o / O**: next / previous pane.
- **W**: new workspace; **[ / ]**: previous / next workspace.
- **r / R**: rename tab / workspace; **z**: focus one pane / restore its layout.
- **a**: open the attachment chooser; **d**: exit the viewer.

Core navigation and editing work by shortcut. The dedicated terminal profile has
17 core action bindings plus numbered selection for tabs and workspaces 1–9.

**Mouse-required paths today:** selecting a session in Attach after filtering,
returning a pane to its parked shell, reordering tabs, moving a tab between
workspaces, and deleting an empty workspace. User-defined bindings and keyboard
menu navigation are separate planned improvements. See the
[shortcut guide](docs/SHORTCUTS.md).

On macOS with Ghostty, `./ghostty` opens a dedicated shortcut profile:
**Command-T** creates a tab, **Command-D / Command-Shift-D** split,
**Command-Shift-[ / ]** switch tabs, **Command-Option-Left / Right** switch
workspaces, and **Command-R** renames a tab. The launcher reads your ordinary
Ghostty appearance without editing its configuration. Native keys that the
terminal consumes cannot reach the viewer without a terminal mapping.

The top **+** creates a tab; the **+** beside the workspace buttons creates a
workspace. Double-click the active tab name or the workspace name at the top to
edit in place: **Enter** saves and **Escape** cancels. Right-click those names or
workspace buttons for their options. See [inline names](docs/INLINE_RENAME.md).

## Attach the sessions you already have

Select a pane, choose **Attach session…**, and pick an existing tmux session.
No agent hooks or agent launcher are required. Sessions created by ordinary tmux,
agent tools or an orchestration service use the same attachment mechanism.
The viewer discovers sessions on the invoking tmux server, or the default server
when started outside tmux. Select another server explicitly when needed:

```sh
./run --source-socket /path/to/existing/tmux.sock
```

Attaching keeps the tab's name and parks its ordinary shell.
**Tab actions… → Return pane to shell** brings that shell back. Offline sessions
keep their saved association and reconnect when they return. Closing a tab or
pane ends its own ordinary shells; it leaves attached external sessions running.
The session hosting a nested viewer cannot attach to itself.

Backbone is an [optional read-only adapter](#optional-backbone-adapter).
A normal launch makes no Backbone configuration or API calls and imports no
Backbone layouts. Session-creation tools complement the workspace layer; they
continue to own their sessions and lifecycles.

## Built around remote tmux hosts

Remote use is central to the design. Run the workspace layer **on the tmux host**
from your SSH terminal, so its organization stays where the work runs. It needs
no desktop window system on that host. Linux servers, development machines and
SSH-accessible environments are the intended setting alongside local terminals.
This is an on-host application, not an SSH connection manager: `--source-socket`
selects a socket on that host, and does not connect to another machine.

Linux and macOS pass the real-terminal integration suites in CI. WSL and an
actual SSH connection still need verification; see [coverage and limits](docs/ACCEPTANCE.md).
The viewer
adapts to available terminal dimensions by temporarily focusing a pane when a
layout will not fit. It does not save a different arrangement per physical display.

## Keep your arrangement

Choose **Exit** and reopen with the same command to resume. Saved names, grouping,
splits and attachment references remain. Ordinary shells and their running
programs also survive viewer exit while their tmux server remains alive.
After a host reboot or shell-server loss, the arrangement remains and shells
restart in saved directories; the previous processes cannot be recovered.

The library directory is `--data-dir`, otherwise `$TMUX_WORKSPACES_DATA_DIR`,
otherwise `$XDG_DATA_HOME/tmux-workspaces`, otherwise
`~/.local/share/tmux-workspaces`. Open the same library in multiple terminal
windows for shared arrangements and independent navigation. Writable tmux clients
can still affect the size of a session they share.

To try sample layouts, run `./preview --terminal`, or `./preview` with macOS
Ghostty. It creates a separate sample library under `.backbone/ui-preview/demo/`
in the checkout, with disposable shell attachments. No live agents are used.
See the [sample guide](docs/UI_PREVIEW.md) and
[full controls and persistence guide](docs/GUIDE.md).

## Install the launcher or load with TPM

From the checkout, put the launcher on PATH by linking it:

```sh
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/run" "$HOME/.local/bin/tmux-workspaces"
```

Keep the checkout in place. With `~/.local/bin` on PATH, `tmux-workspaces` works
from another directory.

For an existing TPM installation, link the checkout into its plugin directory:

```sh
mkdir -p "$HOME/.tmux/plugins"
ln -s "$PWD" "$HOME/.tmux/plugins/tmux-workspaces"
```

Place this before your existing TPM initialization in `~/.tmux.conf`, then reload:

```tmux
set -g @plugin 'tmux-workspaces'
```

To load that linked plugin directly in the current tmux server:

```sh
tmux run-shell '"$HOME/.tmux/plugins/tmux-workspaces/tmux-workspaces.tmux"'
```

**Prefix W** opens a new window with the workspace layer and the invoking server
available for attachment. Existing panes and server options remain available.
The plugin changes its own launch binding; the viewer uses private display and
shell servers. It does not eliminate nested tmux layers.

Optional settings before plugin loading:

```tmux
set -g @tmux-workspaces-key 'W'
set -g @tmux-workspaces-data-dir '/absolute/path/to/a/library'
# Optional read-only adapter:
set -g @tmux-workspaces-backbone 'on'
set -g @tmux-workspaces-backbone-data-dir '/absolute/path/to/backbone-data'
set -g @tmux-workspaces-url 'http://127.0.0.1:7120'
```

Choose an unused launch key. Reload the plugin after changing that key;
data and adapter options are read on each launch. Source checkout and TPM are
the current installation paths. A Homebrew tap is under consideration.

## Optional Backbone adapter

```sh
./run --backbone
# Explicit configuration and source socket:
./run --backbone \
  --backbone-data-dir /path/to/backbone-data \
  --url http://127.0.0.1:7120 \
  --source-socket /path/to/backbone-tmux.sock
```

Only `--backbone` enables configuration/API access. `--url` and
`--backbone-data-dir` require it. The adapter directory defaults to
`$BACKBONE_DATA_DIR` or `~/.local/share/agent-backbone`. Keep the API key in that
directory's `.env`; only that named secret is read. The address can come from
existing SQLite settings, or `--url` when those settings are unavailable.

The adapter polls `/api/agents` every five seconds and adds supplied agent states
and offline references to the chooser. It permits loopback HTTP without redirects.
Failures mark statuses stale while generic sessions and ordinary terminals remain
usable. It never starts, stops, renames or messages an agent. Layouts contain
references, not credentials or transcripts.

## Performance

An exploratory macOS PTY measurement used **0.7–1.1% of one CPU core at idle**
with one- and four-pane layouts. These were short, ten-second samples on one
development host, including the viewer's tmux servers, shells and helper processes.
See the [measurement scope and limits](docs/ACCEPTANCE.md#idle-cpu-baseline).

**Switching performance remains a public-release gate.** Reducing rendering and
tmux command costs needs measured improvement for both clicks and shortcuts.
The main viewer interpreter stays alive and skips unchanged frames; action and
attachment helpers can still start during interaction.

## Verify

```sh
make check
make smoke
```

Tests use private tmux sockets and disposable shells. They exercise mouse and
keyboard input, attachment, concurrent edits, resizing and persistence. The
[acceptance report](docs/ACCEPTANCE.md) distinguishes tested platforms and remaining
limits. Nested tmux can affect redraws, sizing, selection, clipboard and scrollback.

For code responsibilities and ownership, see [architecture](docs/ARCHITECTURE.md).

The imported MIT notice is retained in [LICENSE](LICENSE).
See [provenance](docs/PROVENANCE.md) and the [product contract](docs/INITIAL_TASK.md).
