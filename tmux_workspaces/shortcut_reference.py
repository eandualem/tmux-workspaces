"""Read-only shortcut reference for the running viewer's frozen keymap."""

import contextlib
import curses
import json
import socket
import sys
import textwrap
import time
from itertools import zip_longest

from .config_editor import clip
from .events import InputEvents
from .keymap import ACTION_LABELS, MAX_KEYMAP_BYTES, Keymap, tmux_key_label


def reference_rows(keymap, width):
    """Wrap complete bindings into readable sections, including custom aliases."""
    rows = []
    for title, actions in (
        ("Tabs", [a for a in ACTION_LABELS if "tab" in a]),
        (
            "Panes",
            [
                "split-right",
                "split-below",
                "attach",
                "next-pane",
                "previous-pane",
                "focus",
                "close-pane",
                "copy-selection",
            ],
        ),
        ("Workspaces", [a for a in ACTION_LABELS if "workspace" in a]),
        ("Viewer", ["sidebar", "refresh-viewer", "quit"]),
    ):
        entries = []
        if width >= 76:
            entries.append((f"{'Action':28}  {'After prefix':20}  Terminal", False))
        for action in actions:
            prefix, direct = keymap.label(action), keymap.label(action, command=True)
            if not (prefix or direct):
                continue
            if width >= 76:
                columns = [
                    textwrap.wrap(value or "—", size, break_on_hyphens=False)
                    for value, size in (
                        (ACTION_LABELS[action], 28),
                        (prefix, 20),
                        (direct, width - 52),
                    )
                ]
                entries.extend(
                    (f"{label:28}  {prefix_keys:20}  {terminal_keys}".rstrip(), False)
                    for label, prefix_keys, terminal_keys in zip_longest(*columns, fillvalue="")
                )
                continue
            entries.append((ACTION_LABELS[action], True))
            for label, keys in (("After prefix", prefix), ("Terminal", direct)):
                if keys:
                    entries.extend(
                        (line, False)
                        for line in textwrap.wrap(
                            f"{label}: {keys}",
                            width=max(1, width),
                            initial_indent="  ",
                            subsequent_indent="    ",
                            break_on_hyphens=False,
                        )
                    )
        if entries:
            rows.extend([(title.upper(), True), *entries, ("", False)])
    rows.extend(
        [
            ("PREFIX CONTROLS", True),
            (f"  {tmux_key_label(keymap.prefix)} again: send literal prefix", False),
            ("  Esc: cancel prefix", False),
        ]
    )
    return [
        (part, bold) for line, bold in rows for part in (textwrap.wrap(line, max(1, width)) or [""])
    ]


class ShortcutReference:
    def __init__(self, screen, keymap):
        self.screen, self.keymap = screen, keymap
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
        rows = reference_rows(self.keymap, max(1, width - 4))
        page = max(1, height - 9)
        self.offset = max(0, min(self.offset, max(0, len(rows) - page)))
        return rows, page

    def draw(self):
        height, width = self.screen.getmaxyx()
        self.screen.erase()
        self.close_hit = None
        if height < 12 or width < 40:
            self.put(0, "Enlarge terminal (40x12 minimum)")
            self.put(2, "Esc closes shortcut reference")
        else:
            self.put(0, "Shortcuts", True)
            self.put(1, "Active in this viewer · read-only")
            self.put(2, f"Prefix: {tmux_key_label(self.keymap.prefix)}, release, then key")
            self.put(3, "Terminal keys require a matching profile.")
            rows, page = self.content()
            for index, (line, bold) in enumerate(rows[self.offset : self.offset + page], 5):
                self.put(index, line, bold)
            self.put(
                height - 3,
                f"{self.offset + 1}-{min(len(rows), self.offset + page)} / "
                f"{len(rows)} · Arrows / PgUp / PgDn / wheel",
            )
            self.put(height - 2, "Close · Esc / F10", True)
            self.close_hit = (height - 2, 1, 17)
        self.screen.refresh()

    def key(self, key):
        rows, page = self.content()
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
            self.offset = len(rows) - page
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

    def run(screen):
        screen.keypad(True)
        screen.timeout(0)
        curses.raw()
        curses.mouseinterval(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.curs_set(0)
        reference = ShortcutReference(screen, keymap)
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
