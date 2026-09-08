# Verification and known limits

## Verified baseline — 2026-09-08

The baseline at `4cd4ec9` passed **83 unit/integration tests**, Ruff lint and
formatting, and plugin shell syntax checks through `make check`. All seven real
PTY suites in `make smoke` passed:

- Tab/workspace inline renaming: mouse input, cursor editing, Enter/Escape,
  empty-workspace editing, resize focus and no editor text leaking into shells.
- Generated Ghostty macOS command: execution through its bash wrapper, direct
  shortcuts, viewer exit status and reopening the same library.
- Direct shortcuts and context menus: one action per input, ordering, names,
  workspace/tab navigation, pane targeting, and an ordinary outer tmux server.
- Independent launch and generic attachment: zero Backbone requests with poisoned
  adapter configuration; exact session targets, parked shells and source identity.
- Mixed terminal layouts: four panes, mouse, resize, scrollback, cwd/environment,
  foreground process persistence and retained offline associations.
- Concurrent windows: independent navigation/input with shared arrangements,
  concurrent edits, conflict handling and unchanged source sessions.
- TPM: actual plugin loading and nested launch, attachment, recursion protection,
  exit/reopen and preservation of outer panes and options.

The focused inline suite also passed using `xterm-ghostty` with Ghostty's terminfo,
both before and after integration. Tests use their own libraries, private sockets
and ordinary disposable shells. No real agents or user libraries are fixtures.

The exercised environment was macOS, Python 3.14 and tmux 3.7c. Python 3.11 and
tmux 3.3 are documented floors, not separately verified combinations. Linux/WSL,
an actual SSH connection and native Ghostty GUI automation remain unverified.
PTY verification with Ghostty terminfo and its command wrapper is distinct from
native application rendering, restored-window behavior or GUI performance.

## Reproduce

From the checkout with Python, tmux and uv (for Ruff) available:

```sh
make check
make smoke
```

For a macOS Ghostty installation at its default location:

```sh
TERMINFO=/Applications/Ghostty.app/Contents/Resources/terminfo \
  python3 experiments/workspace_viewer/smoke_inline_rename.py
python3 experiments/workspace_viewer/smoke_windows.py \
  --ghostty-terminfo /Applications/Ghostty.app/Contents/Resources/terminfo
```

Substitute your Ghostty terminfo location when installed elsewhere. These commands
exercise PTYs, not GUI automation.

To try the application with a separate example library, use `./preview --terminal`
or `./preview` for macOS Ghostty. See [UI_PREVIEW.md](UI_PREVIEW.md).

1. Create and rename tabs/workspaces. Confirm attaching a session keeps the tab name.
2. Set a shell variable and change directory. Switch tabs/workspaces and return;
   check that the variable, cwd and a running foreground command remain intact.
3. Split right, below, and right again. Drag borders, focus/restore, resize, and
   switch away and back. The tab should retain its arrangement.
4. Attach a disposable session to an inactive pane after selecting it. Confirm the
   selected destination, then return that pane to its parked shell.
5. Open the same library in a second terminal. Compare independent navigation and
   shared name/layout edits. Close one viewer and verify the other still works.
6. Exit and reopen. Check arrangement, names and shell state. Close a disposable
   tab explicitly and confirm only its own ordinary shells end.

## Regressions addressed

**Navigation expansion on tab switches.** Removing all content panes temporarily let
the navigation panel expand from 28 columns to 94 or the full 160-column test
window. Retaining one content pane and batching sibling removal with width
restoration kept it at 28 columns through the same four switches. A regression
checks geometry between render operations for four-pane, single-pane and empty
layouts, along with shell PIDs. This fixes the measured expansion; it does not
claim all redraw artifacts are gone. See [INLINE_RENAME.md](INLINE_RENAME.md).

**Ghostty command launch.** Adding an extra `exec` caused its macOS bash wrapper to
try running an executable named `exec`. The launcher now passes the quoted Python
command directly. Normal viewer exit detaches its client before server shutdown
so the terminal receives success; unexpected server loss still fails.

**Input ordering and target isolation.** Synchronous shortcut acknowledgments
prevent immediately following text reaching an old shell. Attachment and inline
rename drafts retain target identities and reject conflicting peer changes rather
than applying them to a surviving item. Linked/grouped host sessions are checked
for recursive attachment by pane identity.

**Independent startup.** Generic attachment remains usable through invalid adapter
configuration and unavailable services. Ordinary startup makes no Backbone reads.
Saved session/socket associations are preserved when reopening with another source.

## Idle CPU baseline

An exploratory PTY run on 2026-09-08 measured **1.09% of one CPU with one pane**
and **0.70% with four panes**. Each sample lasted approximately ten seconds after
a one-second settling period. The host ran macOS, Python 3.14.7 and tmux 3.7c;
the PTY was 160 × 38 cells with `TERM=xterm-256color`.

CPU time was summed across the fixture's viewer, tmux servers, shell and helper
process trees, including exited-child CPU charged to parents by macOS `ps -S`.
The active process set stayed unchanged in each sample; the four-pane sample also
included the parked shell from the one-pane tab. One CPU at full use is 100%.
The observer and a native terminal GUI were outside that scope.

These short samples on one development host establish an initial observation,
not a cross-platform performance guarantee or evidence that adding panes lowers
CPU use. Coarse CPU accounting and host activity can affect the figures. A
repeatable benchmark with longer samples, separate mouse/shortcut measurements
and explicit readiness criteria remains work to complete before public release.

## Remaining limits

- Selecting an attachment, returning a pane to its shell, tab reordering/transfer,
  and empty-workspace deletion still require a mouse. The attachment search field
  is keyboard-accessible, but Enter does not activate a session.
  Core shortcuts exist; complete menu parity and user-defined keymaps do not yet.
- Nested tmux affects terminal sizing, clipboard/copy mode, scrolling and repaint.
  Shared writable clients can affect the same session's size in another window.
- Layout changes rebuild content attachment clients. The main viewer process is
  persistent and skips identical frames, but Python action helpers start per
  keyboard action and leaf wrappers start when content is rebuilt. Reducing render
  and tmux command costs is a public-release gate. Repeated measurements must
  demonstrate better tab/workspace switching for both clicks and shortcuts while
  preserving shell ownership, input ordering and layout stability.
- Narrow terminals temporarily show one pane while retaining the saved tree.
  There is no physical-display identity or per-display arrangement storage.
- Processes survive viewer exit while their tmux server lives. They cannot survive
  a host reboot or loss of that server. Names, grouping and layouts remain saved.
- Run on the tmux host, directly or through SSH. There is no built-in remote
  transport or native Windows runtime. Windows use means WSL or SSH to a Unix host.

## Provenance of the checks

The imported prototype passed 17 tests and real PTY four-pane/persistence and
concurrent-window checks. Independent startup, shortcuts, compact navigation and
inline naming expanded that suite to the baseline above. The original snapshot
is retained in local development history; attribution is in
[PROVENANCE.md](PROVENANCE.md). Results here describe tested behavior, not a release
certification or performance guarantee.
