# tmux-workspaces

[![CI](https://github.com/eandualem/tmux-workspaces/actions/workflows/ci.yml/badge.svg)](https://github.com/eandualem/tmux-workspaces/actions/workflows/ci.yml)

**A workspace layer for tmux.** Group terminals by purpose, name their tabs, keep
a split layout in each one, and come back to the same arrangement tomorrow.

```text
┌────────────────────────────┬───────────────────────────────────┐
│ ╭────────────────────────╮ │ $ pytest -q                       │
│ │ ◆ Development        ▾ │ │ ......................            │
│ │                        │ │                                   │
│ │ tabs                 + │ ├───────────────────────────────────┤
│ │   1 api              2 │ │ $ npm run dev                     │
│ │ ▶ 2 web            1 ⋯ │ │ ready on http://localhost:3000    │
│ │                        │ │                                   │
│ │ Agents                 │ │                                   │
│ │ ▶ builder              │ │                                   │
│ │ ! reviewer             │ │                                   │
│ │ ○ tester               │ │                                   │
│ │                        │ │                                   │
│ │ Configure…             │ │                                   │
│ │                        │ │                                   │
│ │  ◆   2                 │ │                                   │
│ │                        │ │                                   │
│ ╰────────────────────────╯ │                                   │
└────────────────────────────┴───────────────────────────────────┘
```

The terminal used to be where you did your work. Now it is also where work
happens without you — agents keep building and testing while you turn to
something else — and checking in is a browsing posture. This gives that work an
arrangement you can return to. It adds no notifications, rings or badges: you
look when you choose.

## Try it

You need **Python 3.11+**, **tmux 3.3+** and a Unix terminal. There are no
third-party Python packages to install.

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
| `d` | exit — your shells keep running |

With `./ghostty`: **⌘T** new tab, **⌘D** / **⌘⇧D** split, **⌘⇧[** / **⌘⇧]** move
between tabs, **⌘⌥←** / **⌘⌥→** between workspaces.

The mouse works too: click to select, double-click a name to rename it in place,
right-click for options.

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
  **Configure… → Edit colors JSON… / Edit shortcuts JSON…** opens the
  [built-in text editor](docs/JSON_SETTINGS.md) with Save, Cancel and undo.

## Install

Put the launcher on your PATH, keeping the checkout where it is:

```sh
mkdir -p ~/.local/bin
ln -s "$PWD/run" ~/.local/bin/tmux-workspaces
```

Or load it with TPM — link the checkout into `~/.tmux/plugins/tmux-workspaces`,
add `set -g @plugin 'tmux-workspaces'` before your TPM initialisation, and
**Prefix W** opens a workspace window on the server you are already using. The
[full guide](docs/GUIDE.md) covers both, plus the optional read-only adapter for
reading agent state from a local Backbone instance.

Source and TPM are the supported installation paths today. There is no published
Homebrew tap.

## Docs

- [Full guide](docs/GUIDE.md) — controls, persistence, and the parts not covered here
- [Shortcuts](docs/SHORTCUTS.md) — the in-app editor, custom keymaps, terminal profiles
- [Colors](docs/THEMES.md) and [refresh](docs/REFRESH.md)
- [Testing](docs/TESTING.md) · [Architecture](docs/ARCHITECTURE.md)
- [Acceptance and known limits](docs/ACCEPTANCE.md) — what is verified, on which
  platforms, and what is not
- If something is wrong: [startup requirements](docs/STARTUP.md) when it will not
  open, [recovery](docs/RECOVERY.md) when a saved arrangement will not load

## Contributing

Issues and pull requests are welcome. `make check` runs lint and unit tests;
`make smoke` runs the real-terminal suites. See [CONTRIBUTING.md](CONTRIBUTING.md).

MIT licensed — see [LICENSE](LICENSE) and [provenance](docs/PROVENANCE.md).
