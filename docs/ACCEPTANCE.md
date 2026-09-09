# Verification and known limits

## Startup preflight and failure cleanup — 2026-09-08

The startup branch passed `make check`: **187 application tests and 12 benchmark
tests**, Ruff and shell syntax on macOS/Python 3.14.7/tmux 3.7c and Debian 13
aarch64/Python 3.12.14/tmux 3.5a. All eleven Linux PTY suites and ten macOS suites
passed; SSH is explicitly skipped on macOS. The final preview-module error-status
follow-up passed its subprocess regression and both platforms' unit checks.
Hosted final-head CI is blocked by the repository account's payment/spending
restriction; its jobs ran no steps.

Six real-PTY failures exercise missing/old tmux, missing/unknown TERM, monochrome
terminfo and missing Python curses. Each leaves a fresh library empty, creates no
runtime sockets and issues at most `tmux -V`. Actual Python 3.9.6 was rejected
cleanly across nine public entry paths before runtime imports. Unit regressions
cover development versions, help/diagnostic output, partial setup and cleanup
failures. Internal action/leaf helpers retain their existing lightweight path.

Linux testing also exposed a fixture-only process-exit race while reading /proc.
That fix landed on the preceding fixture branch with its own regression before
this full Linux run. Startup requirements and the limits of minimum terminfo
checks are documented in [STARTUP.md](STARTUP.md). No native GUI, WSL or WAN
behavior is established by these results.

## Owned fixtures and real SSH — 2026-09-08

The shared PTY layer now owns fresh libraries, servers and clients through setup
errors, assertions, timeouts and interruptions. Cleanup ignores socket paths in
runtime manifests and reports failures without masking the original assertion.
The existing scenarios retain their behavioral assertions, including the keymap
suite integrated from its preceding PR. The combined branch passed 164 application
and 12 benchmark tests on macOS/Python 3.14.7 and Linux/Python 3.12.14, plus all
ten PTY scenarios on Linux and nine on macOS with an explicit SSH skip. The updated
hosted matrix is blocked by the repository account's payment/spending restriction;
its jobs ran no steps. These are local results.

A nonroot Debian 13 container with Python 3.12.14, tmux 3.5a and OpenSSH 10.0p2
passed a real authenticated loopback SSH scenario: mouse and keyboard input,
returned output, resize, abrupt disconnect/reconnect, saved workspace/four-pane
arrangements, the same foreground process resuming, cwd/shell identities and
external attachment ownership. No host service, user library or credentials were
used. This supersedes the historical lack of SSH transport coverage below; it
does not establish WAN, WSL or native GUI behavior. Exact setup and current
platform scope are in [TESTING.md](TESTING.md).

## Combined navigation and input verification — 2026-09-08

The integrated package, provider, recovery, shell-context and navigation changes
passed `make check`: **127 application tests and 12 benchmark tests**, Ruff and
TPM shell syntax. All eight real-PTY suites passed on macOS/Python 3.14/tmux 3.7c.
The routing suite delivers 64 immediate and burst commands across one/four-pane
tabs and workspaces, using both clicks and direct shortcuts; each marker reaches
only its intended shell and original shell PIDs survive. Native content mouse
forwarding, word/line selection, bracketed paste and same-write inline renaming
retain dedicated coverage.

Review regressions cover newline-containing cwd, fragmented Unicode input,
metadata-only reuse, dead attachment clients and stale focus. Event-local pane
snapshots are discarded after mutations and exceptions. The environment fixture
checks shell handoff independently of system profile overrides; other suites
continue to launch ordinary login shells. Normal exit and delayed detach succeed;
unexpected private-server loss still fails.

The navigation budgets and the runs that met them are described in
[PERFORMANCE.md](PERFORMANCE.md), together with the paired comparison showing no
overall slowdown from the later theme, keyboard, refresh and packaging work: seven
of eight paths improved at the median, and four-pane shortcut-workspace rose 8.9%
there while improving at p95. PTY readiness and routing do not prove native
Ghostty paint, actual SSH transport, or WSL behavior. The manual click-and-typing
acceptance pass in [PERFORMANCE.md](PERFORMANCE.md#human-acceptance-on-the-real-terminal)
is the owner's, and is recorded separately from these measurements.


## Hosted terminal coverage — 2026-09-08

The merged package version passed `make check` (89 tests) and all seven real-PTY
suites on Ubuntu with Python 3.11 and 3.14, and on macOS with Python 3.14.
The Linux jobs exercise tmux 3.4; compatibility coverage includes literal dollar
signs in TPM option values. CI configuration and the review/merge procedure live
in [DEVELOPMENT.md](DEVELOPMENT.md).

These jobs use disposable local sockets and ordinary shells. They establish Linux
and macOS terminal integration coverage, not actual SSH transport, WSL or native
Ghostty GUI behavior. The Ghostty command harness uses the macOS login-shell
wrapper on macOS and ordinary shell execution on Linux.
## Layout validation and recovery — 2026-09-08

`make check` passed **99 tests**, Ruff and TPM shell checks; all seven real PTY
suites in `make smoke` passed on macOS with Python 3.14 and tmux 3.7c. New tests
reject malformed JSON, duplicate fields/identities, missing fields, invalid
navigation, split geometry, source references, unsupported versions and excessive
nesting. Rejected loads leave the database byte-for-byte unchanged; invalid legacy
records create no tables. Valid legacy migration retains the exact earlier record,
including offline attachment references.

Follow-up regressions retain the existing default for omitted split ratios and
reject oversized serialized writes before either saving or migrating, preserving
the original database bytes. The full check suite was rerun after these changes;
the seven PTY suites passed immediately before this persistence-only follow-up.

An invalid library blocks launch before any tmux process starts, including demo
launch. Corruption introduced by another writer blocks refresh and save while
retaining the in-memory arrangement and the original database bytes. Existing
concurrent-edit and conflict regressions still pass. Recovery remains an explicit
backup-and-inspect workflow described in [RECOVERY.md](RECOVERY.md); there is no
automatic repair command. Linux and a real damaged user library were not fixtures.

## Read-only provider isolation — 2026-09-08

The adapter extraction passed **100 tests** and all seven real PTY suites on macOS,
Python 3.14 and tmux 3.7c. Contract tests cover default startup importing neither
Backbone nor demo, generic discovery using only the chosen socket, overlays from
an independently supplied provider, stale timestamps/cache/recovery, consumer
mutation isolation, malformed payloads and transport failures, no writes, GET-only
requests, and the existing loopback/no-proxy/no-redirect restrictions. Additional
provider-boundary regressions verify that unexpected exceptions and malformed
snapshots cannot prevent independent provider progress, discard last-good metadata,
expose exception details, or swallow cancellation; subsequent reads recover.

Generic session availability, offline associations, demo attachment identity,
source-socket persistence, concurrent windows and user-owned shell behavior remain
covered by the PTY suites. Snapshot freshness metadata is internal; this change
adds no status notifications or claims of external session ownership.


## SSH and XDG shell context — 2026-09-08

`make check` passed **91 tests**, Ruff and TPM shell checks. All eight real PTY
suites in `make smoke` passed, including the new environment suite. The new real PTY
environment suite uses synthetic launcher secrets, a temporary HOME and two mock
Unix sockets. Ordinary shell commands connect to the configured socket and read a
file through `XDG_CONFIG_HOME`. Reopening supplies changed paths to newly created
tabs; a concurrent viewer with no SSH/XDG context creates shells with those
variables absent. Existing shells retain their PID, cwd and original environment.

External attachment preserves the source session's full environment, including
its own SSH-agent socket. The ordinary-shell allowlist and explicit reconnect
instructions are documented in [SHELL_ENVIRONMENT.md](SHELL_ENVIRONMENT.md).
These tests use macOS, Python 3.14.7 and tmux 3.7c. They verify the environment
plumbing with mock sockets, not an actual SSH connection or agent credentials.


## Package extraction — 2026-09-08

The package extraction passed **87 tests** plus Ruff and TPM shell checks.
All seven real PTY suites passed from a fresh checkout copy whose directory name
contained spaces, with no Git metadata or development memory. The relocated tests
covered standalone and TPM launch, generic/optional sources, inline editing,
shortcuts, four-pane layouts, concurrent windows and persistence.

Compatibility regressions execute the old viewer/plugin/Ghostty/preview command
paths and the standalone symlink from another working directory. A real action
receiver verifies that an old viewer's helper still delivers commands. Another
regression confirms action dispatch imports no curses, SQLite or HTTP adapter.
A disposable pre-refactor viewer was also left running while its checkout files
were replaced. Its existing synchronous shortcut binding accepted rename text in
the same PTY write, then started a new leaf helper. Old-viewer exit and new-viewer
reopen preserved shell PIDs/cwd, split/name/attachment state, and the external
fixture's pane PID/cwd/layout (fixed window size excluded ordinary attach resizing).

A locally built wheel was installed without dependencies into a disposable Python
3.13 environment; a real PTY exercised its console launcher, new tab, split, action
and attachment helpers, and clean exit with ordinary shells retained. No registry
publication or user installation was performed. Full source suites used macOS,
Python 3.14 and tmux 3.7c; Linux/SSH and native Ghostty GUI coverage remain separate.

See [architecture and lifecycle ownership](ARCHITECTURE.md).

## Original verified baseline — 2026-09-08

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

That original baseline was exercised on macOS, Python 3.14 and tmux 3.7c.
Current Linux/Python 3.11 coverage is recorded above. The exact tmux 3.3 floor,
WSL, an actual SSH connection and native Ghostty GUI automation remain unverified.
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
  python3 -m tests.integration.smoke_inline_rename
python3 -m tests.integration.smoke_windows \
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
CPU use. Coarse CPU accounting and host activity can affect the figures. The
repeatable benchmark that followed supplies longer samples, separate mouse and
shortcut measurements and explicit readiness criteria; idle CPU still lacks a
measured Linux baseline and any figure from a machine other than this one.

## User keymap verification

`make smoke` includes `tests.integration.smoke_keymaps`. On isolated PTYs it
checks a custom prefix and action map, same-write action plus immediate typing,
a burst of tab switches with unique intended-shell output, rename cancellation,
literal double-prefix delivery, prefix Escape, and removed prefix/direct keys.
Negative checks observe processed input and inspect the private binding table.
Editing a file preserves existing viewer keys/help; a new viewer takes the new
map, and `--no-keymap` restores defaults despite an invalid discovered file.
An ordinary hosting tmux keeps its keys, options and original process identities.

Unit tests cover precedence, bounded validation, aliases/collisions, disabled
bindings, generated help and profiles, snapshot round trips, startup errors before
library creation, and shell-safe Ghostty argument transport. Ghostty 1.3.1's CLI
validator accepts a generated custom profile and rejects an intentionally invalid
action. These checks do not automate native physical-key interception, operating
system conflicts, actual SSH transport or the menu-navigation work.

## Keyboard menu coverage

The keyboard menu work adds **Ctrl-g m** for tab options and **Ctrl-g M** for
workspace options, alongside **Ctrl-g a** for Attach and **Ctrl-g w** for the
workspace chooser. All option lists support Up/Down and Ctrl-p/Ctrl-n, with
Home/End and PageUp/PageDown for longer lists. Enter activates the visible
selection; Escape and Back close the whole overlay and return to the pane.
An empty workspace keeps navigation focus. Menu movement keys are fixed; the
existing keymap feature customizes the viewer actions that open these menus.

`tests.integration.smoke_menus` sends keyboard input through a real PTY with
private demo sessions. It checks filtering, empty-result Enter, attachment and
return to the same parked shell, preserved tab/pane identity, external-session
survival, Escape focus, resize with an open chooser, tab reorder/transfer,
empty-workspace deletion and an overflowing workspace list. The selected row is
observed in a styled terminal capture. Existing mouse and concurrent-target
regressions remain in their original suites.

Unit regressions cover selection identity when sessions change or workspace
names repeat, refusing vanished or newly arrived unseen targets, filter resets,
scrolling and callback activation, action registration and configurable entry
bindings. Attachment and return-to-shell revalidate the saved destination before
changing it. The keymap feature landed first; menu navigation supplies the
previously missing keyboard routes for these commands. This is evidence for
those implemented workflows, not for arbitrary terminal or operating-system
shortcut capture or native GUI parity.

## Remaining limits

- Nested tmux affects terminal sizing, clipboard/copy mode, scrolling and repaint.
  Shared writable clients can affect the same session's size in another window.
- Layout changes rebuild content attachment clients. The main viewer process is
  persistent and skips identical frames, but Python action helpers start per
  keyboard action and leaf wrappers start when content is rebuilt. Repeated
  measurements met the switching budgets for both clicks and shortcuts while
  preserving shell ownership, input ordering and layout stability. They were taken
  on one idle development machine; a loaded or slower host will be slower.
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
