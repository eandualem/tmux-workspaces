"""The frame the editors and the shortcut reference share.

A popup is a curses program of its own inside ``display-popup``. tmux draws the
rounded border; this module gives the program the sidebar's colors, the title
bar, the footer and the raw title gradient that curses pairs cannot paint.
"""

from __future__ import annotations

import contextlib
import sys

from .attachments import sgr
from .name_editor import cells

# The title bar's gradient: one background per column, from a warm dark
# amber-grey on the left to the header bar color on the right.
GRADIENT = ((0x3A, 0x33, 0x26), (0x1E, 0x21, 0x28))
MIN_ROWS, MIN_COLS = 12, 40


def attribute_styles(curses) -> dict[str, int]:
    """The popup's look without a theme: attributes only."""
    return {
        "normal": 0,
        "title": curses.A_BOLD,
        "accent": curses.A_BOLD,
        "muted": curses.A_DIM,
        "header": curses.A_REVERSE,
        "accent/header": curses.A_REVERSE | curses.A_BOLD,
        "muted/header": curses.A_REVERSE | curses.A_DIM,
        "notice": curses.A_BOLD,
        "key": 0,
        "string": 0,
        "dim": curses.A_DIM,
        "pill": curses.A_REVERSE | curses.A_BOLD,
    }


def install_styles(curses, write, theme_state: str | None, colors: int | None):
    """The sidebar's palette for a popup, or attributes when there is none.

    Returns the styles and the theme, or the attribute styles and None.
    """
    if not theme_state:
        return attribute_styles(curses), None
    from .theme import RGB_SLOTS, ThemeError, install_extras, parse_theme_state

    try:
        curses.start_color()
        ceiling = getattr(curses, "COLORS", 0) or 8
        colors = min(colors or ceiling, ceiling)
        theme = parse_theme_state(theme_state)
        palette = theme.resolve(colors)
        palette.install(curses, write, previous_rgb=RGB_SLOTS)
        extras = install_extras(palette, curses, write, colors, ("key", "string", "dim"))
    except (ThemeError, ValueError, OSError, curses.error):
        return attribute_styles(curses), None
    styles = {
        "normal": palette.style("normal"),
        "title": palette.style("active") | curses.A_BOLD,
        "accent": palette.style("accent"),
        "muted": palette.style("muted"),
        "header": palette.style("header"),
        "accent/header": palette.style("accent", "header"),
        "muted/header": palette.style("muted", "header"),
        "notice": palette.style("accent") | curses.A_BOLD,
        "key": extras["key"],
        "string": extras["string"],
        "dim": extras["dim"],
        # The Save button: the panel's color on the accent.
        "pill": palette.style("accent") | curses.A_REVERSE | curses.A_BOLD,
    }
    return styles, theme


class Frame:
    """Draws the rows every popup shares, in the popup's own coordinates."""

    def __init__(self, screen, curses, styles, theme, colors: int | None):
        self.screen, self.curses, self.styles, self.theme = screen, curses, styles, theme
        self.colors = colors or 256
        self.title_cells: list[tuple[str, str]] = []

    def put(self, row: int, column: int, text: str, style: int = 0) -> None:
        height, width = self.screen.getmaxyx()
        if 0 <= row < height and 0 <= column < width - 1:
            with contextlib.suppress(self.curses.error):
                self.screen.addstr(row, column, text[: max(0, width - column - 1)], style)

    def fill(self, row: int, style: int) -> None:
        width = self.screen.getmaxyx()[1]
        self.put(row, 0, " " * (width - 1), style)
        with contextlib.suppress(self.curses.error):
            self.screen.insstr(row, width - 1, " ", style)

    def title(self, name: str, meta: str) -> None:
        """Row 0: the mark, the title in bold caps, the meta right-aligned.

        Drawn through curses on the header color; when the terminal has the
        colors for it, ``paint_title`` repaints the row with the gradient
        after each refresh.
        """
        width = self.screen.getmaxyx()[1]
        self.fill(0, self.styles["header"])
        self.put(0, 2, "▎", self.styles["accent/header"])
        self.put(0, 3, name.upper(), self.styles["header"] | self.curses.A_BOLD)
        self.put(0, max(0, width - 2 - cells(meta)), meta, self.styles["muted/header"])
        self.title_cells = [(" ", "muted"), (" ", "muted"), ("▎", "accent")]
        self.title_cells += [(char, "title") for char in name.upper()]
        gap = width - len(self.title_cells) - cells(meta) - 2
        self.title_cells += [(" ", "muted")] * max(0, gap)
        self.title_cells += [(char, "muted") for char in meta]
        self.title_cells = self.title_cells[: max(0, width)]

    def paint_title(self, write=None) -> None:
        """Repaint row 0 with one background per column, past curses.

        Only with a theme and a 256-color pane; otherwise the header color
        curses painted stays.
        """
        if self.theme is None or self.colors < 256:
            return
        write = write or _write
        width = self.screen.getmaxyx()[1]
        text = "\x1b7\x1b[1;1H"
        fg = {
            "title": "\x1b[1m" + sgr(self.theme.tmux_role("active", self.colors)[0]),
            "accent": sgr(self.theme.tmux_role("accent", self.colors)[0]),
            "muted": sgr(self.theme.tmux_role("muted", self.colors)[0]),
        }
        (r0, g0, b0), (r1, g1, b1) = GRADIENT
        steps = max(1, width - 1)
        for column in range(width):
            char, role = (
                self.title_cells[column] if column < len(self.title_cells) else (" ", "muted")
            )
            t = column / steps
            r, g, b = (round(a + (c - a) * t) for a, c in ((r0, r1), (g0, g1), (b0, b1)))
            text += f"\x1b[48;2;{r};{g};{b}m" + fg[role] + char + "\x1b[0m"
        write(text + "\x1b8")

    def footer(
        self, left: list[tuple[str, str]], centre: str, right: str
    ) -> list[tuple[int, int, str]]:
        """The last row: actions on the left, the file path in the centre,
        the help or close key on the right, all on the header color.

        ``left`` is a list of (label, style name); returns the columns each
        label occupies for mouse hits.
        """
        height, width = self.screen.getmaxyx()
        row = height - 1
        self.fill(row, self.styles["header"])
        hits = []
        column = 2
        for label, style in left:
            self.put(row, column, label, self.styles[style])
            hits.append((column, column + cells(label), label))
            column += cells(label) + 2
        start = max(column, (width - cells(centre)) // 2)
        self.put(row, start, centre, self.styles["muted/header"])
        self.put(
            row,
            max(start + cells(centre) + 2, width - 2 - cells(right)),
            right,
            self.styles["muted/header"],
        )
        return hits


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()
