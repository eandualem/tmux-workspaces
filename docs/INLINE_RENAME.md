# Inline tab and workspace names

Double-click renaming works for both the active tab and the current workspace.
It is included in the normal application.

## Use it

Choose **Exit** in an older viewer, then launch from the top of the checkout:

```sh
./ghostty
```

Keep any custom data-directory/source options from your usual launch command.
Your saved workspaces, tabs, layouts and ordinary shell processes persist.
Use `./run` in the current terminal instead.

- **Tab:** single-click to select it, then double-click its active name.
- **Workspace:** double-click its name at the top of the navigation panel, beside the +.
  The + still creates a tab. Workspace renaming also works when it has no tabs.
- Type to replace the selected name, or use Left/Right, Home/End, Backspace and
  Delete to edit it. Click within the field to place the cursor. Long names scroll
  horizontally; the existing 80-character limit applies.
- **Enter** saves; **Escape** or clicking elsewhere cancels the draft. Empty names
  stay in the editor until you enter a name or cancel.

Both clicks must be within 450 ms and near the same character. Double-clicking an
inactive tab selects it; double-click again on its active name to edit. Counts,
detail rows and numbered workspace buttons keep their normal click behavior.
Right-click menus and the existing rename shortcuts are also available.

The field uses terminal mouse events, like the other clickable controls. Holding
Shift can make the terminal handle a click itself. No Ghostty configuration or
shortcut changes are needed. Renaming never renames an attached external session
or ends a shell. A conflicting name change/deletion by another viewer cancels
the stale edit rather than overwriting the other viewer's change.

## Separate sample library

`./preview` opens the same UI with disposable sample attachments and its own saved
library. `./preview --terminal` uses the current terminal. Your normal viewer can
stay open. No sample library is copied into normal workspaces.

## Switching and verification

Tab switches previously removed every content pane, briefly expanding/reflowing
the navigation panel from 28 columns to as much as the full 160-column test window. The
renderer now retains one content pane and batches removal of its siblings with
restoration of the navigation panel width. Only this viewer's attachment clients are
replaced; persistent ordinary shells and external sessions stay on their servers.

The width probe now observes 28 columns throughout the same switches. A real-tmux
regression test checks the width between layout operations for four-pane,
single-pane and empty layouts, and confirms ordinary shell PIDs remain stable.
This fixes the measured expansion; content attachment clients still redraw during
switches, leaving room for further performance improvements.

Run `make check` and `make smoke` for unit and real terminal verification. The
inline suite also supports Ghostty's installed terminal definition:

```sh
TERMINFO=/Applications/Ghostty.app/Contents/Resources/terminfo \
  python3 -m tests.integration.smoke_inline_rename
```

The tests exercise input, layout geometry, resize, persistence and concurrent
edits. Native Ghostty GUI interactions are not automated here.
