# Product contract and baseline

The workspace layer organizes terminals by purpose. A workspace contains named
tabs; each tab owns its complete split layout. The user chooses the organization,
and attaching a session does not define or rename that organization.

## Required behavior

1. New tabs get neutral random names and open empty: the pane offers an ordinary
   interactive shell or an existing session, and runs whichever is chosen. Splits
   create shells. Every pane can run an ordinary terminal program.
2. Tabs can be named, reordered, moved between workspaces and closed. Workspaces
   can be named and switched; a tab's split layout belongs to that tab.
3. Existing tmux sessions attach optionally. Session names and source sockets are
   retained offline. Attachment preserves the tab name and parked ordinary shell.
4. Navigation, resize and viewer exit preserve underlying shells and foreground
   programs. Explicit pane/tab close may end its own shells, but never an external
   session. Host reboot cannot preserve running processes; saved arrangements remain.
5. Multiple viewer windows share saved arrangements with independent navigation.
   Concurrent edits are merged or surfaced as conflicts, never silently redirected.
6. The core works with Python and tmux, without a browser, Ghostty, an agent manager
   or a notification service. A normal launch performs no Backbone config/API reads.
7. Backbone is an optional read-only adapter. Adapter failure does not prevent
   generic attachment or ordinary terminal use. No agent lifecycle ownership moves.
8. Both standalone and TPM entry points launch the same application. Packaging
   preserves the isolation of user sessions and the existing tmux configuration.
9. Document keyboard and mouse paths, defaults, exact test commands and limitations.
   Distinguish verified platforms from expected portability.

## Implementation invariants

- Python 3.11+ standard library, curses navigation and tmux 3.3+.
- Each viewer owns a private display server. A separate per-library server owns
  ordinary shells; attached external sessions stay on their original server.
- Version-2 split trees use stable leaf IDs, optional `agent` (the historical field
  name for any attached session), source socket and cwd. Preserve compatibility.
- SQLite stores shared arrangements, with three-way merge and independent per-window
  navigation. Migration preserves earlier tables for recovery.
- If a peer changes the active pane or attachment, focus moves to the navigation
  panel to prevent input landing in a different terminal. `pane_last` handles a
  click/polling focus race.
- Exact session targets are `=name:` including the trailing colon. Display labels
  must stay separate from exact tmux identifiers.
- Ordinary shells clear private TMUX/TMUX_PANE identity. Preserve terminal
  definitions through TERMINFO/TERMINFO_DIRS and avoid forwarding launcher secrets.
- tmux owns `<socket>.lock`; application startup locking uses `.viewer-lock`.
- Use short private socket paths within Unix socket length limits.
- Nested tmux is an implementation tradeoff. A plugin does not itself remove the
  extra layer or prove better rendering, clipboard behavior or performance.

## Verification

Run `make check` and isolated real-PTY `make smoke` suites. Exercise mouse and
keyboard input, four-pane layouts, attachment targets, concurrent windows,
resize, shell persistence and cleanup using disposable shells only.

See [ACCEPTANCE.md](ACCEPTANCE.md) for results and current limitations, and
[PROVENANCE.md](PROVENANCE.md) for the original prototype attribution.
