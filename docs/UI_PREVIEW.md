# Sample workspace library

The compact navigation UI is included in the normal application. To load an
update, choose **Exit** and relaunch from the checkout with the same data/source
options: `./run`, or `./ghostty` on macOS. Saved arrangements and running shells
remain available.

## Try a separate sample

From the top of the checkout:

```sh
./preview --terminal
```

Use `./preview` for macOS Ghostty instead. The example contains six tabs in
Development, three workspaces and a four-pane tab. Two panes attach to disposable
demo shells. Existing viewers can stay open; no live agents are used.

The sample library lives under `.backbone/ui-preview/demo/` in the checkout.
Repeated launch keeps sample edits and ordinary shells while recreating demo
attachment fixtures. No sample arrangements are copied into your normal library.
Do not remove a sample library or its checkout while you still need its shells.

## Organization

Tabs use one numbered row with a pane count on the right; the number and count
are muted on rows at rest, and the active tab is one filled row with a ▶ marker
and one extra detail row. Long names end with an ellipsis. Quiet lowercase
section labels — `tabs`, `actions`, `workspaces` — group tab navigation,
pane/tab actions and workspace switching instead of ruled lines. Selecting
another tab can move the following rows by one line as the detail row changes
position.

Attach session is independent of Focus. Shortcuts and Exit share a row. The
Workspaces… control opens workspace options; numbered workspace buttons switch
purposes, the selected one drawn as a filled block, and the accent + beside them
creates a workspace. The top + creates a tab. Double-click the active tab name or
top workspace name to edit it in place. The bottom row is the status line: an
accent light and `Layouts saved` while idle, a message otherwise.

Content panes use plain single-line borders, retaining the full available
content height, colored from the viewer theme: the focused pane's border in the
accent color, the others muted. The navigation panel and attachment shortcut
both target the selected pane. Colors… in the panel switches presets and edits
each role; see [THEMES.md](THEMES.md).

## Try these interactions

1. Switch between tabs and workspaces using shortcuts and clicks.
2. Create a tab, split right/below, type into shells and rename both kinds of name.
3. Select an inactive pane, attach reviewer, then return to its parked shell.
4. Try right-click options, inline Enter/Escape, and a long name.
5. Drag borders and resize smaller/larger; Focus/Layout restores the saved splits.
6. Exit and launch the sample again; check the arrangement and ordinary shell state.

Very short navigation panels (under 16 rows) show an enlarge hint while core
terminal shortcuts remain available. Complete keyboard menu navigation is pending.

See [ACCEPTANCE.md](ACCEPTANCE.md) for actual PTY verification and platform limits.
Native Ghostty GUI behavior is a separate manual check.
