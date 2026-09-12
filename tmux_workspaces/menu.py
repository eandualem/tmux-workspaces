"""Keyboard selection over menu rows. Identity, not position, owns the choice."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import NamedTuple


class Entry(NamedTuple):
    """One menu row: a stable identity, the drawn label and what Enter runs.

    Labels are what the user reads and are not unique — two workspaces may share
    a name — so selection follows the key: a session name, a workspace id or the
    position of a fixed row.
    """

    key: str
    label: str
    action: Callable


# A separator row inside a menu: drawn as a rule, never activated, skipped
# by keyboard movement.
RULE = "\x00rule"


class Selection:
    """Track the active row of an open menu across rebuilds of its options.

    Menus are rebuilt from live sources on every frame. Reconciling by key keeps
    the active row on the same entry when a sorted insertion, a filter edit or a
    peer's change shifts positions, so Enter runs the entry the user was looking
    at. When that entry is gone the selection is marked stale and activates
    nothing: an entry that vanished must never be replaced by its neighbour.

    Only a drawn frame may adopt a row. Rebuilding for activation never selects
    an entry the user has not seen, so a session arriving while the menu shows no
    results cannot be attached by the Enter that was meant for an empty list.
    """

    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self.index = 0
        self.offset = 0
        self.active: str | None = None
        self.displayed: str | None = None
        self.stale = False
        self.follow_view = False

    @property
    def rows(self) -> list[tuple[str, Callable]]:
        """The menu rows as the drawing and integration contract exposes them."""
        return [(entry.label, entry.action) for entry in self.entries]

    def reset(self) -> None:
        self.entries, self.index, self.offset = [], 0, 0
        self.active = self.displayed = None
        self.stale, self.follow_view = False, False

    def hide(self) -> None:
        """A frame showed no rows: keep the choice, but nothing is armed for Enter."""
        self.displayed = None

    def first(self) -> None:
        """Return to the first result, as an edited filter requires."""
        self.index, self.offset = 0, 0
        self.active = self.displayed = None
        self.stale, self.follow_view = False, False

    def _locate(self) -> int:
        if self.active is None:
            return min(self.index, len(self.entries) - 1)
        if self.index < len(self.entries) and self.entries[self.index].key == self.active:
            return self.index
        match = next((i for i, entry in enumerate(self.entries) if entry.key == self.active), None)
        if match is None:
            self.stale = True
            return min(self.index, len(self.entries) - 1)
        return match

    def show(self, entries: Iterable[Entry], available: int, *, drawn: bool = True) -> list[Entry]:
        """Adopt freshly built entries and return the rows that fit the window.

        `drawn` marks the rebuild that paints a frame. A rebuild made to activate
        a row instead only re-finds the entry already chosen.
        """
        self.entries = list(entries)
        available = max(1, available)
        if not self.entries:
            self.index, self.offset, self.active = 0, 0, None
            self.stale, self.follow_view = False, False
            if drawn:
                self.displayed = None
            return []
        if self.active is None and not drawn:
            # Nothing was on screen to choose; activation must not adopt a row now.
            return self.entries[self.offset : self.offset + available]
        self.index = max(0, self._locate())
        self.offset = min(max(0, self.offset), max(0, len(self.entries) - available))
        last = min(self.offset + available, len(self.entries)) - 1
        if self.follow_view:
            # A scroll gesture moves the window; the active row follows it in,
            # so an activated row is always one the user can see.
            self.index = min(max(self.index, self.offset), last)
        elif self.index < self.offset:
            self.offset = self.index
        elif self.index > last:
            self.offset = self.index - available + 1
        self.follow_view = False
        if self.entries[self.index].label == RULE:
            selectable = [
                i
                for i in range(self.offset, min(self.offset + available, len(self.entries)))
                if self.entries[i].label != RULE
            ]
            if not selectable:
                self.active = self.displayed = None
                return self.entries[self.offset : self.offset + available]
            self.index = min(selectable, key=lambda i: abs(i - self.index))
        if not self.stale:
            self.active = self.entries[self.index].key
        if drawn and not self.stale:
            self.displayed = self.active
        return self.entries[self.offset : self.offset + available]

    def move(self, step: int) -> None:
        if not self.entries:
            self.index, self.active, self.stale = 0, None, False
            return
        self.index = min(max(0, self.index + step), len(self.entries) - 1)
        if self.entries[self.index].label == RULE:
            # A rule is not a row to land on: continue in the same direction,
            # or back the way we came at either end.
            nudge = 1 if step >= 0 else -1
            candidate = self.index + nudge
            if not 0 <= candidate < len(self.entries):
                candidate = self.index - nudge
            self.index = max(0, min(candidate, len(self.entries) - 1))
        # Moving is a deliberate choice among the rows currently loaded, and the
        # frame drawn next shows it before any Enter can be read.
        self.active = self.displayed = self.entries[self.index].key
        self.stale, self.follow_view = False, False

    def scroll(self, amount: int) -> None:
        self.offset = max(0, self.offset + amount)
        self.follow_view = True

    def entry(self) -> Entry | None:
        """The active entry, or None when there is nothing the user has chosen."""
        if self.stale or self.active is None or self.active != self.displayed:
            return None
        return next((entry for entry in self.entries if entry.key == self.active), None)
