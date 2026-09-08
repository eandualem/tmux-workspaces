# Direct shortcuts and right-click menus

On macOS, use the dedicated Ghostty launcher for one-press Command shortcuts:

```sh
./ghostty
```

It opens a separate Ghostty instance with this project's keybinding profile, using
the same default library as `./run`. The existing Ghostty configuration is read
for your appearance but is never edited or reloaded. Ordinary Ghostty windows
keep their current bindings. Both instances still share macOS application identity
and restoration preferences; this is a shortcut profile, not a separate app bundle.

For an existing custom library, pass the same options you already use:

```sh
./ghostty --data-dir /absolute/path/to/your/library
./ghostty --backbone --backbone-data-dir /path/to/backbone-data
```

To replace an already-open viewer, use **Exit viewer**, then launch with `./ghostty`.
Do not close its tabs to upgrade: **Exit viewer** preserves shells and programs;
closing a pane or tab ends that pane/tab's ordinary shells. Existing views can also
remain open alongside the new window; they share arrangements and shells.

## Tabs and splits

These follow Ghostty's usual keys, with the actions applied to saved workspaces:

- **Command-T**: new tab with an ordinary shell.
- **Command-D**: split right, with a vertical divider.
- **Command-Shift-D**: split below, with a horizontal divider.
- **Command-[ / Command-]**: previous / next pane.
- **Command-Shift-[ / Command-Shift-]**: previous / next tab.
- **Command-1 … Command-9**: select tab 1 … 9 in the current workspace.
- **Command-Shift-Enter**: focus the selected pane / restore its split layout.
- **Command-R**: rename the current tab. Type the replacement name, then Enter.
- **Command-W**: close the selected pane; closing its last pane closes the tab.
- **Command-Shift-W**: close the whole tab and its ordinary shells.

## Workspaces and other controls

- **Command-Option-Left / Right**: previous / next workspace.
- **Command-Option-1 … 9**: select workspace 1 … 9.
- **Command-Shift-N**: create and name a workspace.
- **Command-Shift-R**: rename the current workspace.
- **Command-Shift-A**: attach an existing tmux session to the focused pane.
- **Command-B**: focus the navigation panel.
- **Command-N**: another Ghostty window running the same viewer/library.

Out-of-range numbered shortcuts do nothing. New workspaces start empty; use
Command-T for their first tab. Attachment never renames the tab or stops its
parked ordinary shell. **Tab actions… → Return pane to shell** brings that shell back.
Command-C/V, font-size, app preferences and normal shell Control shortcuts retain
their usual behavior. Click **Shortcuts…** for the active profile's controls.

## Right-click

Right-click a tab’s row or the active tab’s detail line to open that tab's menu, even if it
was inactive. **Rename tab** is first; the menu also supports reordering, moving
to another workspace, returning to the parked shell, and closing panes/tabs.
Double-click the active tab's name or the workspace name at the top to edit it
in place. Enter saves and Escape cancels; the existing rename shortcuts still
open their usual form. The + beside the workspace name still creates a tab.
See the [inline rename guide](INLINE_RENAME.md).

Right-click the workspace header or a numbered workspace button for that workspace's options.
The rename field starts selected: typing replaces it, Enter keeps it, and Ctrl-U
clears it. Use a secondary/two-finger click; holding Shift may make Ghostty handle
the click itself instead of forwarding it to the viewer.

The **+** beside the workspace name creates a tab. The wider numbered buttons at
the bottom switch workspaces; arrows reveal additional workspaces when needed.
The workspace name is a label, with its options available by right-click or the
**Workspaces…** button. To attach a session, select its destination pane and click
the navigation panel's **Attach session…** button, separate from **Focus**. Pane borders stay
plain. The navigation panel button,
**Tab actions… → Attach session** and the attachment shortcut work on all supported versions.

## Other terminals and the existing launcher

`./run` stays independent of Ghostty. Its portable **Ctrl-g, then a key** controls
remain available in every profile:

- **t** new tab; **v / h** split right / below; **a** attach; **r** rename tab.
- **n / p** next / previous tab; **o / O** next / previous pane; **z** focus/layout.
- **w** workspace chooser; **W** new workspace; **[ / ]** previous / next workspace.
- **R** rename workspace; **s** focus navigation panel; **d** exit viewer.
- **x** close pane; **&** close tab.

The Command keys require the Ghostty profile; opening `./run` inside a normal
Ghostty window keeps Ghostty's native Command bindings. Installing the TPM plugin
alone does not remap the terminal app's keys. Other terminals can map the CSI
sequences in [integrations/ghostty.conf](../integrations/ghostty.conf) to their own
bindings; the portable prefix remains the default.

## Verification and implementation notes

The Command mapping is generated from `controls.DIRECT_SHORTCUTS`. Ghostty's
[`csi` action](https://ghostty.org/docs/config/keybind/reference#csi) sends a reserved
sequence, which the viewer's private tmux server consumes and routes over its
navigation panel action socket. The shortcut never becomes a shell command, and the outer
tmux server's keybindings are not changed. [Ghostty's configuration guide](https://ghostty.org/docs/config)
explains config files and per-launch overrides.

```sh
make check
make smoke
./ghostty --dry-run
/Applications/Ghostty.app/Contents/MacOS/ghostty +validate-config \
  --config-file="$PWD/integrations/ghostty.conf"
```

The added PTY smoke exercises direct action input, four panes, tab/workspace
navigation, selected-name replacement, right-click targets, shell persistence,
and an unchanged outer tmux server. Generated config is validated with installed
Ghostty 1.3.1. The Ghostty GUI key handling and second-instance restoration have
not been automated; PTY tests and config validation are distinct evidence.

## Launch error: `exec: exec: not found`

The first shortcut launcher incorrectly added `exec` to Ghostty's shell command.
On macOS, Ghostty itself prepends `exec -l`, so bash tried to run an executable
named `exec` and exited before the workspace viewer started. The launcher now
passes the quoted Python executable and arguments without that extra word.
[Ghostty 1.3.1's execution source](https://github.com/ghostty-org/ghostty/blob/v1.3.1/src/termio/Exec.zig#L1377)
shows the wrapper. Configuration validation alone does not execute the command;
the regression checks now include that execution step.

The same test also caught an unsuccessful exit status on **Exit viewer**. Normal
exit now detaches the private tmux client before shutting its viewer server down,
so Ghostty receives success. Unexpected server failures still return an error.

Close only the failed Ghostty surfaces showing this error, then run `./ghostty`
again from a working terminal. An already-running Ghostty instance retains its
old launch command until closed. No workspace data repair or deletion is needed:
this error occurs before the viewer starts.

Several native tabs showing the same error may be restored Ghostty tabs or extra
native surfaces using that same command. This has not been reproduced in the GUI.
Restoration preferences and saved native windows are left unchanged; the launcher
does not use `open -F`, which can discard native saved state.

## Keyboard coverage and customization

The dedicated terminal profile has 17 core action bindings plus numbered selection
for tabs and workspaces 1–9. Navigation, creating tabs/workspaces, splitting,
renaming, focus and closing are available by shortcut.

Opening a chooser by shortcut does not yet make its choices keyboard-accessible.
The session chooser accepts typed filtering but requires a mouse to pick a result.
Return pane to shell, tab reordering/transferring and empty-workspace deletion are
menu commands without a keyboard activation path. These gaps belong to the menu
navigation work; they do not require a shortcut for every individual command.

Direct actions use stable CSI codes in `tmux_workspaces/controls.py`.
You can map a different terminal key to those sequences in your terminal profile.
The portable Ctrl-g prefix and its bindings are currently fixed in code; there is
no application user-keymap file or CLI option yet. The TPM launch key is separately
configurable with `@tmux-workspaces-key`.

User-defined bindings and keyboard menu navigation are separate improvements.
Until both are verified, the README must not promise full keyboard parity,
user-customizable controls or mouse-optional workflows. Having the existing
shortcuts does not establish those claims.
