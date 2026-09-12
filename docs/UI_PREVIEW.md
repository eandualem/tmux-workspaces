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
that ends in its pane count and a ⋯ opening the tab menu. Long names end with
an ellipsis. Every tab is one row; nothing repeats what its panes hold.

The panel is a rounded, outlined rectangle inset one cell inside its pane. Its
first row is the workspace heading — the workspace's icon when one is set,
its name, and a ▾ chooser at the right; a blank row follows, then the `tabs`
label with the + that adds a tab, then the tab rows. The bottom, from the
outline up: a blank row, the workspace icon row (one three-cell slot per
workspace with the current one filled and an … slot when the row is full), a
blank row, Configure… (Edit theme…, Edit shortcuts…, View shortcuts…, Refresh viewer…, Show agent
status, Agent status…, then Detach after a rule), and a row that carries a
message only while there is something to say. Above that, when a
state-reporting source is connected and the setting is on, the `Agents`
section lists active agents one per row — a state symbol in a fixed slot and
the name — bounded to six rows with a count and scroll arrows beyond that, or
one quiet row saying *No active agents* or *Roster unavailable*.
Double-click the active tab name or the workspace name to edit it in place;
the hint takes the blank row under the heading.

Content panes keep the full available content height and sit on the surface
color beside the panel; each is padded by one blank column on either side, and
split panes are separated by one thin line in the outline color, down or
across. No border
marks the focused pane. Edit theme… in Configure edits presets and each
role and ground; see [THEMES.md](THEMES.md).

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
