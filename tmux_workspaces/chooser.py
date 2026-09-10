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


def draw(screen, chooser: Chooser, curses) -> dict[int, int]:
    """Paint the chooser; return screen row -> choice index for mouse hits."""
    screen.erase()
    height, width = screen.getmaxyx()
    room = max(1, width - 2)

    def put(row: int, column: int, text: str, attribute: int = 0) -> None:
        if 0 <= row < height and column < width:
            with contextlib.suppress(curses.error):
                screen.addnstr(row, column, text, max(0, width - column - 1), attribute)

    put(1, 2, TITLE[:room], curses.A_BOLD)
    put(2, 2, LEAD[:room], curses.A_DIM)
    hits: dict[int, int] = {}
    # Rows 4 to height-3 hold the list; a roster taller than that scrolls with
    # the selection, and the rows above and below say so.
    items = chooser.layout()
    start, end = chooser.viewport(height - 6)
    if start > 0:
        put(3, 2, MORE_ABOVE[:room], curses.A_DIM)
    row = 4
    labels = chooser.rows()
    for item in items[start:end]:
        if item is None:
            put(row, 2, ROSTER_HEADING[:room], curses.A_DIM)
            row += 1
            continue
        label, state = labels[item]
        selected = item == chooser.index
        marker = "▸ " if selected else "  "
        put(row, 2, (marker + label)[:room], curses.A_REVERSE if selected else 0)
        if state:
            column = min(width - len(state) - 2, 4 + len(label) + 2)
            if column > 4 + len(label):
                put(row, column, state, curses.A_DIM)
        hits[row] = item
        row += 1
    if end < len(items):
        put(row, 2, MORE_BELOW[:room], curses.A_DIM)
    if not chooser.sessions:
        put(row, 2, EMPTY_ROSTER[:room], curses.A_DIM)
    footer = chooser.message or chooser.error or HINT
    put(height - 1, 2, footer[:room], curses.A_DIM if footer == HINT else curses.A_BOLD)
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


def run(screen, chooser: Chooser, source, action_socket: str, curses, write=None) -> None:
    """Show the roster until the viewer replaces this pane with what was chosen."""
    if write is None:

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    screen.keypad(True)
    screen.timeout(500)
    write(MOUSE_ON)
    try:
        _run(screen, chooser, source, action_socket, curses)
    finally:
        write(MOUSE_OFF)


def _run(screen, chooser: Chooser, source, action_socket: str, curses) -> None:
    chosen_at = 0.0
    hits: dict[int, int] = {}
    while True:
        chooser.update(*source.snapshot())
        if chosen_at and time.monotonic() - chosen_at > 5:
            # The viewer acknowledged but nothing replaced this pane. Offer
            # the choice again rather than sit behind a stale notice.
            chooser.message, chosen_at = "", 0.0
        hits = draw(screen, chooser, curses)
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
        curses.wrapper(lambda screen: run(screen, chooser, source, args.action_socket, curses))
    finally:
        source.close()
    return 0
