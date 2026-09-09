"""Validated, immutable viewer keymaps and terminal profile generation.

Files are TOML data, never tmux commands. Prefix keys use a deliberately small
subset of tmux notation; direct triggers use Ghostty's named keys and modifiers.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .configfile import ConfigConflict, ConfigError, ConfigFile, Vocabulary
from .controls import ACTIONS, DIRECT_SHORTCUTS, SHORTCUTS

MAX_KEYMAP_BYTES = 64 * 1024
MAX_BINDINGS_PER_ACTION = 32
ACTION_CODES = MappingProxyType(
    {action: entry[2] for action, entry in DIRECT_SHORTCUTS.items()}
    | {
        "workspaces": 9018,
        "quit": 9019,
        "refresh-viewer": 9050,
        "tab-options": 9051,
        "workspace-options": 9052,
    }
)
ACTION_LABELS = MappingProxyType(
    {
        "new-tab": "New tab",
        "split-right": "Split right",
        "split-below": "Split below",
        "attach": "Attach session",
        "rename-tab": "Rename tab",
        "tab-options": "Tab options",
        "next-tab": "Next tab",
        "previous-tab": "Previous tab",
        "next-pane": "Next pane",
        "previous-pane": "Previous pane",
        "focus": "Focus / restore layout",
        "workspaces": "Workspace menu",
        "workspace-options": "Workspace options",
        "new-workspace": "New workspace",
        "rename-workspace": "Rename workspace",
        "next-workspace": "Next workspace",
        "previous-workspace": "Previous workspace",
        "close-pane": "Close pane",
        "close-tab": "Close tab",
        "sidebar": "Focus navigation",
        "refresh-viewer": "Refresh viewer",
        "quit": "Exit viewer",
    }
    | {f"select-tab-{n}": f"Select tab {n}" for n in range(1, 10)}
    | {f"select-workspace-{n}": f"Select workspace {n}" for n in range(1, 10)}
)
_ACTION_ORDER = tuple(ACTION_LABELS)
# Default keys added after user keymaps shipped. A file written against an
# earlier release may already spend m/M on another action or on its prefix, and
# it kept working before these menus existed. An unconfigured default here gives
# that key up rather than refusing to load; anything the user assigned to these
# actions is theirs and still collides like every other explicit binding.
YIELDING_DEFAULTS = frozenset({"tab-options", "workspace-options", "refresh-viewer"})
_NAMED_KEYS = {
    key.lower(): key
    for key in (
        "Space",
        "Enter",
        "Tab",
        "BSpace",
        "Escape",
        "Up",
        "Down",
        "Left",
        "Right",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "Insert",
        "Delete",
        "BTab",
        *(f"F{number}" for number in range(1, 13)),
    )
}
_NAMED_KEYS |= {"return": "Enter", "esc": "Escape", "backspace": "BSpace"}
_CONTROL_ALIASES = {
    "i": "Tab",
    "m": "Enter",
    "[": "Escape",
    "?": "BSpace",
    "@": "C-Space",
    "Space": "C-Space",
}
_GHOSTTY_KEYS = (
    frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
    | frozenset(
        [
            "apostrophe",
            "backslash",
            "bracket_left",
            "bracket_right",
            "comma",
            "equal",
            "grave_accent",
            "minus",
            "period",
            "semicolon",
            "slash",
            "space",
            "enter",
            "tab",
            "backspace",
            "escape",
            "insert",
            "delete",
            "right",
            "left",
            "down",
            "up",
            "page_up",
            "page_down",
            "home",
            "end",
        ]
    )
    | {f"f{number}" for number in range(1, 26)}
)
_GHOSTTY_MODIFIERS = ("super", "ctrl", "alt", "shift")
_GHOSTTY_KEY_LABELS = {
    "apostrophe": "'",
    "backslash": "\\",
    "bracket_left": "[",
    "bracket_right": "]",
    "comma": ",",
    "equal": "=",
    "grave_accent": "`",
    "minus": "-",
    "period": ".",
    "semicolon": ";",
    "slash": "/",
    "left": "←",
    "right": "→",
    "up": "↑",
    "down": "↓",
    "page_up": "PageUp",
    "page_down": "PageDown",
}


def canonical_tmux_key(value: str) -> str:
    """Canonicalize supported tmux notation, including indistinguishable C0 keys."""
    if not isinstance(value, str) or not value or len(value) > 32:
        raise ValueError("expected a tmux key such as t, C-t, M-Left or F2")
    modifiers = set()
    remaining = value
    while remaining[:2] in ("C-", "M-"):
        modifier, remaining = remaining[0], remaining[2:]
        if modifier in modifiers:
            raise ValueError(f"duplicate modifier in tmux key {value!r}")
        modifiers.add(modifier)
    key = _NAMED_KEYS.get(remaining.lower())
    if key is None:
        # A semicolon/backslash is a tmux command parser metacharacter, and is
        # intentionally outside this configuration grammar even when printable.
        if len(remaining) != 1 or not 33 <= ord(remaining) <= 126 or remaining in ";\\":
            raise ValueError(f"unsupported tmux key {value!r}")
        key = remaining
    if "C" in modifiers:
        if len(key) == 1 and key.isalpha():
            key = key.lower()
        if key in _CONTROL_ALIASES:
            key = _CONTROL_ALIASES[key]
            modifiers.remove("C")
        elif len(key) == 1 and not ("a" <= key <= "z" or key in "]^_"):
            raise ValueError(f"unsupported control key {value!r}")
    return "".join(f"{modifier}-" for modifier in ("M", "C") if modifier in modifiers) + key


def canonical_ghostty_trigger(value: str) -> str:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("expected a Ghostty trigger such as super+t")
    parts = value.split("+")
    key, modifiers = parts[-1], parts[:-1]
    if (
        key not in _GHOSTTY_KEYS
        or not modifiers
        or any(modifier not in _GHOSTTY_MODIFIERS for modifier in modifiers)
        or len(set(modifiers)) != len(modifiers)
        or not {"super", "ctrl", "alt"}.intersection(modifiers)
    ):
        raise ValueError(
            f"unsupported Ghostty trigger {value!r}; use super, ctrl or alt with a key"
        )
    return "+".join([*(m for m in _GHOSTTY_MODIFIERS if m in modifiers), key])


def _direct_tmux_key(trigger: str) -> str | None:
    """Recognize Ctrl/Alt keys shared by the two supported configuration layers.

    This is deliberately not OS/window-manager collision detection. Super has no
    equivalent in our tmux grammar; shifted punctuation uses the ASCII key names
    offered here, and named Shift keys other than Backtab have no supported
    equivalent. The terminal may reserve additional keys outside this keymap.
    """
    *modifiers, key = trigger.split("+")
    if "super" in modifiers:
        return None
    key = {
        **_GHOSTTY_KEY_LABELS,
        "left": "Left",
        "right": "Right",
        "up": "Up",
        "down": "Down",
        "backspace": "BSpace",
    }.get(key, key)
    if "shift" in modifiers:
        if len(key) == 1:
            key = key.upper().translate(
                str.maketrans("1234567890-=[]\\;',./`", '!@#$%^&*()_+{}|:"<>?~')
            )
        elif key == "tab":
            key = "BTab"
        elif key != "space":
            return None
    prefix = ("M-" if "alt" in modifiers else "") + ("C-" if "ctrl" in modifiers else "")
    try:
        return canonical_tmux_key(prefix + key)
    except ValueError:
        return None


def tmux_key_label(key: str) -> str:
    return key.replace("M-", "Alt-").replace("C-", "Ctrl-")


def ghostty_label(trigger: str) -> str:
    parts = trigger.split("+")
    symbols = {"super": "⌘", "ctrl": "⌃", "alt": "⌥", "shift": "⇧"}
    key = parts[-1]
    return "".join(symbols[m] for m in parts[:-1]) + _GHOSTTY_KEY_LABELS.get(
        key, key.upper() if len(key) == 1 or re.fullmatch(r"f\d+", key) else key.title()
    )


def action_code(action: str) -> int:
    return ACTION_CODES[action]


def direct_sequence(action: str) -> str:
    return f"\x1b[{action_code(action)}~"


def _defaults() -> tuple[dict, dict]:
    bindings = {action: [] for action in _ACTION_ORDER}
    for key, action in SHORTCUTS.items():
        bindings[action].append(key)
    for key, action in (("c", "new-tab"), ("s", "sidebar"), ("d", "quit")):
        bindings[action].append(key)
    direct = {action: [] for action in _ACTION_ORDER}
    for action, (trigger, _label, _code) in DIRECT_SHORTCUTS.items():
        direct[action].append(trigger)
    return bindings, direct


@dataclass(frozen=True)
class Keymap:
    prefix: str
    bindings: Mapping[str, tuple[str, ...]]
    direct: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_dict(cls, data: Mapping) -> Keymap:
        if not isinstance(data, Mapping):
            raise ValueError("keymap must be a TOML table")
        unknown = set(data) - {"prefix", "bindings", "direct"}
        if unknown:
            raise ValueError(f"unknown keymap option: {next(iter(unknown))!r}")
        try:
            prefix = canonical_tmux_key(data.get("prefix", "C-g"))
        except ValueError as error:
            raise ValueError(f"prefix: {error}") from error
        if prefix == "Escape":
            raise ValueError("prefix: Escape is reserved for cancellation")
        bindings, direct = _defaults()
        for section, result, canonicalize in (
            ("bindings", bindings, canonical_tmux_key),
            ("direct", direct, canonical_ghostty_trigger),
        ):
            configured = data.get(section, {})
            if not isinstance(configured, Mapping):
                raise ValueError(f"{section}: expected a table of action = [keys]")
            for action, keys in configured.items():
                if action not in ACTIONS:
                    raise ValueError(f"{section}: unknown action {action!r}")
                if not isinstance(keys, list) or len(keys) > MAX_BINDINGS_PER_ACTION:
                    raise ValueError(f"{section}.{action}: expected a list of at most 32 keys")
                try:
                    result[action] = [canonicalize(key) for key in keys]
                except ValueError as error:
                    raise ValueError(f"{section}.{action}: {error}") from error
            explicit = {key for action in configured for key in result[action]}
            claimed = explicit | ({prefix, "Escape"} if section == "bindings" else set())
            for action in YIELDING_DEFAULTS:
                if action not in configured:
                    result[action] = [key for key in result[action] if key not in claimed]
            seen = {}
            for action, keys in result.items():
                for key in keys:
                    if section == "bindings" and key in {prefix, "Escape"}:
                        raise ValueError(
                            f"bindings.{action}: {key!r} is reserved for prefix/cancel"
                        )
                    if key in seen:
                        raise ValueError(
                            f"{section}: duplicate key {key!r} for {seen[key]!r} and {action!r}"
                        )
                    seen[key] = action
        physical_keys = {
            key: f"bindings.{action}" for action, keys in bindings.items() for key in keys
        }
        physical_keys[prefix] = "prefix"
        physical_keys["Escape"] = "Escape cancellation"
        for action, triggers in direct.items():
            for trigger in triggers:
                physical = _direct_tmux_key(trigger)
                if physical in physical_keys:
                    raise ValueError(
                        f"direct.{action}: {trigger!r} shadows {physical_keys[physical]} "
                        f"({physical}); choose a different direct trigger or prefix binding"
                    )
        return cls(
            prefix,
            MappingProxyType({action: tuple(keys) for action, keys in bindings.items()}),
            MappingProxyType({action: tuple(keys) for action, keys in direct.items()}),
        )

    def prefix_items(self) -> tuple[tuple[str, str], ...]:
        return tuple((key, action) for action, keys in self.bindings.items() for key in keys)

    def direct_items(self) -> tuple[tuple[str, str, int], ...]:
        return tuple(
            (trigger, action, action_code(action))
            for action, triggers in self.direct.items()
            for trigger in triggers
        )

    def label(self, action: str, *, command: bool = False) -> str:
        mapping, format_key = (
            (self.direct, ghostty_label) if command else (self.bindings, tmux_key_label)
        )
        return " / ".join(format_key(key) for key in mapping[action])

    def prefix_help_rows(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (self.label(action), ACTION_LABELS[action])
            for action, keys in self.bindings.items()
            if keys
        )

    def direct_help_rows(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (self.label(action, command=True), ACTION_LABELS[action])
            for action, keys in self.direct.items()
            if keys
        )

    def to_dict(self) -> dict:
        """A plain mapping this class can read back, naming every action.

        Every action is listed explicitly, including the ones bound to nothing.
        ``from_dict`` only lets a default yield its key to an explicit binding
        when the action is absent, so an edited map has to be complete or
        reloading it could quietly restore a key the user cleared.
        """
        return {
            "prefix": self.prefix,
            "bindings": {action: list(keys) for action, keys in self.bindings.items()},
            "direct": {action: list(keys) for action, keys in self.direct.items()},
        }

    def with_keys(self, section: str, action: str, keys) -> Keymap:
        """This map with one action's keys replaced, or ValueError with a reason.

        Validation is not repeated here: the candidate goes back through
        ``from_dict``, which stays the single authority on what a keymap may
        contain. An invalid edit therefore cannot become a Keymap at all.
        """
        if section not in ("bindings", "direct"):
            raise ValueError(f"unknown keymap section {section!r}")
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        candidate = self.to_dict()
        candidate[section][action] = [str(key) for key in keys]
        return Keymap.from_dict(candidate)

    def with_prefix(self, prefix: str) -> Keymap:
        """This map with a different prefix key, or ValueError with a reason."""
        candidate = self.to_dict()
        candidate["prefix"] = str(prefix)
        return Keymap.from_dict(candidate)

    def released(self, section: str, key: str) -> Keymap:
        """This map with `key` removed from whichever action holds it.

        Taking a key that is already in use is a deliberate, confirmed step in
        the editor; this performs only the release, so the caller still has to
        make the assignment and can show both halves before either happens.
        """
        candidate = self.to_dict()
        canonical = _canonicalize(section, key)
        for owner, keys in candidate[section].items():
            candidate[section][owner] = [held for held in keys if held != canonical]
        return Keymap.from_dict(candidate)

    def to_toml(self) -> str:
        lines = [f"prefix = {json.dumps(self.prefix)}"]
        for section, mapping in (("bindings", self.bindings), ("direct", self.direct)):
            lines.extend(("", f"[{section}]"))
            lines.extend(f"{action} = {json.dumps(keys)}" for action, keys in mapping.items())
        return "\n".join(lines) + "\n"

    def ghostty_bindings(self) -> str:
        lines = [
            "# tmux-workspaces shortcuts; used only by the dedicated launcher.",
            "# Generated from the effective viewer keymap.",
        ]
        used = {trigger for trigger, _action, _code in self.direct_items()}
        for trigger, _label, _code in DIRECT_SHORTCUTS.values():
            if trigger not in used:
                # Ignore consumes the old trigger; unbind may forward its key
                # into the terminal and activate a remaining viewer/shell binding.
                lines.append(f"keybind = {trigger}=ignore")
        for trigger, action, code in self.direct_items():
            lines.extend((f"# {action}", f"keybind = {trigger}=csi:{code}~"))
        return "\n".join(lines) + "\n"


DEFAULT_KEYMAP = Keymap.from_dict({})


def _canonicalize(section: str, key: str) -> str:
    """The stored spelling of a key, for the section that will hold it."""
    if section == "bindings":
        return canonical_tmux_key(key)
    if section == "direct":
        return canonical_ghostty_trigger(key)
    raise ValueError(f"unknown keymap section {section!r}")


@dataclass(frozen=True)
class Claim:
    """What already holds a physical key, so the editor can offer a resolution.

    ``kind`` is ``prefix``, ``cancel``, ``bindings`` or ``direct``. ``action`` is
    set only for the two section kinds. ``held`` is the spelling to remove when
    the user chooses to take the key, which is not always the key they typed: a
    terminal trigger and a prefix binding collide through the same physical key
    while being written differently.
    """

    kind: str
    action: str | None
    held: str
    reason: str


def claim_for(keymap: Keymap, section: str, key: str) -> Claim | None:
    """What would collide if `key` were assigned in `section`, or None.

    This answers the question ``from_dict`` refuses to: it names the current
    owner instead of raising on the first problem. ``from_dict`` remains the
    authority on whether a candidate is legal -- this only decides what the
    editor should say before building one.
    """
    canonical = _canonicalize(section, key)
    physical = canonical if section == "bindings" else _direct_tmux_key(canonical)
    for action, keys in keymap.bindings.items() if section == "bindings" else ():
        if canonical in keys:
            return Claim(
                "bindings", action, canonical, f"the prefix key for {ACTION_LABELS[action]}"
            )
    for action, triggers in keymap.direct.items() if section == "direct" else ():
        if canonical in triggers:
            return Claim(
                "direct", action, canonical, f"the terminal shortcut for {ACTION_LABELS[action]}"
            )
    if physical is None:
        # A trigger the terminal never turns into a key the viewer can read
        # cannot collide with a prefix binding at all.
        return None
    if physical == keymap.prefix:
        return Claim("prefix", None, physical, "the prefix key itself")
    if physical == "Escape":
        return Claim("cancel", None, physical, "reserved for cancelling")
    if section == "direct":
        for action, keys in keymap.bindings.items():
            if physical in keys:
                return Claim(
                    "bindings",
                    action,
                    physical,
                    f"shadowed by the prefix key for {ACTION_LABELS[action]}",
                )
        return None
    for action, triggers in keymap.direct.items():
        for trigger in triggers:
            if _direct_tmux_key(trigger) == physical:
                return Claim(
                    "direct",
                    action,
                    trigger,
                    f"shadowed by the terminal shortcut for {ACTION_LABELS[action]}",
                )
    return None


def parse_keymap(payload: bytes) -> Keymap:
    """A keymap from file bytes, raising ValueError with a reason."""
    if len(payload) > MAX_KEYMAP_BYTES:
        raise ValueError(f"keymap exceeds {MAX_KEYMAP_BYTES} bytes")
    return Keymap.from_dict(tomllib.loads(payload.decode("utf-8")))


class KeymapError(ConfigError):
    """Actionable failure that never asks the caller to discard its working map."""


class KeymapConflict(KeymapError, ConfigConflict):
    """The file changed since it was read; the caller keeps its edits."""


KEYMAP_WORDS = Vocabulary(
    noun="keymap",
    title="Keymap",
    unchanged="Your shortcuts are unchanged",
    reopen="reopen the shortcuts to see the new file",
    option="--keymap",
    temp_prefix=".keymap-",
)


@dataclass(frozen=True)
class KeymapLoad:
    """A keymap plus the diagnostic to show. Loading never blocks the viewer."""

    keymap: Keymap
    path: Path
    diagnostic: str | None = None


class KeymapFile(ConfigFile):
    """Reads and conflict-checked atomic writes for one keymap file."""

    def __init__(self, path):
        super().__init__(
            path,
            limit=MAX_KEYMAP_BYTES,
            words=KEYMAP_WORDS,
            error=KeymapError,
            conflict=KeymapConflict,
        )

    def read(self) -> KeymapLoad:
        """Record the bytes seen even when they are invalid, so Save can repair them."""
        payload, diagnostic = self.read_bytes()
        if payload is None:
            return KeymapLoad(DEFAULT_KEYMAP, self.path, diagnostic)
        try:
            return KeymapLoad(parse_keymap(payload), self.path)
        except (ValueError, UnicodeDecodeError) as error:
            repair = (
                "shrink it below the size limit before saving"
                if self.oversized(payload)
                else "saving will replace it"
            )
            return KeymapLoad(DEFAULT_KEYMAP, self.path, f"{error}; {repair} ({self.path})")

    def rewrites_cleanly(self, keymap: Keymap) -> bool:
        """Whether saving would preserve everything the file currently holds.

        The generated TOML carries no comments and one fixed ordering, so a
        hand-written file is not round-tripped. Comparing the bytes is the
        honest test: equal means saving loses nothing, and the editor can offer
        an in-place save. Otherwise it has to say what would be lost.
        """
        payload, _diagnostic = self.read_bytes()
        if payload is None:
            return True
        return payload == keymap.to_toml().encode("utf-8")

    def write(self, keymap: Keymap) -> None:
        """Replace the file atomically, refusing unsafe targets and concurrent edits."""
        self.write_bytes(keymap.to_toml().encode("utf-8"))


def keymap_source(
    path: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[Path, bool]:
    """Resolve which file a launch reads, and whether that selection was explicit.

    Relaunching a viewer preserves this selection rather than the map it produced,
    so an edited file can be re-read and validated before the current one closes.
    """
    env = os.environ if environ is None else environ
    explicit = path is not None or bool(env.get("TMUX_WORKSPACES_KEYMAP"))
    if path is None:
        path = env.get("TMUX_WORKSPACES_KEYMAP")
    if not path:
        home = Path(env.get("HOME") or Path.home())
        config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
        path = config / "tmux-workspaces" / "keymap.toml"
    selected = Path(path)
    if str(selected) == "~" or str(selected).startswith("~/"):
        selected = Path(env.get("HOME") or Path.home()) / str(selected)[2:]
    if not selected.is_absolute():
        selected = (Path.cwd() if cwd is None else Path(cwd)) / selected
    return selected, explicit


def load_keymap(
    path: Path | str | None = None,
    *,
    disabled: bool = False,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Keymap:
    """Read once at launch; only an absent implicit default is silently ignored."""
    if disabled:
        if path is not None:
            raise ValueError("an explicit keymap path cannot be combined with disabled keymaps")
        return DEFAULT_KEYMAP
    selected, explicit = keymap_source(path, environ=environ, cwd=cwd)
    try:
        with selected.open("rb") as stream:
            payload = stream.read(MAX_KEYMAP_BYTES + 1)
        if len(payload) > MAX_KEYMAP_BYTES:
            raise ValueError(f"keymap exceeds {MAX_KEYMAP_BYTES} bytes")
        return Keymap.from_dict(tomllib.loads(payload.decode("utf-8")))
    except FileNotFoundError as error:
        if not explicit:
            return DEFAULT_KEYMAP
        raise ValueError(f"keymap {selected}: file does not exist") from error
    except (OSError, ValueError) as error:
        raise ValueError(f"keymap {selected}: {error}") from error
