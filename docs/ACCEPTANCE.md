# Verification and known limits

The automated checks exercise the viewer through disposable shells and private
tmux servers. They verify input, saved arrangements and process ownership; they
do not certify every terminal emulator's appearance or every dependency version.
Use [TESTING.md](TESTING.md) for prerequisites and reproducible commands.

## Platform coverage

The [CI workflow](../.github/workflows/ci.yml) runs `make check`, `make smoke` and
`make package-smoke` on Ubuntu with Python 3.11 and 3.14, and macOS with Python
3.14. Linux includes a real authenticated loopback SSH scenario; macOS explicitly
skips SSH. The workflow installs tmux from each platform's package manager rather
than testing every supported tmux release.

A [recorded CI run](https://github.com/eandualem/tmux-workspaces/actions/runs/35690654042)
on 2026-09-22 passed all three jobs. The Ubuntu/Python 3.14 job passed on retry
after a sidebar-exit timeout in the Ghostty launch fixture; this is not evidence
of a retry-free run. Consult the [current CI results](https://github.com/eandualem/tmux-workspaces/actions/workflows/ci.yml)
for later revisions instead of treating this snapshot as a permanent guarantee.

Native Ghostty GUI rendering and key capture, native macOS SSH, WSL, the exact
tmux 3.3 minimum and real network latency remain unverified by this matrix.
A PTY (pseudoterminal) using Ghostty's terminfo is separate from driving its GUI.

## What the checks cover

- **Startup and failure isolation:** missing/old tmux, missing or unknown TERM,
  monochrome terminfo and unavailable curses leave a fresh library empty and
  create no runtime servers. Partial startup cleans up viewer-owned resources.
  See [startup errors](STARTUP.md).
- **Organization and input:** neutral tab names, empty new-tab/split choosers,
  inline renaming, four-pane layouts, split dragging, menus, mouse and keyboard
  navigation, and immediate or burst navigation followed by typing. Tests check
  that each marker reaches its intended shell exactly once.
- **Persistence and concurrent viewers:** names, grouping, split proportions,
  cwd, shell process IDs, foreground programs and scrollback survive viewer
  close/reopen. Windows navigate independently and detect conflicting edits.
  Invalid layouts are rejected without replacing the original database; see
  [recovery](RECOVERY.md).
- **External attachment:** exact session targets, offline references, parked
  shells, window selection, recursive-host protection and attachment cleanup.
  Viewer close preserves external sessions and their options. Fixtures include
  source sessions with automatic cleanup policies.
- **Configuration:** bounded keymap/theme validation, per-viewer snapshots,
  reduced color palettes, settings-editor save/cancel, selection copying and
  refresh with the original shell process preserved. Styling assertions inspect
  terminal output, not human visual contrast; see [themes](THEMES.md#verification).
- **Shell environment and SSH:** isolated SSH/XDG context, unchanged existing
  shell environments, disposable socket probes and a separate encrypted loopback
  SSH lifecycle test. No real user keys or agent credentials are used; see
  [shell environment](SHELL_ENVIRONMENT.md) and [SSH tests](TESTING.md#isolated-linux-and-ssh).
- **Optional providers:** ordinary launch has no Backbone imports or API reads.
  Provider failures preserve independent discovery, sanitize errors and mark
  cached metadata stale. See [the read-only contract](ARCHITECTURE.md#read-only-provider-contract).
- **Installed bundles:** archive digest validation, relocated launchers, TPM,
  input and shell preservation across two installations of the same revision.
  This does not prove cross-version schema rollback or dependency upgrades;
  see [packaging](PACKAGING.md#verify-an-installation).

## Performance and visual checks

[PERFORMANCE.md](PERFORMANCE.md) defines latency budgets, a disposable benchmark,
version-specific measurements and a real-terminal procedure. Historical quiet-host
runs met the budgets on one Apple silicon host. Those figures do not measure
native paint, SSH latency or performance on a loaded or slower machine.

To check a terminal manually, use the [separate sample library](UI_PREVIEW.md):

1. Switch tabs and workspaces by click and shortcut. Immediately type distinct
   harmless markers and verify they reach only the intended pane.
2. Open terminals, split right/below, drag separators and resize narrow/wide.
   Check focus, cursor position, names and restored split proportions.
3. Attach a disposable session, return to the parked shell and take the attachment
   offline. Confirm the saved reference remains and reconnects when available.
4. Rename tabs/workspaces, navigate menus by keyboard and check light/dark themes.
5. Detach and reopen the same library. Confirm shell process IDs, cwd and running
   programs survive. Repeat with two viewers.
6. Record the revision, terminal/version, dimensions, operating system and whether
   the connection was local or SSH. Use the full [performance procedure](PERFORMANCE.md#human-acceptance-on-the-real-terminal)
   before making latency or visual smoothness claims.

## Product limits

- Nested tmux affects sizing, scrolling and repaint. Shared writable clients can
  affect the same session's size in another window. Copying needs terminal support
  for OSC52 clipboard writes.
- Layout changes may recreate attachment clients and briefly redraw content.
  Narrow windows temporarily show one pane while retaining the saved split tree.
  There is no per-display arrangement storage.
- Processes survive viewer close only while their tmux server stays alive. A host
  reboot preserves saved names and layouts, not running processes or shell memory.
- Run on the tmux host, directly or through SSH. There is no built-in remote
  transport or native Windows runtime.
