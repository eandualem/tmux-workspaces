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

Use **Ctrl-g, then f** to open **Refresh viewer**. A dedicated Ghostty instance
needs manual reopening to apply its terminal profile; the refresh flow supplies
an exact reopen command. Refresh uses a fresh viewer interpreter and picks up
already-installed Python dependency changes; it does not install them. Changes to
the interpreter or launcher installation still need reopening with the intended
launcher. See [refresh scope and recovery](REFRESH.md).
Do not close its tabs to upgrade: **Exit viewer** preserves shells and programs;
closing a pane or tab ends that pane/tab's ordinary shells. Existing views can also
remain open alongside the new window; they share arrangements and shells.

## Tabs and splits

The shipped map follows Ghostty's usual keys, with the actions applied to saved workspaces:

- **Command-T**: new tab, opening as a chooser for an ordinary shell or a session.
- **Command-D**: split right, with a vertical divider; the new pane opens as a chooser.
- **Command-Shift-D**: split below, with a horizontal divider; the new pane opens as a chooser.
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

`./run` stays independent of Ghostty. Its shipped **Ctrl-g, then a key** controls
remain available in every profile unless overridden by a user keymap:

- **t** new tab; **v / h** split right / below; **a** attach; **r** rename tab.
- **n / p** next / previous tab; **o / O** next / previous pane; **z** focus/layout.
- **w** workspace chooser; **W** new workspace; **[ / ]** previous / next workspace.
- **R** rename workspace; **s** focus navigation panel; **f** refresh viewer; **d** exit viewer.
- **x** close pane; **&** close tab.
- **m / M** tab options / workspace options.

If an existing keymap uses **f** for another action or as its prefix, that setting
takes precedence over the new refresh default. **Refresh viewer** remains available
in **Shortcuts…**, even without a key binding.

The Command keys require the Ghostty profile; opening `./run` inside a normal
Ghostty window keeps Ghostty's native Command bindings. Installing the TPM plugin
alone does not remap the terminal app's keys. Other terminals can map the CSI
sequences in [integrations/ghostty.conf](../integrations/ghostty.conf) to their own
bindings; the portable prefix remains the default.

## Verification and implementation notes

The terminal mapping is generated from the effective `keymap.Keymap`. Ghostty's
[`csi` action](https://ghostty.org/docs/config/keybind/reference#csi) sends a reserved
sequence, which the viewer's private tmux server consumes and routes over its
navigation panel action socket. The shortcut never becomes a shell command, and the outer
tmux server's keybindings are not changed. [Ghostty's configuration guide](https://ghostty.org/docs/config)
explains config files and per-launch overrides.

Navigation-panel clicks also wait for the selected tab or workspace to take focus
before following text is released. Rapid clicks and double-click renaming use the
same ordering; content clicks, selection, scrolling and right-clicks retain their
tmux behavior. The PTY suites check immediate typing and burst navigation in
one- and four-pane arrangements, including unique delivery to the intended shell.

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

Open **Attach** with **Ctrl-g a**, **Tab options** with **Ctrl-g m**, or
**Workspace options** with **Ctrl-g M**. **Ctrl-g w** opens the workspace chooser.
Inside any chooser or option list, **Up / Down** or **Ctrl-p / Ctrl-n** moves the
highlight; **Enter** activates that row. **Home / End** selects the first / last
row; **PageUp / PageDown** moves through longer lists. The list scrolls to keep
the selection visible. **Escape** or **Back** closes the whole menu and returns to the pane,
including from a nested chooser; an empty workspace keeps navigation focus.

Type in Attach to filter session names without case sensitivity. Editing the
filter selects its first match; no matches means Enter does nothing. **Ctrl-u**
clears the filter. Tab options provide **Return pane to shell**, tab reordering
and transfer. Workspace options provide **Delete empty workspace**; a workspace
with tabs cannot be deleted. These commands need no individual shortcut.
Menu input belongs to the navigation panel while the menu has focus.

## Editing shortcuts in the viewer

Open **Shortcuts** from the sidebar and choose **Edit shortcuts…**. Each action
appears twice, once for its prefix key and once for its terminal shortcut, with
the keys it currently holds. **Enter** types a key, **c** captures one, **d**
restores the shipped keys for that row, **u** unbinds it, **a** applies and
**Escape** leaves without changing anything. A row a save would write is marked.

**Capture works for prefix keys only.** Those reach the viewer as ordinary keys,
so pressing one names it. A terminal shortcut never arrives as a key: the
terminal converts it to a private sequence first, so pressing the combination
could only report the mapping already in force. The editor says so and asks you
to type the trigger instead, using the `direct` grammar below.

Taking a key that is already in use is always asked first, naming the action
that holds it. Answering yes removes it from that action; answering no keeps
both as they were. The key is released wherever it actually lives, which is not
always the row you are editing: `ctrl+t` and `C-t` are the same physical key
spelled two ways, and only one of those spellings is in your file. The prefix key
itself and Escape are reserved and cannot be reassigned.

Saving requires a file to save to. That is the file the launch selected: the one
named by `--keymap PATH` or `TMUX_WORKSPACES_KEYMAP`, or otherwise
`~/.config/tmux-workspaces/keymap.toml`, which the first save creates. A window
opened through `./ghostty` runs a snapshot of that file's keys and still edits
the file itself. Only a viewer started with `--no-keymap` has nowhere to save,
and it says so rather than inventing a path. Saving rewrites the file from the
effective map, so comments and hand formatting are not preserved — the editor
warns before you save when the file has any. Concurrent edits, read-only files,
symbolic links and unwritable directories are all refused with the reason, and
your shortcuts stay as they are.

**Nothing reloads.** A save changes the file. Prefix keys apply to viewers
opened afterwards, and terminal shortcuts apply to a fresh `./ghostty` instance,
for the reasons in the last paragraph of the next section. The editor states
this when it saves rather than implying the new keys are already live.

## User keymaps

An optional TOML file changes existing action bindings without editing source.
Configuration is selected in this order (one file, not several merged files):

1. `--no-keymap` uses the shipped map and skips configuration files.
2. `--keymap PATH` selects an explicit file.
3. `$TMUX_WORKSPACES_KEYMAP` selects a file when no CLI path is given.
4. `$XDG_CONFIG_HOME/tmux-workspaces/keymap.toml`, otherwise
   `~/.config/tmux-workspaces/keymap.toml`.

A missing implicit default is fine. An explicit missing file, unreadable file,
malformed TOML, unknown action or unsupported/duplicate key is a startup error
before terminals are created. Files are limited to 64 KiB. Relative file paths
resolve against the invoking directory; `~/` uses the invoking home directory.
`--keymap` and `--no-keymap` cannot be combined.

For example, save this as `keys.toml`:

```toml
prefix = "C-a"

[bindings]
new-tab = ["u"]
rename-tab = ["e"]
close-pane = []

[direct]
new-tab = ["super+alt+t"]
close-pane = []
```

Then launch or inspect it:

```sh
./run --keymap keys.toml
./ghostty --keymap keys.toml
./run --keymap keys.toml --print-keymap help
./run --keymap keys.toml --print-keymap ghostty
./run --no-keymap --print-keymap toml
```

Each specified action list replaces that action's defaults in that section;
`[]` disables it there. Omitted actions retain their defaults. In this example,
Ctrl-a then u creates a tab, Ctrl-a then e renames it, and the former t/c and r
bindings are removed. Command-Option-T creates a tab in the dedicated Ghostty
instance. Closing a pane is disabled by both keyboard routes; mouse actions are
unchanged. To move a key already assigned to another action, clear or rebind that
other action too. Duplicates, including aliases such as C-i and Tab, are errors.

For compatibility with earlier keymaps, the new default **m / M** menu bindings
yield when an existing file assigns either key to another action or uses it as
the prefix, provided that menu action is omitted from `[bindings]`. Explicit
`tab-options` or `workspace-options` entries follow the normal collision rules;
two explicit assignments to the same key remain an error. User files are never
rewritten to resolve a conflict.

The complete shipped configuration is [integrations/keymap.toml](../integrations/keymap.toml).
Its action names are stable identifiers. Both sections accept every listed action,
including numbered selection, `workspaces`, `tab-options`, `workspace-options`
and `quit`; each action accepts up to
32 keys per section. Configured keys are data, never shell commands.

`bindings` uses a bounded subset of tmux notation: printable ASCII keys (except
semicolon and backslash), named keys `Space`, `Enter`, `Tab`, `BSpace`, `Escape`,
`Up`, `Down`, `Left`, `Right`, `Home`, `End`, `PageUp`, `PageDown`, `Insert`, `Delete`,
`BTab`, `F1`–`F12`, and `C-`/`M-` modifiers. Use uppercase letters for shifted
letters; `C-a` means Control-a and `M-Left` means Alt-Left. Indistinguishable
control aliases are canonicalized. Escape is reserved for cancellation, and
pressing the configured prefix twice sends the literal prefix to the terminal.
Those two keys cannot also be assigned to an action in the prefix table.
Choose a prefix the hosting tmux and terminal do not consume first.

`direct` describes terminal triggers, not tmux prefix keys. It accepts lowercase
letters/digits, `f1`–`f25`, named punctuation (`bracket_left`, `bracket_right`,
`apostrophe`, `backslash`, `comma`, `equal`, `grave_accent`, `minus`, `period`,
`semicolon`, `slash`), and named navigation keys (`space`, `enter`, `tab`,
`backspace`, `escape`, `insert`, `delete`, `left`, `right`, `up`, `down`, `page_up`,
`page_down`, `home`, `end`). Combine with `super`, `ctrl`, `alt`, `shift`, including
at least one of super/ctrl/alt. Global bindings, key sequences and arbitrary
Ghostty actions are outside this format. Known direct Ctrl/Alt combinations that
shadow the configured prefix, cancellation or prefix-table keys are rejected.
Validation cannot discover every OS, window-manager or hosting-tmux conflict.

The dedicated Ghostty launcher applies generated bindings through per-launch CLI
options, after the normal appearance configuration. It consumes former shipped
triggers with Ghostty's `ignore` action when they are disabled or moved, preventing
an old Command-T/W from unexpectedly creating or closing native surfaces. A
trigger reassigned to another action uses its new mapping. Unrelated bindings
remain unchanged. The launcher does not edit or reload normal Ghostty windows.
See [Ghostty's keybinding actions](https://ghostty.org/docs/config/keybind/reference).

`./run` does not configure a terminal emulator. In another terminal, translate the
output of `--print-keymap ghostty` into that terminal's own trigger-to-CSI settings.
A terminal consumes the physical key and sends the stable CSI action sequence;
only the private viewer server interprets that sequence. An action with an empty
`direct` list no longer registers its sequence, so remove stale mappings in other
terminals too. Prefix bindings change only the viewer's private tmux server; the
TPM launch key remains independently configurable through `@tmux-workspaces-key`.

The effective map is validated once and snapshotted per viewer. On-screen
Shortcuts and CLI help are generated from it, including aliases and numbered
selection; long help rows wrap and scroll. `--print-keymap toml` emits the complete
effective map, and `--print-keymap ghostty` emits exact triggers and CSI codes.
Diagnostic printing requires no terminal or tmux server and creates no library.
After editing configuration, launch a fresh viewer. New surfaces in an existing
Ghostty instance retain that instance's map so their help matches its terminal
profile; start a fresh `./ghostty` instance to apply a new map. No hot reload is
performed. Existing shells and saved arrangements remain independent of keymaps.

User keymaps and keyboard menu navigation are separate features. The keymap
feature landed first; menu navigation supplies the previously missing keyboard
routes for attachment and option commands. Menu movement and filtering keys are
fixed; the TOML map customizes viewer action bindings, including the actions that
open menus. Verified coverage and platform limits are recorded in
[ACCEPTANCE.md](ACCEPTANCE.md).
