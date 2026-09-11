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

The footer is one plain list, one control per row: Tab actions…, Shortcuts,
Colors…, Exit, then Workspaces… and the numbered workspace buttons, the
selected one drawn as a filled block, with the accent + beside them creating a
workspace. Splits, focus, pane cycling and attaching live in Tab actions…; a
split opens as the same chooser as a new tab. The top + creates a tab.
Double-click the active tab name or top workspace name to edit it in place. The
bottom row is the status line: an accent light and `Layouts saved` while idle,
a message otherwise.

Content panes keep the full available content height and are set apart from
the panel and from each other by a band of the panel's own color rather than
ruled borders; no border marks the focused pane. When the terminal has
answered the background-color query, each pane's contents are inset by one
blank column on each side; the sample launcher can force it with
`--terminal-background` on `./run`. Tab actions… and the
attachment shortcut both target the selected pane. Colors… in the panel
switches presets and edits each role; see [THEMES.md](THEMES.md).

## Try these interactions

1. Switch between tabs and workspaces using shortcuts and clicks.
2. Create a tab, split right/below, type into shells and rename both kinds of name.
3. Select an inactive pane, attach reviewer, then return to its parked shell.
4. Try right-click options, inline Enter/Escape, and a long name.
5. Drag the gaps and resize smaller/larger; Focus pane/Show layout restores the saved splits.
6. Exit and launch the sample again; check the arrangement and ordinary shell state.

Very short navigation panels (under 16 rows) show an enlarge hint while core
terminal shortcuts remain available. Complete keyboard menu navigation is pending.

See [ACCEPTANCE.md](ACCEPTANCE.md) for actual PTY verification and platform limits.
Native Ghostty GUI behavior is a separate manual check.
