# Viewer colors

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

Colors may be exact `#rrggbb` values. The four text roles are curses color
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

The file is read at startup, and again each time you open the colors editor.
Editing it in another program does not disturb a running viewer while you are
working: nothing is re-read on an idle frame. But because opening the editor
shows the file as it now stands, opening it after an external edit will restyle
the viewer to match that file, even though you changed nothing yourself; the
editor says `Saved colors shown` when that happens. Cancel restores what was on
screen before you opened the editor, and nothing is written unless you apply.

## Presets

The quickest way to change the look is a preset: a complete set of the four
roles below, chosen for a particular kind of terminal. A one-line file selects
one:

```toml
preset = "plain"
```

| Preset | Look |
| --- | --- |
| `default` | VS Code's Dark Modern: a `#181818` panel, `#cccccc` text, `#9d9d9d` secondary text, a `#4daafc` accent and a `#ffffff` on `#04395e` selection |
| `plain` | The same Dark Modern colors on the terminal's own background |
| `forest` | The terminal's background with a sage-green accent — the look shipped before presets |
| `paper` | VS Code's Light Modern, for a light terminal: a `#f8f8f8` panel, `#3b3b3b` text and a `#0060c0` selection |
| `mono` | The terminal's two colors only, using bold, dim and reverse |

A `panel` value or a role table after the `preset` line overrides that part
of the preset, so `preset = "plain"` followed by `[accent]` with
`foreground = "red"` is plain with a red accent, and `panel = "#1f2430"` on its
own is the default look on a different panel. Without a `preset` line the
roles start from `default`. The colors editor cycles through the presets too,
and when it saves colors that equal a preset exactly it writes the name rather
than the panel and four tables.

Every preset carries an explicit basic-palette fallback for each role color, so
an eight-color terminal gets a deliberate choice rather than an approximation.
On `plain`, `forest` and `mono` the panel is `default`: the sidebar keeps the
terminal's background and the gap between panes is a blank column.

## The panel

```toml
panel = "#272c36"
```

The panel is the sidebar's own background, the background of an empty pane
waiting for a choice, and the band tmux draws where its pane borders would be.
It is one value: `default` (the terminal's background), a color name, a number
from 0 to 255, or `#rrggbb`. It is painted by tmux as a pane style and a border
style, not by curses, which is why it alone may be an RGB value; the roles
below are drawn over it with the terminal's default background, so they show
the panel through. A role given its own `background` covers the panel where
that role is drawn.

## Roles

Four semantic roles map onto the four color pairs the viewer has always
installed. Every role accepts `foreground`, `background` and `attributes`, and
every key is optional — anything you leave out keeps its preset's value.

| Role | Where it is drawn |
| --- | --- |
| `normal` | Body text, buttons and the sidebar's own background |
| `active` | The selected tab, the selected workspace, the inline name editor and the chooser's selected row |
| `accent` | Hints, the tab detail row, the indicator light and error text |
| `muted` | Section labels, tab numbers and counts and the idle status |

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

Failures share the `accent` pair, because four roles mean four color pairs and
the startup floor allows no more. A failure is therefore labelled `Error:` and
drawn with `bold`, and never by color alone; progress messages such as `Colors
saved` or `Defaults shown` are neither labelled nor bold, so the two are
distinguishable in a monochrome terminal and to a reader who cannot perceive the
accent color. Every failure message leads with its reason and puts the file path
last, because a narrow sidebar shows only the first few words.

## Beyond the sidebar

Two other things are drawn in these colors, so the window reads as one layer:

- **The gap between panes.** tmux's pane borders are painted as a band of the
  panel color, foreground and background alike, so the panel and each
  terminal are set apart by a color gap rather than a line, and no border is
  highlighted for the focused pane. The band, the sidebar's pane style and
  any empty pane's change the moment a theme installs, including while
  previewing in the editor.
- **The new-tab chooser.** An empty pane is its own small program, so it reads
  the same theme file and resolves it against the same palette size the sidebar
  used. Its title is the accent, its hints are muted, its selected row is the
  `active` pair, and the panel shows behind it. Three cases differ: without a
  theme path (a viewer started with `--no-keymap`-style minimal options never
  passes one) the chooser draws with bold, dim and reverse only; a path whose
  file is missing, unreadable or invalid gives the shipped colors, as it does
  for the sidebar; and a palette the terminal refuses to install falls back to
  the attribute-only look.

## Editing colors

Open the editor with the **Colors…** button in the sidebar, or press `t` while
the sidebar has focus and no menu is open — from a terminal pane, that is the
prefix, then `s`, then `t`. Both routes lead to the same screen.

- Move with `↑`/`↓` or the mouse wheel, or click a row.
- `↵` opens the value under the cursor; `↵` again accepts it and previews it
  immediately.
- The **Panel** row takes the panel color as text: `default`, a name, 0-255 or
  `#rrggbb`. It previews as soon as it is accepted, like a role field.
- The last row, **Preset**, names the preset the colors currently equal, or
  `custom`. On that row `↵`, `→` or a click previews the next preset and `←`
  the previous one; the status line describes the preset shown. Custom colors
  sit before the first preset and after the last, so one step always reaches a
  named look, and Cancel still restores what you had.
- An invalid value is reported in place and is never previewed or saved. The
  field stays open with the text you typed so you can correct it.
- `a`, or the **Apply** button, writes the file and closes the editor.
- `d`, or **Restore defaults**, previews the shipped colors without saving;
  apply to keep them.
- `Esc`, **Cancel**, or leaving the editor by any other route restores the
  colors the viewer had before the editor opened, including previewed changes
  and anything the editor picked up from the file. One `Esc` leaves the whole
  editor, from an open value field as well, and a value you never accepted goes
  with it.

Each role has one row per field. A narrow sidebar shortens the field name before
it shortens the role name — `Selected background` becomes `Selected bg`, then
`Sele… bg` — so two fields of one role never read the same, and the key hint at
the top drops to the longest form that fits. The value column shows the ordered
fallback list you typed, comma separated.

The editor closes only once the colors it is leaving you with are actually
installed. In the rare case where the terminal refuses them, it stays open with
the reason instead of leaving the colors you cancelled on screen.

Applying changes the running viewer at once. It updates four color pairs; it
does not reopen the viewer, restart a shell, redraw on a timer or run a
subprocess, and nothing about the theme is read or written on an idle frame.

### When the file cannot be written

A read-only file is reported when the editor opens, and Apply says so rather
than failing silently. If the file changed on disk since the viewer read it,
Apply refuses and says so. In every refusal the colors on screen and the draft
you were editing are both kept, so nothing you were working on is lost. Saving
writes a temporary file in the same directory and renames it into place, so a
reader never sees a half-written theme, and the viewer refuses to replace a
symbolic link or anything that is not a regular file.

A file that exists but whose contents could not be read is never replaced, even
if its permissions are repaired while the editor is open: the viewer would be
destroying colors nobody has seen, and it has no digest to compare against. Fix
the permissions and reopen the editor. A file too large to read within the 16 KiB
limit is refused for the same reason — a concurrent edit to it cannot be detected
— so the diagnostic asks you to shrink it rather than promising a replacement.

An invalid or unreadable file at startup does not stop the viewer: it keeps the
shipped colors and reports the problem in the status line. Colors never block
the workspace layer, so if the terminal refuses a theme outright the viewer falls
back to the shipped one, and then to the terminal's own colors.

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

The viewer requires at least eight colors and five color pairs to start at all,
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
configured normal background, opens Colors through the workspace keyboard menu,
and checks that cancelling a defaults preview restores the configured background.

Unit tests cover the presets themselves: each resolves without an invisible
role on 8, 16 and 256 colors, the `preset` key composes with role overrides, a
saved preset round-trips through its name, the editor's preset row cycles and
previews, the panel reaches tmux as border, sidebar and empty-pane styles,
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

Open **Colors…**, and with the terminal on a light theme and then a dark one,
confirm that the selected row is legible and obviously selected, that the `▶`
marker and the dividers are visible, that hints and the status line are readable
against the background, and that a failure — try entering `#8ab4f8` — stands out
as an error. Then record the terminals, their themes and what you saw in
[ACCEPTANCE.md](ACCEPTANCE.md).

Everything above about which color codes are emitted is verified; nothing above
is a claim about how those colors are rendered to a human eye.
