"""Compact viewer theme editing: role selection, live preview, explicit save.

The editor owns interaction state only. Canonical values, palette resolution
and persistence stay in the theme module: every entry passes ``Theme.with_role``
before it can be previewed, so an invalid, read-only or concurrently edited
file never costs the user the colors they are working with. Cancel restores the
theme the editor opened with; a refused save keeps that theme and the draft.
"""

from __future__ import annotations

import curses

from .name_editor import NameEditor
from .theme import PRESET_DETAILS, PRESET_NAMES, preset_theme

FIELDS = ("foreground", "background", "attributes")
# The preset row follows the roles: one word, no field name beside it.
PRESET = "preset"
PRESET_LABEL = "Preset"
CUSTOM = "custom"
FIELD_LABELS = {"foreground": "text", "background": "background", "attributes": "style", PRESET: ""}
SHORT_FIELDS = {"foreground": "fg", "background": "bg", "attributes": "style", PRESET: ""}
HINTS = (
    "↵ edit · a apply · d defaults",
    "↵ edit · a apply · d reset",
    "↵ edit a apply d reset",
    "↵ edit a apply",
)
PRESET_HINTS = (
    "↵ next preset · ← → choose · a apply",
    "↵ next · ← → choose · a apply",
    "↵ next · a apply",
)
FIELD_HINT = "↵ keep · Esc cancel"
ERROR = "Error: "
MAX_VALUE = 60


def fit_labels(rows, width: int) -> list[str]:
    """Name every field inside `width`, keeping one vocabulary for the column.

    A narrow sidebar shortens the field word before it touches the role name,
    and truncates the role name last, so two fields of one role never collapse
    into the same text -- as long as the width leaves room for the field word
    itself. Below that every row degrades to an ellipsis, which the sidebar
    never asks for: it refuses to draw at all under eighteen columns.
    """
    for words in (FIELD_LABELS, SHORT_FIELDS):
        labels = [f"{role} {words[field]}".rstrip() for role, field, *_ in rows]
        if all(len(label) <= width for label in labels):
            return labels
    labels = []
    for role, field, *_ in rows:
        word = SHORT_FIELDS[field]
        text = f"{role} {word}".rstrip()
        keep = width - len(word) - 2
        if len(text) > width:
            text = f"{role[:keep]}… {word}" if keep >= 1 else text[: max(0, width - 1)] + "…"
        labels.append(text)
    return labels


def failure(reason: str) -> str:
    """Mark a message as a failure.

    Four color pairs leave no room for an error pair, so severity is carried by
    this prefix and by the bold the status rows add for it -- never by color
    alone. The reason leads; any path trails it, because a narrow sidebar shows
    only the first few words.
    """
    reason = " ".join(str(reason).split()) or "the colors were not saved"
    return reason if reason.startswith(ERROR) else ERROR + reason


def failed(message: str) -> bool:
    """Whether a status row is reporting a failure rather than progress."""
    return message.startswith(ERROR)


def hint(width: int, preset: bool = False) -> str:
    """The most complete key hint that fits; the buttons carry the rest."""
    hints = PRESET_HINTS if preset else HINTS
    return next((text for text in hints if len(text) <= width), hints[-1])


class ThemeEditor:
    """Preview-and-confirm editing over a validated theme.

    ``theme`` is a ``Theme``: ``roles`` maps each semantic role to a value with
    ``foreground``, ``background`` and ``attributes`` lists, and ``with_role``
    returns a new theme or raises ``ValueError`` with a message for the user.
    ``defaults`` is the shipped theme, ``install`` previews a theme in the
    terminal and ``save`` persists one, raising ``ValueError``/``OSError``.
    """

    def __init__(self, theme, save, install, defaults, *, labels=None, writable=True):
        self.working = self.draft = theme
        self._save, self._install, self._defaults = save, install, defaults
        self.labels = labels or {}
        self.index = 0
        self.field: NameEditor | None = None
        self.message = "" if writable else failure("the colors file is read-only")
        self.closed = False
        self.saved = False
        self.installed = True

    def __repr__(self) -> str:
        values = [row[2] for row in self.rows()]
        return f"ThemeEditor({values!r}, {self.index}, {self.field!r}, {self.message!r})"

    @property
    def targets(self) -> tuple[tuple[str, str], ...]:
        """Every editable (role, field) in role then field order, then the preset row."""
        return (*((role, field) for role in self.draft.roles for field in FIELDS), (PRESET, PRESET))

    @property
    def on_preset(self) -> bool:
        return self.target == (PRESET, PRESET)

    def preset(self) -> str:
        """The preset the draft equals, or ``custom`` once any role differs."""
        name = getattr(self.draft, "preset_name", lambda: None)()
        return name or CUSTOM

    @property
    def target(self) -> tuple[str, str]:
        return self.targets[self.index]

    @property
    def changed(self) -> bool:
        return self.draft != self.working

    def value(self, role: str, field: str) -> str:
        return ", ".join(getattr(self.draft.roles[role], field))

    def rows(self) -> list[tuple[str, str, str, bool]]:
        """Label, field, value and selection for the preset row and each editable field."""
        return [
            (
                PRESET_LABEL if field == PRESET else self.labels.get(role, role.title()),
                field,
                self.preset() if field == PRESET else self.value(role, field) or "none",
                index == self.index,
            )
            for index, (role, field) in enumerate(self.targets)
        ]

    def describe_preset(self) -> str:
        """What the preset row's value means, for the status line."""
        name = self.preset()
        if name == CUSTOM:
            return "Custom colors; ↵ tries the next preset"
        return f"{name}: {PRESET_DETAILS[name]}"

    def cycle_preset(self, step: int = 1) -> None:
        """Preview the next (or previous) preset. Custom colors count as before the first."""
        self.field = None
        names = list(PRESET_NAMES)
        current = self.preset()
        if current in names:
            index = (names.index(current) + step) % len(names)
        else:
            # Custom colors sit before the first preset and after the last.
            index = 0 if step > 0 else len(names) - 1
        if self.preview(preset_theme(names[index])):
            self.message = ""

    def preview(self, theme) -> bool:
        """Show a validated theme at once; nothing is written to disk."""
        self.draft = theme
        try:
            self._install(theme)
        except (ValueError, OSError, curses.error) as error:
            # The terminal refused the pairs; the colors on screen are unchanged
            # and the draft is kept so the user can correct or cancel it.
            self.message = failure(error)
            self.installed = False
            return False
        self.installed = True
        return True

    def select(self, index: int) -> None:
        if not self.commit() or not 0 <= index < len(self.targets):
            return
        self.index = index

    def move(self, offset: int) -> None:
        self.select((self.index + offset) % len(self.targets))

    def edit(self, index: int | None = None) -> None:
        """Open the value field for a target, replacing any field already open.

        On the preset row there is nothing to type: the row advances to the next
        preset instead, previewing it at once.
        """
        if index is not None:
            self.select(index)
            if self.index != index:
                return
        if self.on_preset:
            self.cycle_preset(1)
            return
        self.field = NameEditor(self.value(*self.target))
        self.message = ""

    def commit(self) -> bool:
        """Validate and preview the open field. False leaves it open to fix."""
        if not self.field:
            return True
        role, field = self.target
        text = self.field.value.strip()
        if len(text) > MAX_VALUE:
            self.message = failure(f"{role}.{field}: use at most {MAX_VALUE} characters")
            return False
        values = [part.strip() for part in text.split(",")]
        try:
            theme = self.draft.with_role(role, **{field: [part for part in values if part]})
        except (ValueError, KeyError) as error:
            self.message = failure(error)
            return False
        self.field = None
        if self.preview(theme):
            self.message = ""
        return True

    def defaults(self) -> None:
        self.field = None
        if self.preview(self._defaults):
            self.message = "Defaults shown; a to keep"

    def cancel(self) -> None:
        """Restore the theme the editor opened with, previewed changes included.

        The editor closes only once that restore is actually on screen: leaving
        while the cancelled colors are still installed would hide both the
        colors the user rejected and the reason they are still there.
        """
        self.field = None
        self.closed = self.preview(self.working)

    def apply(self) -> None:
        """Persist the draft. A refused save keeps the draft and the theme."""
        # Colors the terminal would not install are never written to the file;
        # one retry covers a transient refusal, and a second failure keeps the
        # editor open on the draft.
        if not self.commit() or (not self.installed and not self.preview(self.draft)):
            return
        try:
            self._save(self.draft)
        except (ValueError, OSError) as error:
            # Nothing on disk changed and the draft stays on screen, so the user
            # can retry, edit or cancel back to the working theme.
            self.message = failure(error)
            return
        self.working = self.draft
        self.saved = self.closed = True

    def key(self, key) -> None:
        if key in ("\n", "\r", curses.KEY_ENTER):
            self.commit() if self.field else self.edit()
        elif key == "\x1b":
            # One Escape leaves the whole editor, matching the overlay this will
            # live in; a value that was never accepted goes with it.
            self.cancel()
        elif key in (curses.KEY_UP, curses.KEY_DOWN):
            self.move(-1 if key == curses.KEY_UP else 1)
        elif key in (curses.KEY_LEFT, curses.KEY_RIGHT) and self.on_preset:
            self.cycle_preset(-1 if key == curses.KEY_LEFT else 1)
        elif self.field:
            self.field.key(key)
        elif key == "a":
            self.apply()
        elif key == "d":
            self.defaults()
