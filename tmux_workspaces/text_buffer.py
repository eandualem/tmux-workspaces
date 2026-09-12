"""Bounded multiline editing state, independent of terminal input and rendering."""

from __future__ import annotations

import curses

from .name_editor import cells


class TextBuffer:
    def __init__(self, text, limit=65536):
        self.text, self.cursor, self.anchor = text, 0, None
        self.limit = limit
        self.undo_stack = []
        self.redo_stack = []
        self.top = self.left = 0

    def snapshot(self):
        return self.text, self.cursor, self.anchor

    def replace(self, value):
        start, end = sorted((self.cursor, self.anchor if self.anchor is not None else self.cursor))
        text = self.text[:start] + value + self.text[end:]
        if len(text.encode("utf-8")) > self.limit:
            return False
        self.undo_stack.append(self.snapshot())
        self.undo_stack = self.undo_stack[-100:]
        self.redo_stack.clear()
        self.text, self.cursor, self.anchor = text, start + len(value), None
        return True

    def undo(self, redo=False):
        source, target = (
            (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        )
        if source:
            target.append(self.snapshot())
            self.text, self.cursor, self.anchor = source.pop()

    def position(self):
        before = self.text[: self.cursor]
        return before.count("\n"), len(before.rsplit("\n", 1)[-1])

    def move_to(self, row, column):
        lines = self.text.split("\n")
        row = max(0, min(len(lines) - 1, row))
        self.cursor = sum(len(line) + 1 for line in lines[:row]) + min(
            len(lines[row]), max(0, column)
        )
        self.anchor = None

    def key(self, key, page=10):
        row, column = self.position()
        if key == "\x01":
            self.anchor, self.cursor = 0, len(self.text)
        elif key == "\x1a":
            self.undo()
        elif key == "\x19":
            self.undo(redo=True)
        elif key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE):
            offset = {
                curses.KEY_UP: -1,
                curses.KEY_DOWN: 1,
                curses.KEY_PPAGE: -page,
                curses.KEY_NPAGE: page,
            }[key]
            self.move_to(row + offset, column)
        elif key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            self.cursor = max(
                0, min(len(self.text), self.cursor + (1 if key == curses.KEY_RIGHT else -1))
            )
            self.anchor = None
        elif key in (curses.KEY_HOME, curses.KEY_END):
            self.move_to(row, 0 if key == curses.KEY_HOME else len(self.text.split("\n")[row]))
        elif key in (curses.KEY_BACKSPACE, "\x7f", "\b", curses.KEY_DC):
            if self.anchor is None:
                self.anchor = (
                    min(len(self.text), self.cursor + 1)
                    if key == curses.KEY_DC
                    else max(0, self.cursor - 1)
                )
            if self.anchor != self.cursor:
                self.replace("")
            self.anchor = None
        elif key in ("\n", "\r", curses.KEY_ENTER):
            line = self.text.split("\n")[row]
            indent = len(line) - len(line.lstrip(" "))
            return self.replace("\n" + " " * indent)
        elif key == "\t":
            return self.replace("  ")
        elif isinstance(key, str) and key.isprintable():
            return self.replace(key)
        return True

    def viewport(self, height, width):
        lines = self.text.split("\n")
        row, column = self.position()
        self.top = min(self.top, row)
        self.top = max(self.top, row - height + 1)
        cell = cells(lines[row][:column])
        self.left = min(self.left, cell)
        self.left = max(self.left, cell - width + 1)
        return lines[self.top : self.top + height], row - self.top, cell - self.left

    def click(self, row, cell):
        lines = self.text.split("\n")
        target = min(len(lines) - 1, max(0, row + self.top))
        column = 0
        position = 0
        for char in lines[target]:
            if position + cells(char) > cell + self.left:
                break
            position += cells(char)
            column += 1
        self.move_to(target, column)
