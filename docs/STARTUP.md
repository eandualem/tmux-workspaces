# Startup requirements and errors

The viewer requires Python 3.11+, tmux 3.3+ and an interactive Unix terminal with
usable terminfo and Python curses support. The source, installed console, module,
TPM helper and retained legacy launchers reject an older Python before importing
the application. For example:

```text
tmux-workspaces: Python 3.9.6 is unsupported; Python 3.11+ is required. Install Python 3.11 or newer and use it to run this command.
Workspace viewer: tmux 3.2a detected; tmux 3.3 or newer is required. Upgrade tmux.
```

Run with the newer interpreter explicitly if `python3` still selects an old one:

```sh
python3.14 ./run
```

A normal viewer launch runs `tmux -V`, which does not connect to a server. Patch
letters and numbered development suffixes are accepted when their major/minor
version meets the floor; an unnumbered build cannot establish compatibility.
A missing executable, failed version command or unknown version reports a remedy.
These checks do not widen the supported version range.

## Terminal checks

Before opening the layout store or creating private servers, startup requires
terminal input/output, a nonempty `TERM`, Python curses APIs, and a usable terminfo
entry with at least eight colors, six color pairs, cursor positioning, screen
clearing and attribute reset. The existing eight-color palette remains supported;
256 colors are not required. The probe reads terminfo without entering the screen
or changing terminal modes. It does not contact an existing tmux session.

If `TERM` is unknown, install the terminal's matching terminfo on the host running
the viewer, including an SSH host. Preserve the correct `TERM`, `TERMINFO` and
`TERMINFO_DIRS` supplied by the terminal. A monochrome entry such as `vt100` cannot
provide the current UI. Use a color-capable terminal and matching entry rather
than assigning an arbitrary terminal name. If Python lacks curses, use an
interpreter built with ncurses support; this is not a pip runtime dependency.

These are minimum checks, not a complete terminal-emulator certification. tmux's
internal terminal and runtime curses behavior are evaluated in the separate UI
process. A later curses failure reports a concise terminfo/ncurses remedy to the
launcher. Partial setup closes its action socket, source reader, store and private
display/demo servers. Cleanup attempts all registered resources and retains the
original startup error if cleanup also fails. Persistent ordinary shells and
attached external sessions are not cleanup targets.

## Ghostty and diagnostic commands

`./ghostty` requires macOS and an executable Ghostty application bundle. It checks
tmux and Python curses availability before opening the app. Missing/unsupported
launches explain how to use `./run` in the current terminal; `--ghostty-app PATH`
selects another bundle. Its future terminal is checked by the viewer inside the
new window. No Ghostty version floor is claimed by this check.

On a supported Python, help and command generation remain noninteractive and do
not require tmux, curses or a valid `TERM`:

```sh
./run --help
./run --no-keymap --print-keymap help
./ghostty --help
./ghostty --dry-run --no-keymap
```

Diagnostic generation does not open the layout store, start servers or launch an
app. Internal action and leaf helpers do not run terminal/version probes per key
or attachment. The development preview validates prerequisites before seeding;
GUI preview still checks its future terminal after the new window starts.

## Verification

```sh
make check
make smoke
python3 -m tests.integration.smoke_preflight
python3 -m unittest tests.unit.test_bootstrap tests.unit.test_preflight tests.unit.test_startup_cleanup tests.unit.test_action_cleanup -v
```

The focused PTY fixture exercises missing tmux, reported tmux 3.2a, missing/unknown
TERM, actual monochrome terminfo and a Python import blocker simulating missing
curses. Its fake tmux executable can only report a version and records every call.
All failures must leave the fresh library empty, create no runtime sockets and
print an actionable message without a traceback or screen-control sequences.
Unit tests cover development versions, color/API failures, diagnostic commands,
partial setup and cleanup exceptions. An actual Python 3.9.6 interpreter was also
used to verify rejection across the nine public entry paths. See
[platform scope and the isolated Linux recipe](TESTING.md).
