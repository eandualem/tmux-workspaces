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

See the [installation guide](../README.md) for standalone and TPM launchers.
Add `--demo` to make the attachment chooser use disposable shell fixtures,
isolated from Backbone.

## Organize your own terminals

A new library starts with **Workspace 1** and one ordinary shell in a randomly
named tab, such as **Tab a82f**. Nothing is populated from the agent roster.
New workspaces start empty. Existing saved names, tabs and attachments are
preserved when upgrading; the viewer does not delete your earlier arrangements.

Click the top **+** beside the workspace name to open a new tab. The pane starts
empty and asks what it should run: **Open terminal** for a normal login shell,
initially in the directory where you launched the viewer, or any tmux session
from the same roster the navigation panel shows, with its state beside it. Move
with the arrow keys and press Enter, or click a row. Nothing is created until you
choose, so attaching never lands on top of a shell you did not ask for; an empty
pane stays empty across exit and reopen until it is filled. Use a shell for
commands, a status view, an editor, or any other terminal program.
**Rename tab** in the tab menu changes its name. Names belong to
you: attaching a session never changes them. In the name editor, type a name and
click **Save name** or press Enter; Ctrl-u clears the existing name.

The selected tab's menu sits behind the **⋯** at the right end of its detail
row, behind a right-click on any tab, and behind the tab-options shortcut.
**Split right** or **Split below** there adds another pane beside or below the
selected one, as do the split shortcuts. It opens with the same
chooser as a new tab; a terminal chosen there starts in the original shell's
working directory. Four panes still belong to one tab. Click a pane to select
and type there. Drag the gap between panes to adjust sizes. The tab menu offers
**Previous tab / Next tab** to switch complete layouts, and **Previous pane /
Next pane** to move input focus inside the selected tab. **Focus one pane**
temporarily shows only that pane; **Restore layout** shows the splits again.
Split right creates a vertical divider; split below creates a horizontal divider.
Selecting a menu row with the arrow keys shows its effective prefix or terminal
profile shortcut below the list. Disabled bindings are identified, and long
bindings point to **Configure → View shortcuts** for their full text.
Narrow terminals use temporary focus
without discarding the arrangement. The panel's bottom holds, with a blank
row between each: an optional **Agents** roster, **Configure…**, and the
workspace icons, with one blank row before the outline. **Configure…**
gathers everything infrequent — **Edit theme…**, **Edit shortcuts…**, **View shortcuts…**, **Refresh
viewer…**, the **Show agent status** toggle and **Agent status…** — and, last
after a rule, **Detach**, which leaves the viewer with every shell and
attached session still running.

The roster is an overview of your agent-backbone agents, independent of the
selected tab or pane: one row per active agent, a symbol in a fixed slot and
the name. `▶` is working, `!` needs you (waiting for input, or blocked), `○`
is idle and `?` an unknown state; **Agent status…** spells each state out and
ends with this legend. Offline agents take no row. States come from
agent-backbone's own reports, never from terminal text or a running process;
when the connection fails the section says *Roster unavailable* rather than
showing old states as current. Names keep one alphabetical order as states
change. At most six rows are shown, with scrolling and a count when there are
more, and on short windows the roster gives rows back to the tab list first.
The roster appears when a state-reporting source is connected (`--backbone`,
or the demo) and can be hidden with **Show agent status**; the choice is saved
with the layout, applies before the first frame, and hides the whole section.
Nothing in the panel names the session attached to the focused pane; name the
tab for that.

To attach a session to a pane that already has a shell, select the pane, open
the tab menu's **Attach session**, then choose a tmux session for that pane.
Type to filter the chooser. The pane's original shell stays running;
**Return pane to shell** in the tab menu brings it back. Each split can attach a different session or
remain an ordinary terminal. Offline attachments stay associated with
their pane and reconnect when the session returns; the viewer never starts
an external session. An attached session is joined through a grouped tmux
session of the viewer's own, which shares the session's windows but carries
its own options: it starts with the settings of the session you attached
(mouse mode included) and turns its status line off, so the row it took
returns to the program inside, while the session you attached keeps its own
status line and every other setting untouched. The grouped session disappears
when the pane lets go of it, or as soon as the session it joined is gone, so
a session that ends shows as offline instead of living on inside the viewer.
The roster is only an attachment chooser, never a source of tabs.

The navigation panel is a rounded, outlined panel inset in the window, and
the terminals sit on a slightly lighter surface beside it. Split panes are
separated by one thin line in the outline color, with a blank column of
surface on each side of it, so text never touches a boundary; no border marks
the focused pane, the cursor does. The panel, surface and outline colors are
single values in the colors editor and may be RGB values such as `#22252b`,
so they can be matched to your terminal's theme. The padding is made of thin
panes tmux cannot tell apart from the rest, so a click on one simply hands
focus back to the pane you were in. The tab menu's **Attach session** and the
attachment shortcut work on all supported tmux versions.
Once open, the chooser keeps its selected destination even if focus moves to
another pane. If another viewer removes or changes that destination, attach again
from the updated pane instead of replacing the peer's change.

The workspace name at the top of the panel is the workspace chooser: click its
**▾** (or right-click the name) to switch to another workspace, create one,
rename it, give it an icon or delete an empty one. The icon row at the very
bottom switches with one click: each workspace has a three-cell slot showing
its icon, or its number when it has none, with the current one filled; a
right-click on a slot opens that workspace's options, and when the row is
full its last slot, **…**, opens the full list. **Set icon…** offers a small
set of glyphs that render one cell wide in the usual terminal fonts, and
**Number** takes the icon away again; the choice is saved with the workspace.
The workspace shortcuts cycle and select by number as before. The tab menu
also reorders tabs or moves one to another workspace. Scroll the navigation panel with the wheel or its arrow controls.

**Edit theme…** opens the built-in JSON editor with Save and Cancel. Choose a
preset or tune individual colors; saved changes apply to the running viewer.
[THEMES.md](THEMES.md) describes every role. **Edit shortcuts…** uses the same
editor, while **View shortcuts…** opens a read-only reference to the active keys.
The status line at the bottom of the panel shows a small light while the viewer
is idle and its saved state is current; a message replaces it when something
needs attention.

**Detach** or closing the terminal window leaves your saved tabs, normal
shells, running programs and agents available for reopening. Explicitly
**closing a pane or tab ends its ordinary shells and their running programs**;
attached external sessions keep running. This distinction also applies when
another viewer window is displaying the same saved tab.

### Attachment helper discovery

Viewer-owned grouped attachment sessions carry the tmux session option
`@tmux_workspaces_attachment=1` and are excluded from the viewer's attachment
pickers. Ordinary sessions are never hidden merely because their names begin
with `tw-`. Helpers created by older versions have no marker and remain visible
until their existing attachment naturally closes; reopening that attachment
creates a marked helper. Existing attachments and external sessions are not
migrated or stopped by discovery. Other tools that list tmux sessions manage
their own filtering.

## Keyboard shortcuts

On macOS, `./ghostty` opens a dedicated Ghostty instance with direct Command
shortcuts for the viewer, while preserving your ordinary Ghostty configuration.
Use Command-T for a tab, Command-D / Command-Shift-D for splits,
Command-Shift-[ / ] for tabs, Command-Option-Left / Right for workspaces,
Command-Shift-N for a workspace and Command-R to rename a tab.
See the [complete shortcut guide](SHORTCUTS.md) for numbered selection,
workspace renaming and other direct controls. Right-click tab rows or the active tab’s detail line
or workspace headers/buttons for the corresponding options menu. Rename fields
select the existing name so typing replaces it.

The following portable shortcuts remain available in every profile.
Use **Ctrl-g m** for tab options and **Ctrl-g M** for workspace options.
In choosers and option menus, **Up / Down** or **Ctrl-p / Ctrl-n** moves the
highlight and **Enter** activates it. Type to filter the attachment chooser;
**Escape** closes the menu and returns to the pane. Return to shell, tab
reordering/transfer and empty-workspace deletion are available from these menus.

Press **Ctrl-g**, release it, then press the listed key. These actions operate
inside the viewer and match the clickable controls. **Configure… → View shortcuts…** shows them
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
- **y**: copy the highlighted terminal selection.
- **d**: detach the viewer, preserving its terminals.

When using `./run` without a terminal shortcut profile, Command-D and other
shortcuts captured by Ghostty or another terminal app remain that app's shortcuts. A program inside the terminal cannot override
a key the app consumes. Use the viewer shortcuts above for saved splits;
the viewer does not modify your terminal configuration. The usual Ctrl-b
prefix reaches the inner tmux attachment. Mouse reporting must be enabled;
drag to select within a pane, then use Ctrl-g, y to copy. Release keeps the
highlight; Escape or a click clears it. In a fresh dedicated `./ghostty` window,
Command-C copies the same selection. See [selection and copying](SHORTCUTS.md#selecting-and-copying-terminal-text)
for clipboard support and terminal profile details.

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
scrolling and rendering depend on your terminal and the inner session's mouse
configuration. Selection belongs to the viewer; copying requires the terminal
to support and allow OSC52 clipboard writes. Writable attachments participate in tmux
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
python3 -m tests.integration.smoke_windows \
  --ghostty-terminfo /Applications/Ghostty.app/Contents/Resources/terminfo
```

This verifies a real PTY using `xterm-ghostty`; it does not automate or benchmark
the Ghostty GUI.

See [the acceptance report](ACCEPTANCE.md) for results, platform coverage,
and a manual verification checklist. Linux/WSL and direct Ghostty GUI rendering remain
unverified; PTY checks with installed Ghostty terminfo are a separate result.

## Inline names

Double-click the active tab name or the current workspace name beside the top +
to edit that name in place. Enter saves; Escape or a click elsewhere cancels.
The + on the tabs row creates a tab, and the ▾ beside the workspace name switches workspaces.
Existing right-click menus and rename shortcuts remain available.
