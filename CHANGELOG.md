# Changelog

## 0.1.1

A small version step that collects the changes made since 0.1.0.

### New

- New tabs and splits open as a chooser in the pane itself: an ordinary shell,
  or an existing tmux session to attach, filtered as you type.
- The redesigned workspace: a sidebar with the workspace heading, tabs and
  agent roster; a one-row status footer; and popups centred over the terminals
  with rounded frames.
- Built-in editors for colors and shortcuts, a read-only shortcut reference,
  and theme presets, all under Configure….
- Windows opened by the Ghostty launcher can edit their own shortcuts.

### Improved

- The agent roster uses the height the tab list leaves and shows every active
  agent when there is room. Agents that need you come first, then those
  working. When space runs short, the label shows the total and scroll arrows.
- Tab and pane navigation, with the effective shortcuts shown as hints.
- Pane selection stays visible, and copying is explicit.
- Faster startup and lighter polling.
- Installation and first-workspace documentation.

### Fixed

- Theme changes reach every open part of a viewer at once.
- Attached sessions, attachment windows and saved layouts survive refreshes,
  resizes and viewer restarts; closing a viewer never stops an attached session.
- Split dragging, clicks on panes while an attachment starts, and input sent
  during startup are handled once, in order.
- Long file paths in popup footers keep the file name visible.

## 0.1.0

First public release.
