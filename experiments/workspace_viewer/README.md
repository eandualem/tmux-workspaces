# Terminal workspace controls

A workspace layer for tmux with keyboard shortcuts and mouse controls. Workspaces contain
user-created, named tabs; each tab owns a split layout of ordinary terminals.
Attaching an existing tmux session is optional for each pane. Python 3.11+ and tmux
3.3+ on macOS, Linux or WSL. No new Python dependencies, terminal plugins,
browser, or Backbone service required.

From this checkout, in an ordinary terminal:

```sh
./run
```

See the [installation guide](../../README.md) for standalone and TPM launchers.
Add `--demo` to make the attachment chooser use disposable shell fixtures,
isolated from Backbone.

## Organize your own terminals

A new library starts with **Workspace 1** and one ordinary shell in a randomly
named tab, such as **Tab a82f**. Nothing is populated from the agent roster.
New workspaces start empty. Existing saved names, tabs and attachments are
preserved when upgrading; the viewer does not delete your earlier arrangements.

Click the top **+** beside the workspace name to open a normal login shell,
initially in the directory where you launched the viewer. Use it for commands,
a status view, an editor, or any other terminal program. **Tab actions… → Rename tab** changes its name. Names belong to
you: attaching a session never changes them. In the name editor, type a name and
click **Save name** or press Enter; Ctrl-u clears the existing name.

Click **Split →** or **Split ↓** to add another ordinary terminal beside or
below the selected pane. A split inherits the original shell's working
directory. Four panes still belong to one tab. Click a pane to select and type
there. Drag the borders to adjust sizes. **Focus** temporarily shows one pane;
**Next →** cycles panes and **Layout** restores the splits. Narrow terminals
use temporary focus without discarding the arrangement.

Select a pane, click **Attach session…** in the navigation panel, then choose a tmux session
for that pane. This is a separate control from **Focus**. Type to filter the chooser.
The pane's original shell stays running;
**Tab actions… → Return pane to shell** brings it back. Each split can attach a different session or
remain an ordinary terminal. Attachment names and states appear as secondary
information, below the active tab’s name. Offline attachments stay associated with
their pane and reconnect when the session returns; the viewer never starts
an external session. The roster is only an attachment chooser, never a source of tabs.

Pane borders stay plain. The navigation panel button, **Tab actions… → Attach session**
and the attachment shortcut work on all supported tmux versions.
Once open, the chooser keeps its selected destination even if focus moves to
another pane. If another viewer removes or changes that destination, attach again
from the updated pane instead of replacing the peer's change.

Click a numbered workspace button to switch, **+** beside the buttons to create a
workspace, or **Workspaces… → Switch workspace** to see the full list. **Workspaces…**
renames a workspace or deletes an empty one. **Tab actions…** also reorders tabs or moves one to another
workspace. Scroll the navigation panel with the wheel or its arrow controls.

**Exit viewer** or closing its terminal window leaves your saved tabs, normal
shells, running programs and agents available for reopening. Explicitly
**closing a pane or tab ends its ordinary shells and their running programs**;
attached external sessions keep running. This distinction also applies when
another viewer window is displaying the same saved tab.

## Keyboard shortcuts

On macOS, `./ghostty` opens a dedicated Ghostty instance with direct Command
shortcuts for the viewer, while preserving your ordinary Ghostty configuration.
Use Command-T for a tab, Command-D / Command-Shift-D for splits,
Command-Shift-[ / ] for tabs, Command-Option-Left / Right for workspaces,
Command-Shift-N for a workspace and Command-R to rename a tab.
See the [complete shortcut guide](../../docs/SHORTCUTS.md) for numbered selection,
workspace renaming and other direct controls. Right-click tab rows or the active tab’s detail line
or workspace headers/buttons for the corresponding options menu. Rename fields
select the existing name so typing replaces it.

The following portable shortcuts remain available in every profile.
The session chooser supports typed filtering, but selecting its result still
requires a mouse; some option menus also lack keyboard activation.

Press **Ctrl-g**, release it, then press the listed key. These actions operate
inside the viewer and match the clickable controls. **Shortcuts…** shows them
in the navigation panel.

- **t** (or **c**): new tab.
- **v** (or **%**): split right, with a vertical divider.
- **h** (or **\"**): split below, with a horizontal divider.
- **a**: attach a session to the focused pane.
- **r**: rename the tab.
- **n / p**: next / previous tab.
- **o / O**: next / previous pane.
- **z**: focus / restore layout.
- **w**: workspace chooser; **W**: new workspace.
- **[ / ]**: previous / next workspace; **R**: rename workspace.
- **s**: select the navigation panel.
- **x**: close the focused pane; **&**: close the tab.
- **d**: detach the viewer, preserving its terminals.

When using `./run` without a terminal shortcut profile, Command-D and other
shortcuts captured by Ghostty or another terminal app remain that app's shortcuts. A program inside the terminal cannot override
a key the app consumes. Use the viewer shortcuts above for saved splits;
the viewer does not modify your terminal configuration. The usual Ctrl-b
prefix reaches the inner tmux attachment. Mouse reporting must be enabled;
holding your terminal's selection modifier may bypass application clicks.

## Isolation and saved state

Each viewer window owns a separate tmux server. Its panes run ordinary tmux
clients attached either to external tmux sessions or to a separate server
containing viewer-owned shells. These persistent terminals have stable
identities; switching tabs, resizing and exiting destroy only display clients,
not the underlying shells. Their directories, variables, foreground programs
and scrollback therefore survive reopening. After a machine reboot or loss
of the terminal server, saved layouts and attachment references remain, and
ordinary shells restart in their last saved directories; running processes
and unsaved in-memory shell state cannot survive that loss.

The viewer never moves, renames, restarts, or kills external sessions and never
sends programmatic messages. Viewer shortcuts use a private navigation panel action socket. Normal
shells do not inherit the viewer's `TMUX`, `TMUX_PANE`, or agent launch metadata,
so Backbone CLI commands use its normal tmux server. Shell startup files run
as they would in a normal terminal. Launcher environment secrets are not
exported into these private servers.

Run the same command in multiple terminal windows. Each has its own selected
workspace, tab, pane, focus mode, dimensions and viewer server. Their saved
library is shared; opening the same terminal in two windows attaches to the
same running shell. Closing one window leaves the others running.

Workspaces, ordered tabs, terminal identities and split layouts live in
`layouts.db` under `$TMUX_WORKSPACES_DATA_DIR`, otherwise
`$XDG_DATA_HOME/tmux-workspaces`, otherwise `~/.local/share/tmux-workspaces`.
Override with `--data-dir` for a separate library. No other checkout's layouts
are imported by default. Concurrent edits to different items
are merged; conflicts on the same item are refreshed with a request to retry.
If a peer changes your active pane's layout or attachment, focus moves to the
navigation panel so typing does not silently land in a different terminal. Last-used
navigation seeds new windows.

Older layouts are imported once into the terminal library in the same
database. Old viewers cannot overwrite the new library. Reopen old windows
to load this behavior; later edits in an old version are not synchronized.
Earlier database tables remain intact for recovery.

Demo layouts and ordinary terminals use a `demo/` subdirectory. The demo's
agent fixtures are disposable and recreated on launch; the normal terminals
you create with the top **+** persist like those in a normal library. Runtime
manifests under `windows/<instance>/` identify the window's sockets. Closing
a window cleans up only its own display server and action socket. The viewer
stores references, not transcripts or credentials.

A normal launch reads only the selected tmux server for its attachment chooser.
Outside tmux this is the default socket; inside tmux it is the invoking server.
Use `--source-socket /path/to/socket` to select another. Generic names may contain
spaces, Unicode and punctuation; display text is kept separate from exact tmux
targets. Newly selected attachments save their source socket. If a later launch
uses a different chooser server, those panes still connect to their saved server
and the navigation panel labels them “saved server”. Earlier version-2 references without
a socket continue using the launcher’s source socket. Demo references follow the
new disposable fixture server on each launch.

The optional adapter requires `--backbone`; it polls the existing `/api/agents`
endpoint every five seconds and uses Backbone's state decisions. Generic tmux
sessions remain available beside configured agents, including offline entries.
It reads the API address from Backbone's settings database in read-only mode and
the API key from its `.env`; it never changes those files. Use
`--backbone --backbone-data-dir /path/to/data` or `BACKBONE_DATA_DIR` for another
installation, and `--url http://127.0.0.1:PORT` for an explicit address (required
when it cannot be read from SQLite, including PostgreSQL setups). Keep the API
key in the adapter data directory's `.env`; launcher environment secrets are
not exported into private tmux servers. Ordinary terminals remain usable when
configuration or the API is unavailable; cached roster states are marked stale.

## Experimental limits

Run the viewer on the machine hosting tmux (locally, through SSH, or in WSL).
This is not a native Windows tmux server or a remote terminal transport.
`TERMINFO` and `TERMINFO_DIRS` are preserved for terminals such as Ghostty
that supply their own terminal definitions. It adds a second tmux layer;
scrolling, copy/clipboard and rendering depend on your terminal and the inner
session's mouse configuration. Writable attachments participate in tmux
sizing, so another viewer showing the same terminal can affect its size.
Layout changes recreate attachment clients and can produce a brief redraw.
Offline demo states are fixtures; demo shells are not AI agents.

The standalone launcher and TPM entry point run the same application. When launched inside tmux, the session hosting the viewer cannot
be attached inside itself; the pane explains this and offers returning to its
ordinary shell. Choose a different session to avoid recursive display.

## Validation

```sh
make check
make smoke
```

The smoke checks use real PTYs, mouse clicks and keyboard shortcuts against
isolated shells. They exercise neutral tab creation, naming, four-pane layouts,
optional attachment, shell identity/directory/environment preservation,
foreground commands surviving viewer exit/reopen, scrollback, resize, offline
references, multi-window synchronization, independent input and cleanup.
They never attach to real agents. On a Mac with Ghostty installed:

```sh
python3 experiments/workspace_viewer/smoke_windows.py \
  --ghostty-terminfo /Applications/Ghostty.app/Contents/Resources/terminfo
```

This verifies a real PTY using `xterm-ghostty`; it does not automate or benchmark
the Ghostty GUI.

See [the acceptance report](../../docs/ACCEPTANCE.md) for results, platform coverage,
and a manual verification checklist. Linux/WSL and direct Ghostty GUI rendering remain
unverified; PTY checks with installed Ghostty terminfo are a separate result.

## Inline names

Double-click the active tab name or the current workspace name beside the top +
to edit that name in place. Enter saves; Escape or a click elsewhere cancels.
The + creates a tab, and the numbered workspace buttons switch workspaces.
Existing right-click menus and rename shortcuts remain available.
