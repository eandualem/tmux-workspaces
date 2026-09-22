# Use tmux-workspaces

A workspace layer for tmux with keyboard shortcuts and mouse controls. Workspaces contain
user-created, named tabs; each tab owns a split layout of ordinary terminals.
Attaching an existing tmux session is optional for each pane. Python 3.11+ and tmux
3.3+ are required. macOS and Linux have automated coverage; WSL remains
unverified. No third-party Python package, terminal plugin, browser or Backbone
service is required. Start with the [first-workspace walkthrough](../README.md#make-your-first-workspace).

From this checkout, in an ordinary terminal:

```sh
./run
```

See [source installation](../README.md#install) to put the launcher on your PATH.
For a separate sample library, use [the preview](UI_PREVIEW.md).

## Launch from tmux with TPM

If you already use [TPM](https://github.com/tmux-plugins/tpm), keep this checkout
at a stable location and link it into TPM's plugin directory. From the checkout:

```sh
mkdir -p "$HOME/.tmux/plugins"
ln -s "$PWD" "$HOME/.tmux/plugins/tmux-workspaces"
```

Use an unused destination; if the plugin is already installed, use that checkout.
Add this line to `~/.tmux.conf` **before** TPM's initialization line:

```tmux
set -g @plugin 'tmux-workspaces'
```

Reload your tmux configuration to load the plugin. Press your outer tmux prefix
(default **Ctrl-b**), release it, then **Shift-w** (**W**) to open a workspace window.
Inside that window, use the viewer's **Ctrl-g** prefix. The plugin reads sessions
from the hosting server; it does not change that server's existing pane options.
It cannot attach the session hosting the viewer inside itself.

Optional tmux settings, also placed before TPM initialization, are
`@tmux-workspaces-key` (launch key, default `W`) and `@tmux-workspaces-data-dir`
(an explicit library directory). The plugin and standalone launcher otherwise
use the same saved library.

## Organize your own terminals

A new library starts with **Workspace 1** and one ordinary shell in a randomly
named tab, such as **Tab a82f**. Nothing is populated from the agent roster.
New workspaces start empty. Existing saved names, tabs and attachments are
preserved when upgrading; the viewer does not delete your earlier arrangements.

Click **+** on the tabs row below the workspace name to open a new tab. The pane starts
empty and asks what it should run: **Open terminal** for a normal login shell,
initially in the directory where you launched the viewer, or any tmux session
from the same roster the navigation panel shows, with its state beside it. Move
with the arrow keys and press Enter, or click a row. Nothing is created until you
choose, so attaching never lands on top of a shell you did not ask for; an empty
pane stays empty across exit and reopen until it is filled. Use a shell for
commands, a status view, an editor, or any other terminal program.
**Rename tab** in the tab menu changes its name. Names belong to
you: attaching a session never changes them. In the name editor, type a name and
click **Save name** or press Enter; Ctrl-u clears the existing name. You can also
double-click the active tab name or workspace name to edit it in place: Enter
saves, and Escape or clicking elsewhere cancels. See [inline naming](INLINE_RENAME.md).

## Split and navigate panes

The selected tab's menu sits behind the **⋯** at the right end of its
row, behind a right-click on any tab, and behind the tab-options shortcut.
**Split right** or **Split below** there adds another pane beside or below the
selected one, as do the split shortcuts. It opens with the same
chooser as a new tab; a terminal chosen there starts in the original shell's
working directory. Four panes still belong to one tab. Click a pane to select
and type there. Drag the separator between panes to adjust sizes. The next and
previous tab shortcuts switch complete layouts, and the next and previous pane
shortcuts move input focus inside the selected tab. **Focus one pane** in the
tab menu temporarily shows only that pane; **Restore layout** shows the splits
again. Split right creates a vertical divider; split below creates a horizontal
divider. Each menu row ends in its prefix key, and selecting a row with the
arrow keys puts every way to reach it, prefix and terminal profile, in the
status row along the bottom of the window.
When the complete layout does not fit, the viewer temporarily shows the focused
pane without discarding the saved arrangement.

## Attach an existing session

To attach a session to a pane that already has a shell, select the pane, open
the tab menu's **Attach session**, then choose a tmux session for that pane.
Type to filter the chooser. The pane's original shell stays running;
**Return to shell** in the tab menu brings it back. Each split can attach a different session or
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
The attachment starts on the source session's current window. A window you select
inside it stays selected through resizing and tab navigation, independently of
the source session. Reopening a closed viewer starts from the source's current
window again. A source with an automatic `destroy-unattached` policy attaches
directly instead: adding a group could delete it. Direct attachments retain the
source's status line and share its current window. The source's own cleanup policy
still applies when its last client detaches. The roster is only an attachment
chooser, never a source of tabs.

### Resize and choose an attachment destination

The navigation panel is 22 columns wide, flush with the left edge, and the
terminals sit on a slightly lighter surface beside it. One thin line in the
outline color separates the panel from the content and one split pane from
another; no border marks the focused pane, the cursor does. The panel,
surface and outline colors are values in **Edit theme…** and may be RGB values
such as `#15171c`, so they can be matched to your terminal's theme. Drag the
line between two panes to resize the split; the panel keeps its width. Saved
split proportions are limited by the space each nested layout needs; a
smaller window temporarily shows the focused pane when
the complete layout cannot fit. The tab menu's **Attach session** and the
attachment shortcut work on all supported tmux versions.
Once open, the chooser keeps its selected destination even if focus moves to
another pane. If another viewer removes or changes that destination, attach again
from the updated pane instead of replacing the peer's change.

### Attachment helper discovery

Viewer-owned grouped attachment sessions carry the tmux session option
`@tmux_workspaces_attachment=1` and are excluded from the viewer's attachment
pickers. Ordinary sessions are never hidden merely because their names begin
with `tw-`. Helpers created by older versions have no marker and remain visible
until their existing attachment naturally closes; reopening that attachment
creates a marked helper. Existing attachments and external sessions are not
migrated or stopped by discovery. Other tools that list tmux sessions manage
their own filtering.

## Switch and organize workspaces

The workspace name at the top of the panel is the workspace chooser: click its
**▾** (or right-click the name) to switch to another workspace, create one,
rename it, give it an icon or delete an empty one. The icon row at the very
bottom switches with one click: each workspace has a three-cell slot showing
its icon, or its number when it has none, with the current one filled; a
right-click on a slot opens that workspace's options, and when the row is
full its last slot, **…**, opens the full list. **Set icon…** offers a small
set of glyphs that render one cell wide in the usual terminal fonts, and
**Number** takes the icon away again; the choice is saved with the workspace.
The workspace shortcuts cycle and select by number. The tab menu
also reorders tabs or moves one to another workspace. Scroll the navigation panel with the wheel or its arrow controls.

## Settings and leaving the viewer

Open **Configure…** near the bottom of the navigation panel for theme and
shortcut editing, the shortcut reference, viewer refresh and optional agent status.

**Edit theme…** opens the built-in JSON editor, in a popup centred over the
panes, with Save and Cancel. Choose a preset or tune individual colors; saved
changes apply to the running viewer. [THEMES.md](THEMES.md) describes every
role. **Edit shortcuts…** uses the same editor, while **View shortcuts…** opens
a read-only reference to the active keys in the same popup frame.

The status row along the bottom of the window, set off by a rule above it,
has three parts. On the left,
where you are: the workspace, the tab and the focused pane, or the selected menu
row and its keys, or the open popup. In the centre, the few keys that matter in
the current mode. On the right, the agents toggle when an agent source is
connected, and a small light while the saved arrangement is current; a message
replaces the light when something needs attention.

**Detach** or closing the terminal window leaves your saved tabs, normal
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
See the [complete shortcut guide](SHORTCUTS.md) for numbered selection,
workspace renaming and other direct controls. Right-click tab rows
or workspace headers/buttons for the corresponding options menu. Rename fields
select the existing name so typing replaces it.

The following portable shortcuts remain available in every profile.
Use **Ctrl-g m** for tab options and **Ctrl-g M** for the workspace menu, and
**Ctrl-g A** to show or hide the agents section.
In choosers and option menus, **Up / Down** or **Ctrl-p / Ctrl-n** moves the
highlight and **Enter** activates it. Type to filter the attachment chooser;
**Escape** closes the menu and returns to the pane. Return to shell, tab
reordering/transfer and empty-workspace deletion are available from these menus.

Press **Ctrl-g**, release it, then press the listed key. These actions operate
inside the viewer and match the clickable controls. **Configure… → View shortcuts…** shows them
in a read-only window.

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
you create with **+** on the tabs row persist like those in a normal library. Runtime
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

## Optional Backbone agent status

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
The roster appears when a state-reporting source is connected (Backbone, found
on its own, or the demo) and can be hidden with **Show agents** in Configure…, with
**Ctrl-g A**, or by clicking **agents shown** in the status row; the choice is
saved with the layout, applies before the first frame, and hides the whole
section.
Nothing in the panel names the session attached to the focused pane; name the
tab for that.

The adapter runs on its own when Backbone's data directory holds its database
(`~/.local/share/agent-backbone`, or `BACKBONE_DATA_DIR`); `--no-backbone`
keeps it off, and `--backbone` asks for it. It polls the existing `/api/agents`
endpoint every five seconds and uses Backbone's state decisions. Generic tmux
sessions remain available beside configured agents, including offline entries.
It reads the API address from Backbone's settings database in read-only mode and
the API key from its `.env`; it never changes those files. Use
`--backbone-data-dir /path/to/data` for another
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

## Verification

See [terminal tests](TESTING.md) for reproducible commands and
[coverage and limits](ACCEPTANCE.md) for recorded results and a manual checklist.
Real PTY tests exercise mouse and keyboard input, shell preservation, attachment,
resize and concurrent windows with disposable resources. They do not establish
native Ghostty GUI appearance or WSL behavior.
