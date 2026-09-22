# Sample workspace library

Explore the viewer with a separate sample arrangement and disposable attachment
sessions. Your normal library and existing viewers remain independent.

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

The panel is 22 columns wide and flush with the left edge; one thin line
separates it from the content. Its first row is the workspace heading on the
header bar: the workspace's icon when one is set, its name, and a ▾ chooser at
the right. A blank row follows, then the `TABS` label with the + that adds a
tab, then one row per tab: its number, its name, and one ▮ per pane. The
selected tab is one filled row with its name in bold and a ⋯ at the right end
opening the tab menu. Long names end with an ellipsis; nothing repeats what a
tab's panes hold.

The bottom, from the last row up: the workspace icon row (one three-cell slot
per workspace with the current one filled and an … slot when the row is full),
Configure… (Edit theme…, Edit shortcuts…, View shortcuts…, Refresh viewer…,
then Show agents and Agent status…, then Detach after a rule) and a blank row.
Above that, when a state-reporting source is connected and the setting is on,
the `AGENTS` section lists active agents one per row — a state symbol in a
fixed slot and the name — bounded to six rows with a count and scroll arrows
beyond that, or one quiet row saying *No active agents* or *Roster unavailable*.
Menus take the place of the tab list, headed by ‹ Back, with each row's prefix
key at its right end. Double-click the active tab name or the workspace name to
edit it in place; the hint takes the blank row under the heading.

The status row along the bottom of the window, directly under a thin line
(see [the status band](THEMES.md#beyond-the-sidebar)) and with one cell of
padding on each side, names the workspace, tab and
pane on the left, the keys for the current mode in the centre, and the agents
toggle and the saved-state light on the right. Content panes sit on the surface
color beside the panel; split panes are separated by one thin line in the
outline color, down or across. No border marks the focused pane. Edit theme…
in Configure edits presets and each role and ground; see [THEMES.md](THEMES.md).

## Try these interactions

1. Switch between tabs and workspaces using shortcuts and clicks.
2. Create a tab, split right/below, type into shells and rename both kinds of name.
3. Select an inactive pane, attach reviewer, then return to its parked shell.
4. Try right-click options, inline Enter/Escape, and a long name.
5. Drag the separators and resize smaller/larger; **Focus one pane / Restore
   layout** toggles the saved splits.
6. Detach and launch the sample again; check the arrangement and ordinary shell state.

Very short navigation panels (under 16 rows) show an enlarge hint while core
terminal shortcuts remain available. In menus, arrows move the selection, Enter
activates it and Escape returns to the pane; see [keyboard controls](SHORTCUTS.md).

See [ACCEPTANCE.md](ACCEPTANCE.md) for actual PTY verification and platform limits.
Native Ghostty GUI behavior is a separate manual check.
