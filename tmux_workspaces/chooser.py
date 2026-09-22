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
LEAD = "· what should it run?"
HINT = "Nothing is created until you choose."
EMPTY_ROSTER = "No tmux sessions to attach"
NO_MATCH = "No session matches the filter"
ROSTER_HEADING = "ATTACH A SESSION"
FILTER_LABEL = "filter: "
MORE_ABOVE, MORE_BELOW = "↑ more above", "↓ more below"
# A row in the layout that is neither a choice nor the heading.
BLANK = -1

# The roster's vocabulary, shared with the sidebar: a glyph that differs in
# shape before it differs in color, and a short word.
STATE_GLYPHS = {
    "busy": "▶",
    "starting": "▶",
    "waiting_for_human": "!",
    "blocked": "!",
    "idle": "○",
    "running": "○",
    "offline": "·",
}
STATE_WORDS = {
    "busy": "working",
    "starting": "starting",
    "waiting_for_human": "needs you",
    "blocked": "needs you",
    "idle": "idle",
    "running": "running",
    "offline": "offline",
}


def state_glyph(state: str) -> str:
    return STATE_GLYPHS.get(state, "?")


def state_word(state: str) -> str:
    return STATE_WORDS.get(state, state.replace("_", " ") or "unknown")


class Chooser:
    """Selection state and the action a choice sends."""

    def __init__(self, tab_id: str, leaf_id: str, cwd: str = ""):
        self.tab_id, self.leaf_id = tab_id, leaf_id
        self.cwd = cwd
        self.sessions: list[tuple[str, str]] = []
        self.query = ""
        self.index = 0
        self.offset = 0
        self.error = ""
        self.message = ""

    def update(self, sessions: dict[str, dict], error: str) -> None:
        """Replace the roster, keeping the selection on the same session name."""
        selected = self.selected_name()
        rows = []
        for name in sorted(sessions):
            if self.query.casefold() not in name.casefold():
                continue
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

    def filter(self, key: str) -> None:
        """Type to narrow the roster; Backspace widens it, Ctrl-U clears it."""
        if key == "\x15":
            self.query = ""
        elif key in ("\x7f", "\b"):
            self.query = self.query[:-1]
        elif key.isprintable() and len(self.query) < 80:
            self.query += key
        else:
            return
        self.index = min(self.index, 1 if self.query else 0)

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
        """The choice index drawn on each list row; None is the roster
        heading, BLANK the empty row above it."""
        items: list[int | None] = [0, BLANK, None]
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
        "marker": curses.A_REVERSE | curses.A_BOLD,
        "notice": curses.A_BOLD,
        "normal": 0,
        "accent": curses.A_BOLD,
        "danger": curses.A_BOLD,
        "dim": curses.A_DIM,
    }


def theme_styles(palette, curses, extras: dict[str, int] | None = None) -> dict[str, int]:
    """The chooser's look in the sidebar's colors, from an installed palette."""
    extras = extras or {}
    return {
        "title": palette.style("accent") | curses.A_BOLD,
        "muted": palette.style("muted"),
        "selected": palette.style("active"),
        "marker": palette.style("accent", "active"),
        "notice": palette.style("accent") | curses.A_BOLD,
        "normal": palette.style("normal"),
        "accent": palette.style("accent"),
        "danger": palette.style("danger"),
        "dim": extras.get("dim", palette.style("muted")),
    }


def glyph_style(state: str, styles: dict[str, int]) -> int:
    glyph = state_glyph(state)
    if glyph == "▶":
        return styles["accent"]
    if glyph == "!":
        return styles["danger"]
    if glyph == "·":
        return styles["dim"]
    return styles["muted"]


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

    def put_right(row: int, text: str, attribute: int = 0) -> None:
        put(row, max(1, width - 1 - len(text)), text, attribute)

    def bar(row: int) -> None:
        put(row, 0, " " * width, styles["selected"])

    put(0, 1, TITLE[:room], styles["title"])
    put(0, 2 + len(TITLE), LEAD, styles["muted"])
    hits: dict[int, int] = {}
    # Rows 2 to height-3 hold the list; a roster taller than that scrolls with
    # the selection, and the rows above and below say so.
    items = chooser.layout()
    start, end = chooser.viewport(height - 4)
    if start > 0:
        put(1, 1, MORE_ABOVE[:room], styles["muted"])
    row = 2
    labels = chooser.rows()
    for item in items[start:end]:
        if item == BLANK:
            row += 1
            continue
        if item is None:
            put(row, 1, ROSTER_HEADING[:room], styles["muted"])
            prompt = FILTER_LABEL + chooser.query
            column = max(2 + len(ROSTER_HEADING), width - 2 - len(prompt))
            put(row, column, prompt, styles["muted"])
            put(row, column + len(prompt), " ", styles["normal"] | curses.A_REVERSE)
            row += 1
            continue
        label, state = labels[item]
        selected = item == chooser.index
        if selected:
            bar(row)
            put(row, 1, "▶", styles["marker"])
        text = styles["selected"] | curses.A_BOLD if selected else styles["normal"]
        if item == 0:
            put(row, 3, label[:room], text)
            if chooser.cwd:
                put_right(
                    row,
                    chooser.cwd[-max(0, room - len(label) - 4) :],
                    styles["selected"] if selected else styles["muted"],
                )
        else:
            put(
                row,
                3,
                state_glyph(state),
                styles["selected"] if selected else glyph_style(state, styles),
            )
            offline = state == "offline"
            put(row, 5, label[:room], text if selected or not offline else styles["muted"])
            word = state_word(state)
            if selected:
                word_style = styles["selected"]
            elif state_glyph(state) == "!":
                word_style = styles["danger"]
            else:
                word_style = styles["muted"]
            put_right(row, word, word_style)
        hits[row] = item
        row += 1
    if end < len(items):
        put(height - 2, 1, MORE_BELOW[:room], styles["muted"])
    if not chooser.sessions:
        put(row, 3, (NO_MATCH if chooser.query else EMPTY_ROSTER)[:room], styles["muted"])
    footer = chooser.message or chooser.error or HINT
    put(height - 1, 1, footer[:room], styles["muted"] if footer == HINT else styles["notice"])
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


def install_theme(
    curses, theme_path, terminal_colors: int | None, write=None, *, theme_state: str | None = None
) -> dict[str, int] | None:
    """Install the sidebar's theme in this pane, or None to draw with attributes.

    The chooser is its own curses process. Use the viewer's installed snapshot
    so a peer saving the shared file cannot change this pane's colors. The file
    remains a fallback for callers without a snapshot. Unusable colors never
    stop the chooser: it draws as it did without a theme.
    """
    if theme_path is None and theme_state is None:
        return None
    from .theme import RGB_SLOTS, ThemeError, install_extras, load_theme, parse_theme_state

    try:
        curses.start_color()
        ceiling = getattr(curses, "COLORS", 0) or 8
        colors = min(terminal_colors or ceiling, ceiling)
        theme = (
            parse_theme_state(theme_state)
            if theme_state is not None
            else load_theme(theme_path).theme
        )
        # The chooser is a content pane: it sits on the surface, not the panel.
        theme = theme.with_panel(theme.surface)
        palette = theme.resolve(colors)
        # A respawned chooser may inherit this private pane's old RGB overrides.
        # Reset unused slots from the range we own before defining the new ones.
        palette.install(curses, write, previous_rgb=RGB_SLOTS)
        extras = install_extras(palette, curses, write, colors, ("dim",))
        screen_background = palette.style("normal")
    except (ThemeError, ValueError, OSError, curses.error):
        return None
    styles = theme_styles(palette, curses, extras)
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
        if key in (curses.KEY_UP, 16):
            chooser.move(-1)
        elif key in (curses.KEY_DOWN, 14):
            chooser.move(1)
        elif key in (curses.KEY_ENTER, 10, 13):
            choose = True
        elif key == 27:
            row = clicked_row(read_sequence(screen))
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            chooser.filter("\x7f")
        elif key == 21:
            chooser.filter("\x15")
        elif 32 <= key < 256 and not chosen_at:
            # Typing narrows the roster; the next poll redraws it.
            chooser.filter(chr(key))
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
        chooser = Chooser(args.tab, args.leaf, getattr(args, "cwd", "") or "")

        def emit(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        def main(screen):
            styles = install_theme(
                curses, args.theme, args.terminal_colors, emit, theme_state=args.chooser_theme
            )
            run(screen, chooser, source, args.action_socket, curses, styles=styles)

        curses.wrapper(main)
    finally:
        source.close()
    return 0
