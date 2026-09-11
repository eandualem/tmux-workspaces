"""An empty pane's chooser: an ordinary shell, or one of the sessions the sidebar lists.

A new tab or split opens as this instead of a shell, so attaching a session
never lands on top of a terminal nobody asked for. The pane knows which tab and leaf it was
started for and names them in its choice, so a choice can only ever fill that
pane. Drawing and input live in ``run``; ``Chooser`` holds the state and is
tested without a terminal.
"""

from __future__ import annotations

import contextlib
import re
import sys
import time

from .controls import send_action

TERMINAL = "Open terminal"
TITLE = "New pane"
LEAD = "Choose what this pane runs."
HINT = "↑↓ move · Enter open · click"
EMPTY_ROSTER = "No tmux sessions to attach"
ROSTER_HEADING = "Attach a session"
MORE_ABOVE, MORE_BELOW = "↑ more above", "↓ more below"
BAR_WIDTH = 48


class Chooser:
    """Selection state and the action a choice sends."""

    def __init__(self, tab_id: str, leaf_id: str):
        self.tab_id, self.leaf_id = tab_id, leaf_id
        self.sessions: list[tuple[str, str]] = []
        self.index = 0
        self.offset = 0
        self.error = ""
        self.message = ""

    def update(self, sessions: dict[str, dict], error: str) -> None:
        """Replace the roster, keeping the selection on the same session name."""
        selected = self.selected_name()
        rows = []
        for name in sorted(sessions):
            item = sessions[name]
            state = item.get("state", "offline") if item.get("online") else "offline"
            rows.append((name, str(state)))
        self.sessions = rows
        self.error = error
        names = [name for name, _state in rows]
        if selected in names:
            self.index = names.index(selected) + 1
        else:
            self.index = min(self.index, len(rows))

    @property
    def count(self) -> int:
        return 1 + len(self.sessions)

    def move(self, offset: int) -> None:
        self.index = (self.index + offset) % self.count

    def select(self, index: int) -> bool:
        if 0 <= index < self.count:
            self.index = index
            return True
        return False

    def selected_name(self) -> str | None:
        return None if self.index == 0 else self.sessions[self.index - 1][0]

    def rows(self) -> list[tuple[str, str]]:
        return [(TERMINAL, ""), *self.sessions]

    def layout(self) -> list[int | None]:
        """The choice index drawn on each list row; None is the roster heading."""
        items: list[int | None] = [0]
        if self.sessions:
            items.append(None)
        items.extend(range(1, self.count))
        return items

    def viewport(self, available: int) -> tuple[int, int]:
        """The slice of ``layout`` to draw, scrolled only as far as the selection needs."""
        items = self.layout()
        available = max(1, available)
        position = items.index(self.index)
        if position < self.offset:
            self.offset = position
        elif position >= self.offset + available:
            self.offset = position - available + 1
        self.offset = max(0, min(self.offset, len(items) - available))
        return self.offset, min(len(items), self.offset + available)

    def action(self) -> str:
        """The viewer action for the current selection."""
        name = self.selected_name()
        if name is None:
            return f"choose-terminal:{self.tab_id}:{self.leaf_id}"
        return f"choose-session:{self.tab_id}:{self.leaf_id}:{name}"


def attribute_styles(curses) -> dict[str, int]:
    """The chooser's look without a theme: attributes only, as it always drew."""
    return {
        "title": curses.A_BOLD,
        "muted": curses.A_DIM,
        "selected": curses.A_REVERSE,
        "notice": curses.A_BOLD,
    }


def theme_styles(palette, curses) -> dict[str, int]:
    """The chooser's look in the sidebar's colors, from an installed palette."""
    return {
        "title": palette.style("accent") | curses.A_BOLD,
        "muted": palette.style("muted"),
        "selected": palette.style("active"),
        "notice": palette.style("accent") | curses.A_BOLD,
    }


def draw(screen, chooser: Chooser, curses, styles: dict[str, int] | None = None) -> dict[int, int]:
    """Paint the chooser; return screen row -> choice index for mouse hits."""
    styles = styles or attribute_styles(curses)
    screen.erase()
    height, width = screen.getmaxyx()
    room = max(1, width - 2)

    def put(row: int, column: int, text: str, attribute: int = 0) -> None:
        if 0 <= row < height and column < width:
            with contextlib.suppress(curses.error):
                screen.addnstr(row, column, text, max(0, width - column - 1), attribute)

    put(1, 2, TITLE[:room], styles["title"])
    put(2, 2, LEAD[:room], styles["muted"])
    hits: dict[int, int] = {}
    # Rows 4 to height-3 hold the list; a roster taller than that scrolls with
    # the selection, and the rows above and below say so.
    items = chooser.layout()
    start, end = chooser.viewport(height - 6)
    if start > 0:
        put(3, 2, MORE_ABOVE[:room], styles["muted"])
    row = 4
    labels = chooser.rows()
    for item in items[start:end]:
        if item is None:
            put(row, 2, ROSTER_HEADING[:room], styles["muted"])
            row += 1
            continue
        label, state = labels[item]
        selected = item == chooser.index
        marker = "▸ " if selected else "  "
        # The selected row is one filled run, padded so it reads as a bar; in
        # a wide pane the bar stops short of the far edge.
        text = (marker + label)[:room]
        bar = max(len(text), min(room, BAR_WIDTH))
        put(row, 2, text.ljust(bar) if selected else text, styles["selected"] if selected else 0)
        if state:
            column = min(width - len(state) - 2, 4 + len(label) + 2)
            if column > 4 + len(label):
                inside = selected and column + len(state) <= 2 + bar
                put(row, column, state, styles["selected"] if inside else styles["muted"])
        hits[row] = item
        row += 1
    if end < len(items):
        put(row, 2, MORE_BELOW[:room], styles["muted"])
    if not chooser.sessions:
        put(row, 2, EMPTY_ROSTER[:room], styles["muted"])
    footer = chooser.message or chooser.error or HINT
    put(height - 1, 2, footer[:room], styles["muted"] if footer == HINT else styles["notice"])
    screen.refresh()
    return hits


# xterm mouse reporting, SGR encoded: tmux forwards a click to a pane that has
# asked for it, whatever the pane's TERM says. curses only decodes clicks for
# terminals whose terminfo advertises them, which tmux's default does not.
MOUSE_ON, MOUSE_OFF = "\x1b[?1000h\x1b[?1006h", "\x1b[?1006l\x1b[?1000l"
SGR_CLICK = re.compile(r"\[<(\d+);(\d+);(\d+)([Mm])")


def read_sequence(screen) -> str:
    """The rest of an escape sequence already begun, without blocking."""
    text = ""
    screen.nodelay(True)
    try:
        for _ in range(32):
            key = screen.getch()
            if key < 0 or key > 255:
                break
            text += chr(key)
            if text[-1].isalpha() or text[-1] == "~":
                break
    finally:
        screen.nodelay(False)
        screen.timeout(500)
    return text


def clicked_row(sequence: str) -> int | None:
    """The screen row of a left-button press in an SGR mouse sequence."""
    match = SGR_CLICK.fullmatch(sequence)
    if match and match.group(4) == "M" and int(match.group(1)) == 0:
        return int(match.group(3)) - 1
    return None


def install_theme(curses, theme_path, terminal_colors: int | None) -> dict[str, int] | None:
    """Install the sidebar's theme in this pane, or None to draw with attributes.

    The chooser is its own curses process, so it resolves the same file the
    sidebar read against the same palette size. A theme that cannot be read or
    installed never stops the chooser: it draws as it did without one.
    """
    if theme_path is None:
        return None
    from .theme import ThemeError, load_theme

    try:
        curses.start_color()
        ceiling = getattr(curses, "COLORS", 0) or 8
        colors = min(terminal_colors or ceiling, ceiling)
        palette = load_theme(theme_path).theme.resolve(colors)
        palette.install(curses)
        # The empty pane shares the sidebar's background when one is configured.
        screen_background = palette.style("normal")
    except (ThemeError, ValueError, OSError, curses.error):
        return None
    styles = theme_styles(palette, curses)
    styles["background"] = screen_background
    return styles


def run(
    screen, chooser: Chooser, source, action_socket: str, curses, write=None, styles=None
) -> None:
    """Show the roster until the viewer replaces this pane with what was chosen."""
    if write is None:

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    screen.keypad(True)
    screen.timeout(500)
    if styles and styles.get("background"):
        with contextlib.suppress(curses.error):
            screen.bkgdset(" ", styles["background"])
    write(MOUSE_ON)
    try:
        _run(screen, chooser, source, action_socket, curses, styles)
    finally:
        write(MOUSE_OFF)


def _run(screen, chooser: Chooser, source, action_socket: str, curses, styles=None) -> None:
    chosen_at = 0.0
    hits: dict[int, int] = {}
    while True:
        chooser.update(*source.snapshot())
        if chosen_at and time.monotonic() - chosen_at > 5:
            # The viewer acknowledged but nothing replaced this pane. Offer
            # the choice again rather than sit behind a stale notice.
            chooser.message, chosen_at = "", 0.0
        hits = draw(screen, chooser, curses, styles)
        key = screen.getch()
        choose = False
        row = None
        if key in (curses.KEY_UP, ord("k")):
            chooser.move(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            chooser.move(1)
        elif key in (curses.KEY_ENTER, 10, 13):
            choose = True
        elif key == 27:
            row = clicked_row(read_sequence(screen))
        elif key == curses.KEY_MOUSE:
            try:
                _id, _x, y, _z, state = curses.getmouse()
            except curses.error:
                continue
            if state & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                row = y
        if row is not None and row in hits and chooser.select(hits[row]):
            choose = True
        if not choose or chosen_at:
            continue
        try:
            send_action(action_socket, chooser.action(), wait=True)
        except (OSError, ValueError):
            chooser.message = "The viewer did not respond; try again"
            continue
        chooser.message, chosen_at = "Opening…", time.monotonic()


def chooser_main(args) -> int:
    from .application import make_source
    from .preflight import load_curses

    curses = load_curses()
    source = make_source(
        args.source_socket,
        backbone_data_dir=args.backbone_data_dir if args.backbone else None,
        url=args.url,
        demo=args.instance_dir / "demo.json" if args.demo else None,
    )
    try:
        source.refresh()
        source.start()
        chooser = Chooser(args.tab, args.leaf)

        def main(screen):
            styles = install_theme(curses, args.theme, args.terminal_colors)
            run(screen, chooser, source, args.action_socket, curses, styles=styles)

        curses.wrapper(main)
    finally:
        source.close()
    return 0
