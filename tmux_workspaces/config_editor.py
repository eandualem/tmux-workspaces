"""Built-in terminal JSON editor, run in an owned tmux popup."""

from __future__ import annotations

import contextlib
import curses
import json
import socket
import sys
import textwrap
import time
from pathlib import Path

from .events import InputEvents
from .json_settings import SettingsDraft
from .name_editor import cells
from .text_buffer import TextBuffer


def clip(text, left, width):
    """Clip terminal cells without splitting a wide character at either edge."""
    result, position = "", 0
    for char in text:
        size = cells(char)
        if position >= left and position + size <= left + width:
            result += char if char.isprintable() else " "
        elif position < left < position + size:
            result += " " * (position + size - left)
        position += size
        if position > left + width:
            break
    return result


class Editor:
    def __init__(self, screen, draft):
        self.screen, self.draft = screen, draft
        self.buffer = TextBuffer(draft.initial, draft.file.limit)
        self.message = draft.message
        self.done = False
        self.discard = False
        self.help = False
        self.buttons = []
        self.sequence = ""
        self.sequence_time = 0.0
        self.pasting = False
        self.paste_discarding = False
        self.paste = ""
        self.paste_time = 0.0
        self.paste_overflow = False

    def put(self, row, column, text, style=0):
        height, width = self.screen.getmaxyx()
        if 0 <= row < height and 0 <= column < width - 1:
            with contextlib.suppress(curses.error):
                self.screen.addstr(row, column, clip(text, 0, width - column - 1), style)

    def draw(self):
        height, width = self.screen.getmaxyx()
        self.screen.erase()
        self.buttons.clear()
        if height < 12 or width < 40:
            self.put(0, 0, "Enlarge terminal (40x12 minimum)")
            self.put(2, 0, "Esc cancels; your draft is retained")
            self.screen.refresh()
            return
        title = f"{self.draft.kind.title()} · JSON editor"
        self.put(0, 1, title, curses.A_BOLD)
        self.put(1, 1, str(self.draft.file.path), curses.A_DIM)
        self.put(2, 1, "JSON view · saved as TOML", curses.A_DIM)
        self.put(3, 1, "Ctrl-A select all · Ctrl-Z undo · Ctrl-Y redo", curses.A_DIM)
        area_height, area_width = height - 9, width - 7
        lines, cursor_row, cursor_column = self.buffer.viewport(area_height, area_width)
        if self.help:
            info = (
                "Edit JSON, then Save. Errors keep your draft and working settings. "
                "Arrow keys, Home/End and PageUp/PageDown move the cursor. Paste replaces "
                "selected text. F4 formats valid JSON. Escape or Cancel asks before discarding. "
                + (
                    "Shortcuts: prefix is a tmux key such as C-g. bindings maps actions to key "
                    "arrays; direct maps actions to terminal triggers such as super+t. [] unbinds. "
                    "Omitted actions use defaults. Resolve duplicates. Save offers refresh; "
                    "dedicated Ghostty profiles require reopening."
                    if self.draft.kind == "shortcuts"
                    else "Colors: panel/surface accept names or #rrggbb. normal, active, accent, "
                    "muted and outline accept foreground, background and attributes. Color lists "
                    "provide fallbacks. A preset can be default, plain, forest, paper or mono; "
                    "explicit fields override it. Saved colors apply to this viewer."
                )
            )
            for index, line in enumerate(textwrap.wrap(info, width=width - 4)[:area_height]):
                self.put(4 + index, 2, line)
        else:
            for index, line in enumerate(lines):
                self.put(4 + index, 0, f"{self.buffer.top + index + 1:4}", curses.A_DIM)
                style = curses.A_REVERSE if self.buffer.anchor is not None else 0
                self.put(4 + index, 6, clip(line, self.buffer.left, area_width), style)
        row, column = self.buffer.position()
        self.put(height - 5, 1, f"Line {row + 1}, column {column + 1}", curses.A_DIM)
        for index, line in enumerate(textwrap.wrap(self.message, max(1, width - 2))[:2]):
            self.put(height - 4 + index, 1, line, curses.A_BOLD)
        for index, (label, action) in enumerate(
            (("Save F2", self.save), ("Cancel F10", self.cancel), ("Help F1", self.toggle_help))
        ):
            start = 1 + index * ((width - 2) // 3)
            self.put(height - 2, start, label, curses.A_REVERSE)
            self.buttons.append((height - 2, start, start + len(label), action))
        if not self.help:
            with contextlib.suppress(curses.error):
                self.screen.move(4 + cursor_row, 6 + cursor_column)
        self.screen.refresh()

    def toggle_help(self):
        self.help = not self.help

    def save(self):
        self.discard = False
        if self.draft.save(self.buffer.text):
            self.done = True
        else:
            self.message = self.draft.message
            try:
                json.loads(self.buffer.text)
            except json.JSONDecodeError as error:
                self.buffer.move_to(error.lineno - 1, error.colno - 1)
            except (ValueError, RecursionError):
                pass

    def cancel(self):
        if self.buffer.text != self.draft.initial and not self.discard:
            self.discard = True
            self.message = "Unsaved edits. Cancel again to discard, or keep editing."
        else:
            self.done = True

    def key(self, key):
        if key == curses.KEY_RESIZE:
            return
        height, width = self.screen.getmaxyx()
        if key in ("\x1b", "\x03", curses.KEY_F10):
            self.cancel()
        elif height < 12 or width < 40:
            return
        elif key == curses.KEY_MOUSE:
            with contextlib.suppress(curses.error):
                _, x, y, _, buttons = curses.getmouse()
                if buttons & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                    for row, start, end, action in self.buttons:
                        if y == row and start <= x < end:
                            action()
                            return
                    if 4 <= y < height - 5 and x >= 6 and not self.help:
                        self.buffer.click(y - 4, x - 6)
                elif buttons & curses.BUTTON4_PRESSED:
                    self.buffer.key(curses.KEY_PPAGE, page=max(1, height - 9))
                elif buttons & getattr(curses, "BUTTON5_PRESSED", 0):
                    self.buffer.key(curses.KEY_NPAGE, page=max(1, height - 9))
        elif key in ("\x13", curses.KEY_F2):
            self.save()
        elif key == curses.KEY_F1:
            self.toggle_help()
        elif key == curses.KEY_F4:
            self.discard = False
            try:
                self.draft.validate(self.buffer.text)
                text = json.dumps(json.loads(self.buffer.text), ensure_ascii=True, indent=2) + "\n"
                self.buffer.anchor, self.buffer.cursor = 0, len(self.buffer.text)
                if not self.buffer.replace(text):
                    self.message = "Formatted JSON exceeds text limit; draft unchanged."
            except (ValueError, RecursionError) as error:
                self.message = str(error)
        elif not self.help:
            self.discard = False
            if not self.buffer.key(key, page=max(1, height - 9)):
                self.message = "Text limit reached; edit or remove some text before adding more."

    def feed(self, key):
        """Consume complete escape/paste sequences; pasted controls never run actions."""
        if self.pasting and isinstance(key, str):
            self.paste_time = time.monotonic()
        if self.sequence:
            if not isinstance(key, str):
                self.sequence = ""
                return
            self.sequence += key
            if self.sequence == "\x1b[200~":
                self.pasting, self.paste, self.paste_overflow = True, "", False
                self.paste_discarding = False
                self.paste_time = time.monotonic()
                self.sequence = ""
            elif self.sequence == "\x1b[201~":
                if self.pasting:
                    self.discard = False
                    if self.paste_overflow or not self.buffer.replace(self.paste):
                        self.message = "Paste exceeds text limit; draft unchanged."
                    self.pasting, self.paste = False, ""
                self.paste_discarding = False
                self.sequence = ""
            elif len(self.sequence) >= 3 and (key.isalpha() or key == "~"):
                self.sequence = ""  # Unsupported CSI/direct shortcut, never text.
            elif len(self.sequence) > 32 or (len(self.sequence) == 2 and key not in "[O"):
                self.sequence = ""
            return
        if key == "\x1b":
            self.sequence, self.sequence_time = key, time.monotonic()
        elif self.pasting:
            if isinstance(key, str) and (key.isprintable() or key in "\n\r\t"):
                value = "\n" if key == "\r" else "  " if key == "\t" else key
                if len(self.paste) + len(value) <= self.buffer.limit:
                    self.paste += value
                else:
                    self.paste_overflow = True
        elif self.paste_discarding:
            # A delayed paste tail must not turn into live Save or edit keys.
            # A new paste/end marker or explicit Escape/F10 can recover safely.
            if key == curses.KEY_F10:
                self.cancel()
        else:
            self.key(key)

    def idle(self):
        if self.pasting and time.monotonic() - self.paste_time > 2.0:
            self.pasting, self.paste, self.paste_overflow = False, "", False
            self.paste_discarding = True
            self.sequence = ""
            self.message = "Incomplete paste discarded; draft unchanged. Paste again or press Esc."
        if self.sequence and time.monotonic() - self.sequence_time > 0.15:
            sequence, self.sequence = self.sequence, ""
            if sequence == "\x1b" and not self.pasting:
                self.cancel()


def editor_main(args):
    draft = SettingsDraft(args.config_kind, args.config_path)
    result = Path(args.config_result)

    def run(screen):
        screen.keypad(True)
        screen.timeout(0)
        curses.raw()
        curses.mouseinterval(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.curs_set(1)
        editor = Editor(screen, draft)
        receiver, sender = socket.socketpair()
        with receiver, sender:
            events = InputEvents(screen, receiver)
            editor.draw()
            result.with_suffix(".ready").write_text("ready")
            while not editor.done:
                key = events.read_or_wait(time.monotonic() + 0.1)
                if key is not None:
                    editor.feed(key)
                else:
                    editor.idle()
                editor.draw()

    try:
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()
        curses.wrapper(run)
    finally:
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()
    temporary = result.with_suffix(".tmp")
    temporary.write_text(json.dumps({"saved": draft.saved, "kind": draft.kind}))
    temporary.replace(result)
    return 0
