"""Built-in terminal JSON editor, run in an owned tmux popup."""

from __future__ import annotations

import contextlib
import curses
import json
import re
import socket
import sys
import textwrap
import time
from pathlib import Path

from .events import InputEvents
from .json_settings import SettingsDraft
from .name_editor import cells
from .popup import MIN_COLS, MIN_ROWS, Frame, attribute_styles, install_styles
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


# JSON strings, and the ones that name a key.
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"(\s*:)?')


def spans(line):
    """The line as (text, kind) pieces: ``key``, ``string`` or ``text``."""
    result, position = [], 0
    for match in _STRING.finditer(line):
        if match.start() > position:
            result.append((line[position : match.start()], "text"))
        if match.group(1):
            result.append((line[match.start() : match.end() - len(match.group(1))], "key"))
            result.append((match.group(1), "text"))
        else:
            result.append((match.group(0), "string"))
        position = match.end()
    if position < len(line):
        result.append((line[position:], "text"))
    return result


class Editor:
    # The text starts here: after two cells of padding and a four-cell gutter.
    TEXT_COLUMN = 6
    # Rows above and below the text: the title bar and a blank row, then the
    # cursor row and the footer.
    TOP, BOTTOM = 2, 2

    def __init__(self, screen, draft, styles=None, frame=None):
        self.screen, self.draft = screen, draft
        self.styles = styles or attribute_styles(curses)
        self.frame = frame or Frame(screen, curses, self.styles, None, None)
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

    def area(self):
        """Rows and columns the text occupies."""
        height, width = self.screen.getmaxyx()
        return height - self.TOP - self.BOTTOM, width - self.TEXT_COLUMN - 2

    def draw(self):
        height, width = self.screen.getmaxyx()
        self.screen.erase()
        self.buttons.clear()
        if height < MIN_ROWS or width < MIN_COLS:
            self.put(0, 0, f"Enlarge terminal ({MIN_COLS}x{MIN_ROWS} minimum)")
            self.put(2, 0, "Esc cancels; your draft is retained")
            self.screen.refresh()
            return
        title = "Edit theme" if self.draft.kind == "colors" else "Edit shortcuts"
        self.frame.title(title, "JSON view · saved as TOML")
        area_height, area_width = self.area()
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
                    "muted, outline, header and danger accept foreground, background and "
                    "attributes. Color lists provide fallbacks. A preset can be default, plain, "
                    "forest, paper or mono; explicit fields override it. Saved colors apply to "
                    "this viewer."
                )
            )
            for index, line in enumerate(textwrap.wrap(info, width=width - 4)[:area_height]):
                self.put(self.TOP + index, 2, line, self.styles["normal"])
        else:
            for index, line in enumerate(lines):
                row = self.TOP + index
                self.put(row, 2, f"{self.buffer.top + index + 1:<4}", self.styles["dim"])
                if self.buffer.anchor is not None:
                    text = clip(line, self.buffer.left, area_width)
                    self.put(row, self.TEXT_COLUMN, text, self.styles["normal"] | curses.A_REVERSE)
                else:
                    self.draw_line(row, line, area_width)
        row, column = self.buffer.position()
        if self.message:
            text = textwrap.wrap(self.message, max(1, width - 4))[:1]
            self.put(height - 2, 2, text[0] if text else "", self.styles["notice"])
        else:
            self.put(
                height - 2,
                2,
                f"Line {row + 1}, column {column + 1} · ^A select all · ^Z undo · ^Y redo"
                " · ^V paste",
                self.styles["muted"],
            )
        hits = self.frame.footer(
            [("Save F2", "pill"), ("Cancel F10", "muted/header")],
            str(self.draft.file.path),
            "Help F1",
        )
        actions = {"Save F2": self.save, "Cancel F10": self.cancel}
        for start, end, label in hits:
            self.buttons.append((height - 1, start, end, actions[label]))
        help_column = max(0, width - 2 - len("Help F1"))
        self.buttons.append((height - 1, help_column, help_column + 7, self.toggle_help))
        if not self.help:
            with contextlib.suppress(curses.error):
                self.screen.move(self.TOP + cursor_row, self.TEXT_COLUMN + cursor_column)
        self.screen.refresh()
        self.frame.paint_title()

    def draw_line(self, row, line, area_width):
        """One line of JSON, keys and strings in their colors, clipped to the view."""
        position, column, left = 0, self.TEXT_COLUMN, self.buffer.left
        for text, kind in spans(line):
            style = self.styles[kind] if kind in ("key", "string") else self.styles["normal"]
            for char in text:
                size = cells(char)
                if position + size <= left:
                    position += size
                    continue
                if position >= left + area_width:
                    return
                self.put(row, column, char if char.isprintable() else " ", style)
                column += size
                position += size

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
        page = max(1, height - self.TOP - self.BOTTOM)
        if key in ("\x1b", "\x03", curses.KEY_F10):
            self.cancel()
        elif height < MIN_ROWS or width < MIN_COLS:
            return
        elif key == curses.KEY_MOUSE:
            with contextlib.suppress(curses.error):
                _, x, y, _, buttons = curses.getmouse()
                if buttons & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                    for row, start, end, action in self.buttons:
                        if y == row and start <= x < end:
                            action()
                            return
                    if (
                        self.TOP <= y < height - self.BOTTOM
                        and x >= self.TEXT_COLUMN
                        and not self.help
                    ):
                        self.buffer.click(y - self.TOP, x - self.TEXT_COLUMN)
                elif buttons & curses.BUTTON4_PRESSED:
                    self.buffer.key(curses.KEY_PPAGE, page=page)
                elif buttons & getattr(curses, "BUTTON5_PRESSED", 0):
                    self.buffer.key(curses.KEY_NPAGE, page=page)
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
            if not self.buffer.key(key, page=page):
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


def emit(text):
    sys.stdout.write(text)
    sys.stdout.flush()


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
        colors = getattr(args, "terminal_colors", None)
        styles, theme = install_styles(curses, emit, getattr(args, "chooser_theme", None), colors)
        editor = Editor(screen, draft, styles, Frame(screen, curses, styles, theme, colors))
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
