# tmux-workspaces

[![CI](https://github.com/eandualem/tmux-workspaces/actions/workflows/ci.yml/badge.svg)](https://github.com/eandualem/tmux-workspaces/actions/workflows/ci.yml)

**A workspace layer for tmux.** Group terminals by purpose, name their tabs, keep
a split layout in each one, and come back to the same arrangement tomorrow.

```text
┌──────────────────────┬────────────────────────────────────────┐
│ ◆ Development      ▾ │ $ pytest -q                            │
│                      │ ......................                 │
│ TABS               + │                                        │
│ 1 api             ▮▮ ├────────────────────────────────────────┤
│ 2 web           ▮▮ ⋯ │ $ npm run dev                          │
│                      │ ready on http://localhost:3000         │
│                      │                                        │
│ AGENTS             3 │                                        │
│ ▶ builder            │                                        │
│ ! reviewer           │                                        │
│ ○ tester             │                                        │
│                      │                                        │
│ Configure…           │                                        │
│  ◆   2               │                                        │
├──────────────────────┴────────────────────────────────────────┤
│ Development · web · pane 1/2   ^g t new · ^g v split   agents shown ^g A  ● │
└───────────────────────────────────────────────────────────────┘
```

Use it for shells, editors, development servers or sessions you already run in
tmux. The Agents section shown above reads state from Backbone when its data
directory is on this host; the workspace viewer works without it and adds no
notifications. The status row
along the bottom names where you are, the keys that matter in the current
mode, and whether the saved arrangement is current.

## Try it

You need **Python 3.11+**, **tmux 3.3+** and a Unix terminal. There are no
third-party Python packages to install. Python needs curses support, and the
terminal needs a usable color terminfo entry. See [startup help](docs/STARTUP.md)
if a prerequisite check fails. macOS and Linux are tested; WSL and native Ghostty
rendering have separate [verification limits](docs/ACCEPTANCE.md).

```sh
git clone https://github.com/eandualem/tmux-workspaces
cd tmux-workspaces
./run
```

On macOS with [Ghostty](https://ghostty.org), use `./ghostty` instead. It opens a
dedicated profile with native Command-key shortcuts and reads your ordinary
Ghostty appearance without editing its configuration.

```sh
./ghostty
```

## Make your first workspace

1. On first launch, **Workspace 1** contains a randomly named tab and a shell.
   Run `pwd` to see its starting directory.
2. Press **Ctrl-g**, release it, then **r**. Type `development` and press Enter
   to name the tab.
3. Press **Ctrl-g**, release it, then **v** to split right. Select **Open terminal**
   in the new pane and press Enter. It starts in the neighboring shell's directory.
4. Press **Ctrl-g**, release it, then **t** for another tab. Choose **Open terminal**
   and run a command. **Ctrl-g**, then **p** returns to the first tab and its splits.
5. Choose **Configure… → Detach**, or press **Ctrl-g**, then **d**. Run `./run`
   again: names, splits and shells remain. Closing a pane or tab instead ends its
   own shells. Running processes survive only while their tmux server stays alive.

The mouse path starts with **+** on the tabs row; **⋯** on the selected tab opens
its split and rename options. See the [guide](docs/GUIDE.md) for workspaces,
existing sessions and settings.

## The keys

Press **Ctrl-g**, release it, then:

| key | does |
| --- | --- |
| `t` | new tab |
| `v` / `h` | split right / below |
| `n` / `p` | next / previous tab |
| `o` / `O` | next / previous pane |
| `W` / `[` / `]` | new / previous / next workspace |
| `r` / `R` | rename tab / workspace |
| `a` | attach an existing tmux session |
| `z` | focus one pane, or restore the layout |
| `A` | show or hide the agents section |
| `d` | detach — your shells keep running |

With `./ghostty`: **⌘T** new tab, **⌘D** / **⌘⇧D** split, **⌘⇧[** / **⌘⇧]** move
between tabs, **⌘⌥←** / **⌘⌥→** between workspaces.

The mouse works too: click to select, double-click the active tab or workspace
name to rename it, right-click for options.

## What you get

- **Workspaces group tabs by purpose** — development, review, operations — not by
  screen size. A new tab or split asks what it should run: an ordinary shell,
  or one of your tmux sessions, right there in the pane.
- **Attach sessions you already have.** Any tmux session, whoever created it. Closing
  a tab ends its own shells and never stops a session you attached.
- **It persists.** Names, grouping, splits and attachment references survive exit
  and reopen, and your shells keep running as long as tmux does.
- **Built for remote hosts.** Run it on the tmux host through SSH. It needs no
  desktop, and your shells keep the SSH agent and XDG paths you started with.
- **Yours to configure.** Pick a color preset or tune each role, and rebind
  shortcuts, from inside the viewer or from a TOML file. The panel sits beside
  your terminals in a color of its own, and the new-tab chooser matches it.
  **Configure… → Edit theme… / Edit shortcuts…** opens the
  [built-in text editor](docs/JSON_SETTINGS.md) in a popup over the panes, with
  Save, Cancel and undo. **View shortcuts…** opens a read-only reference to the
  keys active in this viewer in the same popup frame.

## Install

Put the launcher on your PATH, keeping the checkout where it is:

```sh
mkdir -p ~/.local/bin
ln -s "$PWD/run" ~/.local/bin/tmux-workspaces
```

Keep `~/.local/bin` on your PATH and do not move the checkout while viewers or
launchers use it. If that command already exists, choose another name rather
than replacing it.

For a tmux window launched through **TPM** (Tmux Plugin Manager), follow the
[TPM setup](docs/GUIDE.md#launch-from-tmux-with-tpm). Source and TPM are the
supported installation paths. The [packaging reference](docs/PACKAGING.md)
explains local source bundles; it does not provide a published Homebrew tap.

## Docs

- [Full guide](docs/GUIDE.md) — controls, persistence, and the parts not covered here
- [Shortcuts](docs/SHORTCUTS.md) — the in-app editor, custom keymaps, terminal profiles
- [Colors](docs/THEMES.md) and [refresh](docs/REFRESH.md)
- [Testing](docs/TESTING.md) · [Architecture](docs/ARCHITECTURE.md)
- [Changelog](CHANGELOG.md) — what changed in each version
- [Acceptance and known limits](docs/ACCEPTANCE.md) — what is verified, on which
  platforms, and what is not
- If something is wrong: [startup requirements](docs/STARTUP.md) when it will not
  open, [recovery](docs/RECOVERY.md) when a saved arrangement will not load

## Contributing

Issues and pull requests are welcome. `make check` runs lint and unit tests;
`make smoke` runs the real-terminal suites. See [CONTRIBUTING.md](CONTRIBUTING.md).

MIT licensed — see [LICENSE](LICENSE) and [provenance](docs/PROVENANCE.md).
