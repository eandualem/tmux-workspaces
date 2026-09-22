# Inline tab and workspace names

Double-click renaming works for both the active tab and the current workspace.
It is included in the normal application.

## Use it

In a running viewer, use the mouse or the rename shortcuts (**Ctrl-g**, then
**r** for the tab, **R** for the workspace).

- **Tab:** single-click to select it, then double-click its active name.
- **Workspace:** double-click its name at the top of the navigation panel.
  The + on the tabs row creates a tab. Workspace renaming also works when it has no tabs.
- Type to replace the selected name, or use Left/Right, Home/End, Backspace and
  Delete to edit it. Click within the field to place the cursor. Long names scroll
  horizontally; the existing 80-character limit applies.
- **Enter** saves; **Escape** or clicking elsewhere cancels the draft. Empty names
  stay in the editor until you enter a name or cancel.

Both clicks must be within 450 ms and near the same character. Double-clicking an
inactive tab selects it; double-click again on its active name to edit. Counts
and workspace icon/number buttons keep their normal click behavior.
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

## Verification

Run `make check` and `make smoke` for unit and real terminal verification. The
inline suite also supports Ghostty's installed terminal definition:

```sh
TERMINFO=/Applications/Ghostty.app/Contents/Resources/terminfo \
  python3 -m tests.integration.smoke_inline_rename
```

The tests exercise input, layout geometry, resize, persistence and concurrent
edits. Native Ghostty GUI interactions are not automated here.
