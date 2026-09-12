# Viewer colors

Use **Configure… → Edit theme…** for the [built-in editor](JSON_SETTINGS.md).
Edit a preset or individual colors in JSON, then Save to apply them to this viewer.

The viewer draws its own layer — the sidebar, the selected row, dividers, hints
and messages — and nothing else. This document describes how to change those
colors, what the viewer can and cannot control, and exactly which of its claims
have been verified.

## What the terminal keeps

The font, its size, glyph availability and the colors a shell program prints
stay under the terminal's control. A curses application cannot choose a font, so
the viewer does not offer one, and it never edits Ghostty, terminal profiles or
operating-system settings to get one. If a glyph such as `▶` or `─` renders as a
box, that is the terminal's font, not the theme.

Colors may be exact `#rrggbb` values. The five roles are curses color
pairs, and curses cannot name an RGB color, so the viewer defines each such
value in one of its own pane's palette slots (16 to 23) through tmux, which
keeps a palette per pane: nothing outside the viewer is recolored. That needs
a 256-color pane, so an RGB value is always written with a 256-color and a
basic fallback after it. The panel — the sidebar's background and the gap
between panes — is painted by tmux directly. On a terminal without truecolor
tmux approximates every RGB value itself.

## The file

Colors live in `theme.toml`, outside any installation directory. The first of
these that is set wins:

1. `--theme PATH` on the command line
2. `$TMUX_WORKSPACES_THEME`
3. `$XDG_CONFIG_HOME/tmux-workspaces/theme.toml`
4. `$HOME/.config/tmux-workspaces/theme.toml`

The path is resolved once, in the terminal that launches the viewer, and carried
across the viewer's private tmux server, so the file the viewer reads is the one
your shell would have named. A missing file is not an error: the shipped colors
are used and the same path is where the editor saves. The file may be at most
16 KiB. Reading follows symbolic links, so a link into a dotfile repository
works; saving through one does not, and is refused rather than replacing your
link with a plain file.

The file is read at startup, and again when you open **Configure… → Edit theme…**.
The editor shows the saved file as a draft; opening it does not restyle the viewer.
**Save** (`F2` / `Ctrl-S`) validates and writes a changed draft, then applies it.
Cancel leaves the file and current appearance unchanged. External file edits
do not change a running viewer while idle; use **Refresh viewer…** to load them.

## Presets

The quickest way to change the look is a preset: a complete set of the five
roles below plus the two grounds, chosen for a particular kind of terminal. A
one-line file selects one:

```toml
preset = "plain"
```

| Preset | Look |
| --- | --- |
| `default` | A `#22252b` panel inset in a `#292c33` surface, outlined in `#31343b`; `#cccccc` text, `#999999` secondary text, a `#608af7` accent and a `#343841` selection |
| `plain` | The same colors on the terminal's own background: no panel, no padding |
| `forest` | The terminal's background with a sage-green accent — the look shipped before presets |
| `paper` | The light counterpart: a `#f8f8f8` panel on white, `#3b3b3b` text, a `#005fb8` accent and an `#e8e8e8` selection |
| `mono` | The terminal's two colors only, using bold, dim and reverse |

A `panel` or `surface` value, or a role table, after the `preset` line
overrides that part of the preset, so `preset = "plain"` followed by
`[accent]` with `foreground = "red"` is plain with a red accent, and
`panel = "#1f2430"` on its own is the default look on a different panel.
Inherited normal, accent and muted backgrounds follow `panel`, including after
saving and reopening. An explicit role `background` takes precedence and stays
fixed; remove that key to inherit the panel again. The outline background follows
`surface` in the same way. Bright ground colors accept
both `bright-blue` and tmux's `brightblue` spelling.
Without a `preset` line everything starts from `default`. The theme editor
keeps the preset and inherited backgrounds in the saved file; it does not turn
inherited backgrounds into explicit overrides.

Every preset carries an explicit basic-palette fallback for each role color, so
an eight-color terminal gets a deliberate choice rather than an approximation.
On `plain`, `forest` and `mono` both grounds are `default`: the sidebar keeps
the terminal's background, there is no padding, and the borders between panes
form a band of the panel color.

## The grounds

```toml
panel = "#22252b"
surface = "#292c33"
```

Two colors tmux paints behind everything else. The **panel** is the ground of
the sidebar. The **surface** is the ground of the terminals: every content
pane, the padding beside it and the corners the sidebar's rounded outline
leaves open. Each is one value: `default` (the terminal's background), a color
name, a number from 0 to 255, or `#rrggbb`; being tmux's, an RGB value reaches
it directly, whereas an RGB role color takes a palette slot in the viewer's
pane. The roles are drawn over the panel; a role given a `background` of its
own covers the panel where that role is drawn, which is how the selected tab
gets its bar.

Giving the terminals a surface of the theme's own is what makes padding
possible: tmux's border glyphs are painted in the surface so they vanish, and
one blank column then sits on each side of every pane. With `surface =
"default"` there is no padding, because a border cannot be hidden on a ground
the viewer does not know.

## Roles

Five semantic roles map onto the five color pairs the viewer installs. Every role accepts `foreground`, `background` and `attributes`, and
every key is optional — anything you leave out keeps its preset's value.

| Role | Where it is drawn |
| --- | --- |
| `normal` | Body text, buttons and the sidebar's own background |
| `active` | The selected tab, the selected workspace, the inline name editor and the chooser's selected row |
| `accent` | Hints, the add button and error text |
| `muted` | Section labels, tab numbers and counts, the tab detail row and the chevron |
| `outline` | The panel's rounded perimeter, drawn on the surface, and the separators between split panes |

```toml
[active]
foreground = [231, "white"]
background = [238, "blue"]
attributes = ["bold"]

[accent]
foreground = "cyan"
```

A color is `#rrggbb`, `default` (the terminal's own foreground or background),
one of the eight basic names, a `bright-` name, or a number from 0 to 255. A
list is an ordered set of fallbacks: the first value this terminal can actually
show wins, and `#rrggbb` needs a 256-color pane. A bare value is shorthand for
a one-item list. The shipped presets write every exact color as
`["#4daafc", 75, "cyan"]`: the exact value, its nearest palette number, and
the basic color for an eight-color terminal.

`attributes` accepts `bold`, `dim`, `reverse`, `standout` and `underline`. They
are what keeps selection, focus and errors distinguishable when color is not
available or not perceived, alongside the `▶` marker on the selected row, which
is never removed.

Failures share the `accent` pair, because five roles mean five color pairs and
the startup floor allows no more. A failure is therefore labelled `Error:` and
drawn with `bold`, and never by color alone; progress messages such as `Colors
saved` or `Defaults shown` are neither labelled nor bold, so the two are
distinguishable in a monochrome terminal and to a reader who cannot perceive the
accent color. Every failure message leads with its reason and puts the file path
last, because a narrow sidebar shows only the first few words.

## Beyond the sidebar

Two other things are drawn in these colors, so the window reads as one layer:

- **The separators between split panes.** One thin rule in the `outline`
  foreground, drawn on the surface: down a column between panes side by side,
  across a row between stacked ones, so both directions weigh the same. On
  each side of a separator sits one blank column (or row) of surface, the
  padding of the pane beside it. No border marks the focused pane. Everything
  here changes the moment a theme installs, including while previewing in the
  editor.
- **The padding inside panes.** Each content pane is a tmux pane with a
  one-cell gutter pane on either side, drawn in the surface, and tmux's
  border glyphs are painted in the surface too so they vanish. Padding
  therefore exists only on a surface of the theme's own; `plain` has none.
- **The new-tab chooser.** An empty pane is its own small program, so it reads
  the same theme file and resolves it against the same palette size the sidebar
  used. Its title is the accent, its hints are muted, its selected row is the
  `active` pair, and the panel shows behind it. Three cases differ: without a
  theme path (a viewer started with `--no-keymap`-style minimal options never
  passes one) the chooser draws with bold, dim and reverse only; a path whose
  file is missing, unreadable or invalid gives the shipped colors, as it does
  for the sidebar; and a palette the terminal refuses to install falls back to
  the attribute-only look.

## Editing the theme

Open **Configure… → Edit theme…**, or press `t` with the sidebar focused and no
menu open. The built-in editor shows the selected theme as JSON, with Save,
Cancel, paste, undo/redo and formatting. Saved colors apply immediately to this
viewer; editing a draft does not preview it. Cancel leaves the file and current
appearance unchanged. The old per-row editor and separate Colors menu are retired.

Set `preset` to `default`, `plain`, `forest`, `paper` or `mono`; explicit role or
ground values override it. To use just a preset, replace the draft with, for
example, `{"preset":"paper"}`. See [editing controls](JSON_SETTINGS.md).

Saving uses the existing TOML file and conflict-checked atomic writer. It asks
before removing comments or custom formatting. Invalid values, unsafe targets,
read-only files and concurrent changes leave the draft open with the reason.
A file that could not be read is never replaced; fix its permissions or size
and reopen the editor. The theme-file limit is 16 KiB.

An invalid or unreadable startup theme keeps the viewer on shipped colors with
a diagnostic. If the terminal refuses theme installation, startup falls back
to the shipped theme and then the terminal's own colors.

## Palettes, and which terminal is asked

This is the subtle part, and it is worth stating precisely.

The viewer runs inside its own private tmux server, and tmux gives that pane its
own `TERM` — normally `tmux-256color` — regardless of the terminal you are
sitting in front of. A curses process inside that pane therefore reports 256
colors on almost every host, even when your terminal has eight. Asking curses
alone would mean the viewer always believed it had a full palette.

So the palette size your terminal reports is read from its terminfo before the
viewer crosses into tmux, and the theme is resolved against the smaller of that
and what the pane itself accepts. Your terminal can only narrow the palette,
never widen it: asking for a color the pane would reject is what makes curses
refuse to install a color pair at all. When your terminal has eight colors the
viewer picks the basic color it means, rather than emitting a 256-color code and
leaving the result to tmux. That is worth doing, because tmux's approximation is
not something to rely on: with `COLORTERM` set, tmux forwards 256-color codes to
an eight-color terminal unchanged.

When a configured color and all of its fallbacks are outside that palette, the
viewer still resolves it, and does so the same way every time: a `bright-` color
drops to its base color plus `bold`, and anything else picks the closest entry
in the standard xterm palette. That is a deterministic choice, not a claim about
how it will look — a terminal is free to redefine what its sixteen ANSI colors
actually are, and many do. This is why the shipped roles carry an explicit
second value rather than relying on the calculation: the eight-color choices are
deliberate, not approximations. If a role's foreground and background resolve to
the same color, that role falls back to its shipped colors, because swapping two
identical colors cannot make text visible.

The viewer requires at least eight colors and six color pairs to start at all,
which is checked before it opens anything. There is therefore no monochrome
mode: a terminal that cannot do color is refused at startup with an explanation,
and the theme feature does not claim otherwise.

## Verification

These are two different kinds of evidence and are not interchangeable.

### Verified by automated terminal tests

`tests/integration/smoke_themes.py` drives a real PTY. It reads styling with
`capture-pane -e` against the viewer's own tmux server, which reports how tmux
rendered the pane rather than the bytes your terminal received; one scenario
also inspects the real terminal's bytes directly, to confirm that no 256-color
code reaches an eight-color terminal. Reduced palettes are real: the test
compiles a terminfo entry with
`colors#8` or `colors#16` and puts it on `TERMINFO_DIRS`, so the fallback code
runs exactly as it would for a user rather than being simulated by patching a
constant. Those scenarios need `infocmp` and `tic`; without them the run prints
an explicit skip naming the missing tools, and `--required` turns that skip into
a failure.

The suite also captures blank cells with `capture-pane -e -N` to verify a
configured normal background. The settings popup smoke suite checks a saved
theme reaches the displayed panel and Cancel preserves it.

Unit tests cover the presets themselves: each resolves without an invisible
role on 8, 16 and 256 colors, the `preset` key composes with role overrides, a
saved preset round-trips through its name, the panel reaches tmux as border,
sidebar and empty-pane styles,
and the chooser installs the same file the sidebar read. The pane styles and
border band were also read back from a private tmux server on macOS with tmux
3.7c during development, on `default` and `plain`.

### Not established by those tests

No automated test here establishes how the colors *look*. Contrast, legibility
against a particular light or dark terminal theme, and how a specific terminal
renders `bold`, `dim` or the `▶` marker are visual questions that a person has
to answer by looking.

**Native light and dark terminal appearance is NOT VERIFIED.** No screenshot or
other visual record was captured, so treat the appearance on any specific
terminal — including over SSH and in WSL — as unverified until someone looks.

To check it yourself, on a disposable library so your own is untouched:

```sh
TMUX_WORKSPACES_DATA_DIR=$(mktemp -d) ./run
```

Open **Configure… → Edit theme…**, and with the terminal on a light theme and then a dark one,
confirm that the selected row is legible and obviously selected, that the `▶`
marker and the dividers are visible, that hints and the status line are readable
against the background, and that a failure — try entering `#8ab4f` — stands out
as an error. Then record the terminals, their themes and what you saw in
[ACCEPTANCE.md](ACCEPTANCE.md).

Everything above about which color codes are emitted is verified; nothing above
is a claim about how those colors are rendered to a human eye.

Live theme saves refresh existing chooser colors and add or remove padding for the
selected theme. Terminal clients may redraw, while their underlying shells and
attached sessions keep running. Switching from RGB to indexed colors restores
released palette slots, and named outline colors apply to split separators too.
