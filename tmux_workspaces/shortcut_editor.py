"""Interactive shortcut editing: choose or capture a key, resolve, then save.

The editor owns interaction state only. Canonicalisation, conflict reporting and
persistence stay in the keymap module: every change passes ``Keymap.with_keys``
before it can enter the draft, so an invalid, read-only or concurrently edited
file never costs the user the map they are working on. Cancel restores the
keymap the editor opened with; a refused save keeps that keymap and the draft.

Two halves of one map behave differently, and the difference is not cosmetic.
A prefix key reaches the viewer as an ordinary key, so it can be captured. A
terminal trigger does not: the terminal turns it into a private escape sequence
before the viewer sees anything, so pressing the combination could only ever
report the mapping already in force. The terminal half is therefore chosen, not
captured, and the editor says so rather than offering a prompt it cannot honour.

Nothing here reloads a running viewer. Saving writes a file; the keys change for
viewers and terminal instances started afterwards. That is a property of the
architecture, not a limitation of this editor, so it is stated in the interface
instead of being papered over.
"""

from __future__ import annotations

import curses

from .keymap import ACTION_LABELS, canonical_tmux_key, claim_for
from .name_editor import NameEditor

SECTIONS = ("bindings", "direct")
SECTION_LABELS = {"bindings": "prefix", "direct": "terminal"}
SECTION_TITLES = {"bindings": "After the prefix", "direct": "Terminal shortcuts"}
HINTS = (
    "↵ type · c capture · d default · u unbind · a apply",
    "↵ type · c capture · d default · u unbind · a apply",
    "↵ type c capture d default u unbind a apply",
    "↵ type · c capture · a apply",
    "↵ type a apply",
)
FIELD_HINT = "↵ keep · Esc cancel"
CAPTURE_HINT = "press a key · Esc cancel"
CONFIRM_HINT = "y take it · n keep it"
ERROR = "Error: "
MAX_VALUE = 80

# Curses reports these as integers; the keymap grammar names them as words. Only
# keys the grammar accepts appear here, so a captured key is always expressible.
_KEYPAD_NAMES = {
    "KEY_UP": "Up",
    "KEY_DOWN": "Down",
    "KEY_LEFT": "Left",
    "KEY_RIGHT": "Right",
    "KEY_HOME": "Home",
    "KEY_END": "End",
    "KEY_PPAGE": "PageUp",
    "KEY_NPAGE": "PageDown",
    "KEY_IC": "Insert",
    "KEY_DC": "Delete",
    "KEY_BTAB": "BTab",
    "KEY_BACKSPACE": "BSpace",
    "KEY_ENTER": "Enter",
}
_CONTROL_NAMES = {" ": "Space", "\t": "Tab", "\n": "Enter", "\r": "Enter", "\x7f": "BSpace"}


def keypad_names(module=curses) -> dict[int, str]:
    """The integer keycodes this terminal reports, mapped to grammar names."""
    names = {}
    for attribute, name in _KEYPAD_NAMES.items():
        code = getattr(module, attribute, None)
        if isinstance(code, int):
            names[code] = name
    function = getattr(module, "KEY_F0", None)
    if isinstance(function, int):
        names.update({function + number: f"F{number}" for number in range(1, 13)})
    return names


def captured_key(key, module=curses) -> str:
    """Name a key the way the keymap grammar spells it, or raise ValueError.

    Capture is only offered for the prefix half, where the viewer really does
    receive the key the user pressed. Anything the grammar cannot express is
    refused here with its own message rather than being stored in a shape that
    would fail to load later.
    """
    if isinstance(key, int):
        name = keypad_names(module).get(key)
        if name is None:
            raise ValueError("that key has no name this keymap can store")
        return canonical_tmux_key(name)
    if not isinstance(key, str) or len(key) != 1:
        raise ValueError("that key has no name this keymap can store")
    if key in _CONTROL_NAMES:
        return canonical_tmux_key(_CONTROL_NAMES[key])
    code = ord(key)
    if code == 0:
        return canonical_tmux_key("C-Space")
    if code < 27:
        return canonical_tmux_key(f"C-{chr(code + 96)}")
    if code in (29, 30, 31):
        return canonical_tmux_key(f"C-{chr(code + 64)}")
    return canonical_tmux_key(key)


SHORT_SECTIONS = {"bindings": "pfx", "direct": "term"}


def fit_rows(rows, width: int) -> list[str]:
    """Name every row inside `width`, keeping the section word visible.

    Two rows of one action differ only by their section, so that word is the
    last thing to go: the vocabulary shortens before the action name does, and
    the action name truncates before the section is touched. Below the width
    where even the short section word fits, the rows degrade together rather
    than collapsing onto each other.
    """
    for words in (SECTION_LABELS, SHORT_SECTIONS):
        labels = [f"{label} ({words[section]})" for label, section, *_ in rows]
        if all(len(text) <= width for text in labels):
            return labels
    labels = []
    for label, section, *_ in rows:
        word = SHORT_SECTIONS[section]
        text = f"{label} ({word})"
        if len(text) <= width:
            # Only the names that overflow are shortened; a row that fits keeps
            # its whole action name even when a longer row beside it cannot.
            labels.append(text)
            continue
        keep = width - len(word) - 4
        if keep >= 1:
            labels.append(f"{label[:keep]}… ({word})")
            continue
        # Narrower than any name can survive. Keep the section rather than the
        # name: it is what tells this row from its twin, and a column of two
        # identical labels is worse than a column of anonymous ones. The sidebar
        # refuses to draw a menu under eighteen columns, so this is a floor
        # rather than a layout anyone sees.
        section_only = f"… ({word})"
        labels.append(section_only if len(section_only) <= width else f"({word})"[:width])
    return labels


def failure(reason: str) -> str:
    """Mark a message as a failure.

    Four color pairs leave no room for an error pair, so severity is carried by
    this prefix and by the bold the status rows add for it -- never by color
    alone. The reason leads; any path trails it, because a narrow sidebar shows
    only the first few words.
    """
    reason = " ".join(str(reason).split()) or "the shortcuts were not saved"
    return reason if reason.startswith(ERROR) else ERROR + reason


def failed(message: str) -> bool:
    """Whether a status row is reporting a failure rather than progress."""
    return message.startswith(ERROR)


def hint(width: int) -> str:
    """The most complete key hint that fits; the buttons carry the rest."""
    return next((text for text in HINTS if len(text) <= width), HINTS[-1])


class ShortcutEditor:
    """Choose-and-confirm editing over a validated keymap.

    ``keymap`` is a ``Keymap``; ``save`` persists one, raising ``ValueError`` or
    ``OSError``; ``defaults`` is the shipped map, used to restore one action at a
    time. ``destination`` is the file a save would write, shown before it is
    written. ``lossy`` says that saving would drop comments or hand formatting,
    which the user is told before it happens rather than after.
    """

    def __init__(
        self,
        keymap,
        save,
        defaults,
        destination,
        *,
        writable: bool = True,
        lossy: bool = False,
        origin: str = "",
    ):
        self.working = self.draft = keymap
        self._save, self._defaults = save, defaults
        self.destination = destination
        self.origin = origin
        self.lossy = lossy
        self.writable = writable
        self.index = 0
        self.field: NameEditor | None = None
        self.capturing = False
        self.pending: tuple[str, str, list[str], object] | None = None
        self.message = "" if writable else failure("the keymap file is read-only")
        self.closed = False
        self.saved = False

    def __repr__(self) -> str:
        return (
            f"ShortcutEditor({self.index}, capturing={self.capturing}, "
            f"pending={self.pending is not None}, {self.message!r})"
        )

    @property
    def targets(self) -> tuple[tuple[str, str], ...]:
        """Every editable (section, action), prefix keys before terminal ones."""
        return tuple((section, action) for section in SECTIONS for action in ACTION_LABELS)

    @property
    def target(self) -> tuple[str, str]:
        return self.targets[self.index]

    @property
    def changed(self) -> bool:
        return self.draft != self.working

    def keys(self, section: str, action: str) -> tuple[str, ...]:
        return (self.draft.bindings if section == "bindings" else self.draft.direct)[action]

    def value(self, section: str, action: str) -> str:
        return ", ".join(self.keys(section, action))

    def capturable(self, section: str) -> bool:
        """Whether pressing the key can name it; only true for the prefix half."""
        return section == "bindings"

    def rows(self) -> list[tuple[str, str, str, bool, bool]]:
        """Action name, section, keys, selection and change flag per row.

        The section is its key, not its display word: the caller chooses how
        much of that word fits and shortens it, which it cannot do from the
        long form alone.
        """
        rows = []
        for index, (section, action) in enumerate(self.targets):
            current = self.keys(section, action)
            before = self.working.bindings if section == "bindings" else self.working.direct
            rows.append(
                (
                    ACTION_LABELS[action],
                    section,
                    ", ".join(current) or "none",
                    index == self.index,
                    current != before[action],
                )
            )
        return rows

    def changes(self) -> list[str]:
        """One line per pending change, for a preview before anything is written."""
        lines = []
        for section, action in self.targets:
            before = self.working.bindings if section == "bindings" else self.working.direct
            after = self.keys(section, action)
            if after != before[action]:
                was = ", ".join(before[action]) or "none"
                now = ", ".join(after) or "none"
                lines.append(f"{ACTION_LABELS[action]} ({SECTION_LABELS[section]}): {was} → {now}")
        if self.draft.prefix != self.working.prefix:
            lines.insert(0, f"prefix: {self.working.prefix} → {self.draft.prefix}")
        return lines

    def effect(self) -> str:
        """When a saved change actually starts working. Never claim a reload."""
        return (
            "Saved. Prefix keys apply to viewers opened after this; "
            "terminal shortcuts apply to a new terminal instance."
        )

    def select(self, index: int) -> None:
        if not self.commit() or not 0 <= index < len(self.targets):
            return
        self.index = index

    def move(self, offset: int) -> None:
        self.select((self.index + offset) % len(self.targets))

    def edit(self, index: int | None = None) -> None:
        """Open the keys field for a target, replacing any field already open."""
        if index is not None:
            self.select(index)
            if self.index != index:
                return
        self.field = NameEditor(self.value(*self.target))
        self.capturing = False
        self.message = ""

    def capture(self) -> None:
        """Wait for one key press to name the selected prefix binding."""
        section, _action = self.target
        if not self.capturable(section):
            # Saying why beats a prompt that could only report the old mapping.
            self.message = (
                "Terminal shortcuts are chosen, not captured: the terminal "
                "converts them before the viewer sees a key. Press ↵ to type one."
            )
            return
        self.field = None
        self.capturing = True
        self.message = "Press the key to bind; Esc cancels."

    def _apply_keys(self, keys: list[str]) -> bool:
        """Validate and stage one action's keys, asking before taking a used key."""
        section, action = self.target
        for key in keys:
            try:
                claim = claim_for(self.draft, section, key)
            except ValueError as error:
                self.message = failure(error)
                return False
            if claim is not None and claim.action != action:
                self.pending = (section, action, keys, claim)
                held = ACTION_LABELS[claim.action] if claim.action else claim.reason
                self.message = f"{key} is {claim.reason}. Take it from {held}? y/n"
                return False
        return self._stage(section, action, keys)

    def _stage(self, section: str, action: str, keys: list[str], *, release=None) -> bool:
        """Stage one action's keys, optionally releasing a key held elsewhere.

        ``release`` is ``(section, key)`` because the key being taken is not
        always held in the section being edited, and the two sections spell the
        same physical key differently: releasing ``ctrl+t`` from the terminal
        half is what frees ``C-t`` for a prefix binding.
        """
        draft = self.draft
        try:
            if release is not None:
                draft = draft.released(*release)
            self.draft = draft.with_keys(section, action, keys)
        except ValueError as error:
            self.message = failure(error)
            return False
        self.field = None
        self.capturing = False
        self.pending = None
        self.message = ""
        return True

    def confirm(self, take: bool) -> None:
        """Resolve a claimed key: take it from its owner, or keep the old binding."""
        if self.pending is None:
            return
        section, action, keys, claim = self.pending
        self.pending = None
        if not take:
            self.message = "Kept the existing shortcut."
            self.field = None
            self.capturing = False
            return
        if claim.kind in ("prefix", "cancel"):
            # Reserved keys have no owner to take them from; the map would
            # refuse the assignment anyway, so say that instead of trying.
            self.message = failure(f"{claim.held} is {claim.reason} and cannot be reassigned")
            return
        # Releasing happens in the section that actually holds the key, which is
        # not always the section being edited: the two spell it differently.
        self._stage(section, action, keys, release=(claim.kind, claim.held))
        if not self.message:
            self.message = f"Took {claim.held}."

    def commit(self) -> bool:
        """Validate and stage the open field. False leaves it open to fix."""
        if not self.field:
            return True
        text = self.field.value.strip()
        if len(text) > MAX_VALUE:
            self.message = failure(f"use at most {MAX_VALUE} characters")
            return False
        keys = [part.strip() for part in text.split(",") if part.strip()]
        return self._apply_keys(keys)

    def unbind(self) -> None:
        """Clear the selected action's keys in this section."""
        section, action = self.target
        self.field = None
        self._stage(section, action, [])

    def restore(self) -> None:
        """Put back the shipped keys for the selected action and section."""
        section, action = self.target
        shipped = self._defaults.bindings if section == "bindings" else self._defaults.direct
        self.field = None
        if self._apply_keys(list(shipped[action])) and not self.message:
            self.message = "Shipped keys restored; a to keep"

    def dismiss(self) -> None:
        """Leave at once, discarding staged changes and any open sub-state.

        ``cancel`` is the Escape key's two-step: the first press closes an open
        field, capture or question, the second leaves the editor. A caller that
        is taking the screen away -- focus moving to another pane, a socket
        action, another menu opening -- is not pressing Escape, so it gets one
        step. Leaving the editor half-open there is what strands it: undrawn,
        still holding scroll input, still blocking refresh.
        """
        self.field = None
        self.capturing = False
        self.pending = None
        self.draft = self.working
        self.closed = True

    def cancel(self) -> None:
        """Leave, discarding every staged change. Nothing was written."""
        if self.pending is not None:
            self.confirm(False)
            return
        if self.field or self.capturing:
            self.field = None
            self.capturing = False
            self.message = ""
            return
        self.draft = self.working
        self.closed = True

    def apply(self) -> None:
        """Persist the draft. A refused save keeps the draft and the file."""
        if not self.commit():
            return
        if not self.changed:
            self.message = "No changes to save."
            return
        try:
            self._save(self.draft)
        except (ValueError, OSError) as error:
            # Nothing on disk changed and the draft stays on screen, so the user
            # can retry, edit or cancel back to the working keymap.
            self.message = failure(error)
            return
        self.working = self.draft
        self.saved = self.closed = True

    def key(self, key) -> None:
        if self.pending is not None:
            if key in ("y", "Y"):
                self.confirm(True)
            elif key in ("n", "N", "\x1b"):
                self.confirm(False)
            return
        if self.capturing:
            if key == "\x1b":
                self.capturing = False
                self.message = ""
                return
            try:
                named = captured_key(key)
            except ValueError as error:
                self.message = failure(error)
                return
            self.capturing = False
            self._apply_keys([named])
            return
        if key in ("\n", "\r", curses.KEY_ENTER):
            self.commit() if self.field else self.edit()
        elif key == "\x1b":
            self.cancel()
        elif key in (curses.KEY_UP, curses.KEY_DOWN):
            self.move(-1 if key == curses.KEY_UP else 1)
        elif self.field:
            self.field.key(key)
        elif key == "a":
            self.apply()
        elif key == "c":
            self.capture()
        elif key == "d":
            self.restore()
        elif key == "u":
            self.unbind()
