"""A single-line name field with a horizontally scrolling cursor."""

from __future__ import annotations

import curses
import unicodedata
from dataclasses import dataclass


def cells(text: str) -> int:
    return sum(
        0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
        for char in text
    )


@dataclass
class NameEditor:
    value: str
    cursor: int = 0
    selected: bool = True
    offset: int = 0

    def key(self, key) -> None:
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_HOME, curses.KEY_END):
            if key == curses.KEY_HOME or (key == curses.KEY_LEFT and self.selected):
                self.cursor = 0
            elif key == curses.KEY_END or (key == curses.KEY_RIGHT and self.selected):
                self.cursor = len(self.value)
            else:
                self.cursor = max(
                    0, min(len(self.value), self.cursor + (1 if key == curses.KEY_RIGHT else -1))
                )
            self.selected = False
        elif key in ("\x15", curses.KEY_BACKSPACE, "\x7f", "\b", curses.KEY_DC):
            if self.selected or key == "\x15":
                self.value, self.cursor = "", 0
            elif key == curses.KEY_DC:
                self.value = self.value[: self.cursor] + self.value[self.cursor + 1 :]
            elif self.cursor:
                self.value = self.value[: self.cursor - 1] + self.value[self.cursor :]
                self.cursor -= 1
            self.selected = False
        elif isinstance(key, str) and key.isprintable():
            if self.selected:
                self.value, self.cursor = "", 0
            if len(self.value) < 80:
                self.value = self.value[: self.cursor] + key + self.value[self.cursor :]
                self.cursor += 1
            self.selected = False

    def viewport(self, width: int) -> tuple[str, int]:
        self.offset = min(self.offset, self.cursor)
        while cells(self.value[self.offset : self.cursor]) >= width:
            self.offset += 1
        end = self.offset
        while end < len(self.value) and cells(self.value[self.offset : end + 1]) <= width:
            end += 1
        return self.value[self.offset : end], cells(self.value[self.offset : self.cursor])

    def click(self, column: int, width: int) -> None:
        text, _cursor = self.viewport(width)
        self.cursor = self.offset
        for char in text:
            if column < cells(char):
                break
            column -= cells(char)
            self.cursor += 1
        self.selected = False
