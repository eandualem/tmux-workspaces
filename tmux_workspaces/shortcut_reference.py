"""Read-only shortcut reference for the running viewer's frozen keymap."""

import contextlib
import curses
import json
import math
import socket
import sys
import time
from pathlib import Path

from .config_editor import clip
from .events import InputEvents
from .keymap import ACTION_LABELS, MAX_KEYMAP_BYTES, Keymap, short_key_label, tmux_key_label
from .popup import MIN_COLS, MIN_ROWS, Frame, attribute_styles, install_styles

# The reference's groups: each row is one action, or a pair that reads as
# one line, or the numbered selections folded into a range.
GROUPS = (
    (
        "Tabs",
        (
            ("new-tab",),
            ("rename-tab",),
            ("tab-options",),
            ("next-tab", "previous-tab"),
            ("select-tab",),
            ("close-tab",),
        ),
    ),
    (
        "Panes",
        (
            ("split-right", "split-below"),
            ("attach",),
            ("next-pane", "previous-pane"),
            ("focus",),
            ("close-pane",),
            ("copy-selection",),
        ),
    ),
    (
        "Workspaces",
        (
            ("workspaces", "workspace-options"),
            ("new-workspace", "rename-workspace"),
            ("next-workspace", "previous-workspace"),
            ("select-workspace",),
            ("show-agents",),
        ),
    ),
    ("Viewer", (("sidebar",), ("refresh-viewer",), ("quit",))),
)
PAIR_LABELS = {
    ("next-tab", "previous-tab"): "Next / previous tab",
    ("split-right", "split-below"): "Split right / below",
    ("next-pane", "previous-pane"): "Next / previous pane",
    ("workspaces", "workspace-options"): "Workspace chooser / options",
    ("new-workspace", "rename-workspace"): "New / rename workspace",
    ("next-workspace", "previous-workspace"): "Next / previous workspace",
}
NONE = "—"
# Column widths of the wide layout: the label, the prefix keys, the rest.
LABEL_WIDTH, PREFIX_WIDTH = 32, 20
WIDE = 76


def _keys(keymap, action, *, command):
    return keymap.label(action, command=command) or ""


def _join(keys):
    """A pair's keys on one line; a pair with neither bound is one dash."""
    if not any(keys):
        return NONE
    return " / ".join(key or NONE for key in keys)


def _range_row(keymap, prefix, label):
    """The nine numbered selections as one row, or nothing when none is bound."""
    actions = [f"{prefix}-{n}" for n in range(1, 10)]
    bound = [a for a in actions if keymap.bindings[a] or keymap.direct[a]]
    if not bound:
        return None

    def fold(command):
        keys = [_keys(keymap, a, command=command) for a in actions]
        if all(keys):
            return f"{keys[0]} … {keys[-1]}"
        return " / ".join(key for key in keys if key) or NONE

    return (label, fold(False), fold(True), "item")


def reference_rows(keymap):
    """The reference as rows of (label, prefix keys, terminal keys, kind).

    ``kind`` is ``group`` for a heading, ``item`` for a binding, ``blank``
    for the row between groups and ``note`` for a line of plain text.
    Unbound actions are left out; a pair with one side unbound shows the
    other alone, so custom maps read exactly as they work.
    """
    rows = []
    for title, entries in GROUPS:
        items = []
        for entry in entries:
            if entry[0] in ("select-tab", "select-workspace"):
                row = _range_row(keymap, entry[0], f"Select {entry[0][7:]} 1–9")
                if row:
                    items.append(row)
                continue
            bound = [a for a in entry if keymap.bindings[a] or keymap.direct[a]]
            if not bound:
                continue
            if len(bound) == len(entry) == 2:
                label = PAIR_LABELS[entry]
                prefix = [_keys(keymap, a, command=False) for a in entry]
                direct = [_keys(keymap, a, command=True) for a in entry]
                items.append((label, _join(prefix), _join(direct), "item"))
                continue
            for action in bound:
                items.append(
                    (
                        ACTION_LABELS[action],
                        _keys(keymap, action, command=False) or NONE,
                        _keys(keymap, action, command=True) or NONE,
                        "item",
                    )
                )
        if items:
            rows.extend(
                [(title, "after prefix", "Ghostty", "group"), *items, ("", "", "", "blank")]
            )
    rows.extend(
        [
            ("Prefix controls", "", "", "group"),
            (f"{tmux_key_label(keymap.prefix)} again: send literal prefix", "", "", "note"),
            ("Esc: cancel prefix", "", "", "note"),
        ]
    )
    return rows


def layout_rows(rows, width):
    """Lines of (column, text, style) segments that fit ``width``.

    Wide windows align three columns; narrow ones stack the keys under
    each label.
    """
    lines = []
    room = max(1, width - 4)
    for label, prefix, direct, kind in rows:
        if kind == "blank":
            lines.append([])
        elif kind == "note":
            lines.append([(2, clip(label, 0, room), "normal")])
        elif kind == "group":
            segments = [(2, label.upper(), "muted")]
            if width >= WIDE and prefix:
                segments += [
                    (2 + LABEL_WIDTH, prefix, "muted"),
                    (2 + LABEL_WIDTH + PREFIX_WIDTH, direct, "muted"),
                ]
            lines.append(segments)
        elif width >= WIDE:
            lines.append(
                [
                    (2, clip(label, 0, LABEL_WIDTH - 1), "normal"),
                    (
                        2 + LABEL_WIDTH,
                        clip(prefix, 0, PREFIX_WIDTH - 1),
                        "accent" if prefix != NONE else "muted",
                    ),
                    (
                        2 + LABEL_WIDTH + PREFIX_WIDTH,
                        clip(direct, 0, max(1, room - LABEL_WIDTH - PREFIX_WIDTH)),
                        "muted",
                    ),
                ]
            )
        else:
            lines.append([(2, clip(label, 0, room), "normal")])
            if prefix != NONE:
                lines.append([(4, clip("after prefix: " + prefix, 0, room - 2), "accent")])
            if direct != NONE:
                lines.append([(4, clip("Ghostty: " + direct, 0, room - 2), "muted")])
    return lines


def short_path(path) -> str:
    """The last two parts of a path, as the footer shows it."""
    if not path:
        return ""
    parts = Path(str(path)).parts
    return "…/" + "/".join(parts[-2:]) if len(parts) > 2 else str(path)


class ShortcutReference:
    TOP, BOTTOM = 2, 2

    def __init__(self, screen, keymap, styles=None, frame=None, path=None):
        self.screen, self.keymap = screen, keymap
        self.styles = styles or attribute_styles(curses)
        self.frame = frame or Frame(screen, curses, self.styles, None, None)
        self.path = path
        self.offset = 0
        self.done = False
        self.sequence = ""
        self.sequence_time = 0.0
        self.pasting = False
        self.paste_discarding = False
        self.paste_time = 0.0
        self.close_hit = None

    def put(self, row, text, bold=False):
        height, width = self.screen.getmaxyx()
        if row < height:
            with contextlib.suppress(curses.error):
                self.screen.addstr(
                    row, 1, clip(text, 0, max(0, width - 2)), curses.A_BOLD if bold else 0
                )

    def content(self):
        height, width = self.screen.getmaxyx()
        lines = layout_rows(reference_rows(self.keymap), width)
        page = max(1, height - self.TOP - self.BOTTOM)
        self.offset = max(0, min(self.offset, max(0, len(lines) - page)))
        return lines, page

    def draw(self):
        height, width = self.screen.getmaxyx()
        self.screen.erase()
        self.close_hit = None
        if height < MIN_ROWS or width < MIN_COLS:
            self.put(0, f"Enlarge terminal ({MIN_COLS}x{MIN_ROWS} minimum)")
            self.put(2, "Esc closes shortcut reference")
            self.screen.refresh()
            return
        lines, page = self.content()
        pages = max(1, math.ceil(len(lines) / page))
        current = min(pages, self.offset // page + 1)
        self.frame.title("Shortcuts", f"read-only · {current}/{pages}")
        for index, segments in enumerate(lines[self.offset : self.offset + page]):
            for column, text, style in segments:
                self.frame.put(self.TOP + index, column, text, self.styles[style])
        centre = " · ".join(
            part
            for part in (short_path(self.path), f"prefix {short_key_label(self.keymap.prefix)}")
            if part
        )
        self.frame.footer([("↑↓ PgUp PgDn scroll", "muted/header")], centre, "esc / F10 close")
        # The footer holds nothing else to press: the whole row closes.
        self.close_hit = (height - 1, 0, width)
        self.screen.refresh()
        self.frame.paint_title()

    def key(self, key):
        lines, page = self.content()
        if key in ("\x1b", "\x03", curses.KEY_F10):
            self.done = True
        elif key == curses.KEY_UP:
            self.offset -= 1
        elif key == curses.KEY_DOWN:
            self.offset += 1
        elif key == curses.KEY_PPAGE:
            self.offset -= page
        elif key == curses.KEY_NPAGE:
            self.offset += page
        elif key == curses.KEY_HOME:
            self.offset = 0
        elif key == curses.KEY_END:
            self.offset = len(lines) - page
        elif key == curses.KEY_MOUSE:
            with contextlib.suppress(curses.error):
                _, x, y, _, buttons = curses.getmouse()
                if buttons & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                    if self.close_hit:
                        row, start, end = self.close_hit
                        if row == y and start <= x < end:
                            self.done = True
                elif buttons & curses.BUTTON4_PRESSED:
                    self.offset -= 3
                elif buttons & getattr(curses, "BUTTON5_PRESSED", 0):
                    self.offset += 3
        self.content()

    def feed(self, key):
        # Reference text and shortcut rows are never executable. Swallow paste
        # and unsupported terminal sequences before handling navigation/Close.
        if self.pasting and isinstance(key, str):
            self.paste_time = time.monotonic()
        if self.sequence:
            if not isinstance(key, str):
                self.sequence = ""
                return
            self.sequence += key
            if self.sequence in ("\x1b[200~", "\x1b[201~"):
                self.pasting = self.sequence == "\x1b[200~"
                self.paste_discarding = False
                self.paste_time = time.monotonic()
                self.sequence = ""
            elif (
                (len(self.sequence) >= 3 and (key.isalpha() or key == "~"))
                or len(self.sequence) > 32
                or (len(self.sequence) == 2 and key not in "[O")
            ):
                self.sequence = ""
        elif key == "\x1b":
            self.sequence, self.sequence_time = key, time.monotonic()
        elif self.pasting:
            return
        elif self.paste_discarding:
            if key == curses.KEY_F10:
                self.done = True
        else:
            self.key(key)

    def idle(self):
        if self.pasting and time.monotonic() - self.paste_time > 2.0:
            self.pasting, self.paste_discarding = False, True
            self.sequence = ""
        if self.sequence and time.monotonic() - self.sequence_time > 0.15:
            sequence, self.sequence = self.sequence, ""
            if sequence == "\x1b" and not self.pasting:
                self.done = True


def reference_main(args):
    payload = args.config_path.read_bytes()
    if len(payload) > MAX_KEYMAP_BYTES:
        raise ValueError("Shortcut reference snapshot is too large")
    keymap = Keymap.from_dict(json.loads(payload))

    def emit(text):
        sys.stdout.write(text)
        sys.stdout.flush()

    def run(screen):
        screen.keypad(True)
        screen.timeout(0)
        curses.raw()
        curses.mouseinterval(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.curs_set(0)
        colors = getattr(args, "terminal_colors", None)
        styles, theme = install_styles(curses, emit, getattr(args, "chooser_theme", None), colors)
        reference = ShortcutReference(
            screen,
            keymap,
            styles,
            Frame(screen, curses, styles, theme, colors),
            path=getattr(args, "keymap_source", None),
        )
        receiver, sender = socket.socketpair()
        with receiver, sender:
            events = InputEvents(screen, receiver)
            reference.draw()
            args.config_result.with_suffix(".ready").write_text("ready")
            while not reference.done:
                key = events.read_or_wait(time.monotonic() + 0.1)
                if key is None:
                    reference.idle()
                else:
                    reference.feed(key)
                reference.draw()

    try:
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()
        curses.wrapper(run)
    finally:
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()
    temporary = args.config_result.with_suffix(".tmp")
    temporary.write_text(json.dumps({"saved": False, "kind": "reference"}))
    temporary.replace(args.config_result)
    return 0
