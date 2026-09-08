"""Validated semantic viewer colors resolved against real terminal capability.

Files are TOML data naming four semantic roles, never terminal escape sequences.
This module never imports curses: the caller injects the module, as
``preflight.load_curses`` does, so resolution and pair installation stay testable
without a terminal. Roles map one-to-one onto curses pairs 1-4, which keeps the
documented startup floor of eight colors and five pairs intact.

The viewer styles its own layer only. Fonts, glyph availability and the colors a
shell program prints remain the terminal's, because a curses application cannot
choose a font; see docs for that boundary.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import stat
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import MappingProxyType

MAX_THEME_BYTES = 16 * 1024
MAX_FALLBACKS = 8
SUBSTITUTE_FOREGROUND = 7
SUBSTITUTE_BACKGROUND = 0
LOCK_TIMEOUT_SECONDS = 2.0
LOCK_POLL_SECONDS = 0.02

# Order is the curses pair number minus one, matching the pairs the viewer has
# always installed: normal=1, active=2, accent=3, muted=4.
ROLES = ("normal", "active", "accent", "muted")
ROLE_LABELS = MappingProxyType(
    {
        "normal": "Normal",
        "active": "Selected",
        "accent": "Accent",
        "muted": "Muted",
    }
)
ROLE_DETAILS = MappingProxyType(
    {
        "normal": "Body text and inactive buttons.",
        "active": "The selected tab, the inline name editor and active counts.",
        "accent": (
            "Hints, the status line and error text. Error text shares this pair, so it "
            "always adds bold: with four pairs the colour alone cannot distinguish it."
        ),
        "muted": "Dividers, inactive counts and the idle status line.",
    }
)
ATTRIBUTES = ("bold", "dim", "reverse", "standout", "underline")
_BASIC_NAMES = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
COLOR_NAMES = ("default", *_BASIC_NAMES, *(f"bright-{name}" for name in _BASIC_NAMES))
COLOR_CHOICES = COLOR_NAMES
DEFAULT_COLOR = "default"

_NAME_TO_INDEX = MappingProxyType(
    {DEFAULT_COLOR: -1} | {name: index for index, name in enumerate(COLOR_NAMES[1:])}
)
_INDEX_TO_NAME = MappingProxyType({index: name for name, index in _NAME_TO_INDEX.items()})

# xterm's first sixteen entries. Used only to pick a nearest basic color when a
# configured value and every configured fallback exceed the terminal's palette.
_SYSTEM_RGB = (
    (0x00, 0x00, 0x00),
    (0xCD, 0x00, 0x00),
    (0x00, 0xCD, 0x00),
    (0xCD, 0xCD, 0x00),
    (0x00, 0x00, 0xEE),
    (0xCD, 0x00, 0xCD),
    (0x00, 0xCD, 0xCD),
    (0xE5, 0xE5, 0xE5),
    (0x7F, 0x7F, 0x7F),
    (0xFF, 0x00, 0x00),
    (0x00, 0xFF, 0x00),
    (0xFF, 0xFF, 0x00),
    (0x5C, 0x5C, 0xFF),
    (0xFF, 0x00, 0xFF),
    (0x00, 0xFF, 0xFF),
    (0xFF, 0xFF, 0xFF),
)
_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)


def _rgb(index: int) -> tuple[int, int, int]:
    if index < 16:
        return _SYSTEM_RGB[index]
    if index < 232:
        offset = index - 16
        return tuple(_CUBE_LEVELS[(offset // step) % 6] for step in (36, 6, 1))
    level = 8 + 10 * (index - 232)
    return (level, level, level)


def canonical_color(value: str | int | bool) -> str:
    """Canonicalize a configured color; raise with an actionable message otherwise."""
    if isinstance(value, bool):
        raise ValueError("expected a color name, 'default', or a number from 0 to 255")
    if isinstance(value, int):
        if not 0 <= value <= 255:
            raise ValueError(f"color index {value} is outside 0-255")
        return str(value)
    if not isinstance(value, str) or not value or len(value) > 32:
        raise ValueError("expected a color name, 'default', or a number from 0 to 255")
    text = value.strip().lower().replace("_", "-")
    if text in _NAME_TO_INDEX:
        return text
    if text.isdigit():
        return canonical_color(int(text))
    if text.startswith("#") or text.startswith("0x") or text.startswith("rgb"):
        raise ValueError(
            f"RGB values such as {value!r} are not supported; use a color name, "
            "'default', or a number from 0 to 255"
        )
    raise ValueError(f"unsupported color {value!r}; use one of {', '.join(COLOR_NAMES)} or 0-255")


def color_index(name: str) -> int:
    """Return the curses color number for a canonical name; -1 means terminal default."""
    return _NAME_TO_INDEX[name] if name in _NAME_TO_INDEX else int(name)


def color_label(name: str) -> str:
    return name if name in _NAME_TO_INDEX else f"color {name}"


def color_error(value, colors: int | None = None) -> str | None:
    """Non-raising validation for an editor field. Capability is advisory only."""
    try:
        name = canonical_color(value)
    except ValueError as error:
        return str(error)
    if colors is None:
        return None
    index = color_index(name)
    if index >= colors:
        return f"{color_label(name)} needs {index + 1} colors; this terminal reports {colors}"
    return None


def _canonical_list(value, *, what: str) -> tuple[str, ...]:
    values = value if isinstance(value, list) else [value]
    if not values or len(values) > MAX_FALLBACKS:
        raise ValueError(f"{what}: expected 1 to {MAX_FALLBACKS} colors")
    try:
        return tuple(canonical_color(item) for item in values)
    except ValueError as error:
        raise ValueError(f"{what}: {error}") from error


def _canonical_attributes(value, *, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > len(ATTRIBUTES):
        raise ValueError(f"{what}: expected a list of attribute names")
    result = []
    for item in value:
        if not isinstance(item, str) or item.strip().lower() not in ATTRIBUTES:
            raise ValueError(f"{what}: unknown attribute {item!r}; use {', '.join(ATTRIBUTES)}")
        name = item.strip().lower()
        if name not in result:
            result.append(name)
    return tuple(sorted(result, key=ATTRIBUTES.index))


@dataclass(frozen=True)
class Role:
    """One semantic slot. Colors are ordered fallbacks; the first supported wins."""

    foreground: tuple[str, ...]
    background: tuple[str, ...]
    attributes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping, *, role: str) -> Role:
        if not isinstance(data, Mapping):
            raise ValueError(f"{role}: expected a table of foreground/background/attributes")
        unknown = set(data) - {"foreground", "background", "attributes"}
        if unknown:
            raise ValueError(f"{role}: unknown option {next(iter(sorted(unknown)))!r}")
        return cls(
            _canonical_list(data.get("foreground", DEFAULT_COLOR), what=f"{role}.foreground"),
            _canonical_list(data.get("background", DEFAULT_COLOR), what=f"{role}.background"),
            _canonical_attributes(data.get("attributes", []), what=f"{role}.attributes"),
        )

    def to_toml_table(self, role: str) -> str:
        def emit(names: tuple[str, ...]) -> str:
            values = [name if name not in _NAME_TO_INDEX else json.dumps(name) for name in names]
            return values[0] if len(values) == 1 else "[" + ", ".join(values) + "]"

        return (
            f"[{role}]\n"
            f"foreground = {emit(self.foreground)}\n"
            f"background = {emit(self.background)}\n"
            f"attributes = {json.dumps(list(self.attributes))}\n"
        )


def _shipped() -> dict[str, Role]:
    # Exactly the pairs sidebar.py has always installed. The second entry of each
    # list is the basic-palette choice: nearest-color approximation would pick
    # black for 238 and green for 108, silently changing the shipped appearance.
    return {
        "normal": Role((DEFAULT_COLOR,), (DEFAULT_COLOR,), ()),
        "active": Role(("231", "white"), ("238", "blue"), ()),
        "accent": Role(("108", "cyan"), (DEFAULT_COLOR,), ()),
        "muted": Role(("245", "white"), (DEFAULT_COLOR,), ()),
    }


@dataclass(frozen=True)
class Palette:
    """Colors resolved for one terminal. Built once; never recomputed per frame."""

    colors: int
    entries: Mapping[str, tuple[int, int, tuple[str, ...]]]
    fallbacks: tuple[str, ...] = ()
    _styles: dict[str, int] = field(default_factory=dict, compare=False, repr=False)
    _installed: dict[str, tuple[int, int]] = field(default_factory=dict, compare=False, repr=False)
    _repaired: set[str] = field(default_factory=set, compare=False, repr=False)

    def pair(self, role: str) -> int:
        return ROLES.index(role) + 1

    def style(self, role: str) -> int:
        """Precomputed curses attribute. install() must run first."""
        if role not in self._styles:
            raise ThemeError(f"palette role {role!r} is not installed")
        return self._styles[role]

    def installed(self, role: str) -> tuple[int, int]:
        """The numbers actually handed to init_pair, which entries may not equal.

        On a terminal without default-color support, -1 is substituted before the
        call, so this is the honest answer about what the user is looking at.
        """
        if role not in self._installed:
            raise ThemeError(f"palette role {role!r} is not installed")
        return self._installed[role]

    def describe(self, role: str) -> str:
        foreground, background, attributes = self.entries[role]
        text = f"{_describe_color(foreground)} on {_describe_color(background)}"
        if attributes:
            text += " " + "+".join(attributes)
        if role in self.fallbacks:
            text += " (shipped fallback; configured colors were identical)"
        elif role in self._repaired:
            text += " (adjusted; this terminal has no default-color support)"
        elif self.colors < 256:
            text += f" ({self.colors}-color fallback)"
        return text

    def install(self, curses) -> None:
        """Install pairs 1-4 once. On failure, restore the pairs already in use."""
        previous = dict(_INSTALLED)
        curses.start_color()
        with_default = True
        try:
            curses.use_default_colors()
        except curses.error:
            # Without default-color support, -1 is not a usable color number.
            with_default = False
        applied: dict[str, int] = {}
        installed: dict[str, tuple[int, int]] = {}
        repaired: set[str] = set()
        try:
            for role in ROLES:
                foreground, background, attributes = self.entries[role]
                if not with_default:
                    # Substituting concrete colors for -1 can collapse a pair that
                    # was legible while the terminal supplied its own defaults, so
                    # the legibility guard has to run again on these numbers. Only
                    # one side can have been substituted here, and it is the side
                    # to move: the other is the user's explicit choice.
                    substituted_foreground = foreground < 0
                    foreground = SUBSTITUTE_FOREGROUND if foreground < 0 else foreground
                    substituted_background = background < 0
                    background = SUBSTITUTE_BACKGROUND if background < 0 else background
                    if foreground == background:
                        if substituted_background:
                            background = (
                                SUBSTITUTE_BACKGROUND
                                if foreground == SUBSTITUTE_FOREGROUND
                                else SUBSTITUTE_FOREGROUND
                            )
                        elif substituted_foreground:
                            foreground = (
                                SUBSTITUTE_BACKGROUND
                                if background == SUBSTITUTE_FOREGROUND
                                else SUBSTITUTE_FOREGROUND
                            )
                        else:
                            foreground = SUBSTITUTE_FOREGROUND
                            background = SUBSTITUTE_BACKGROUND
                        repaired.add(role)
                curses.init_pair(self.pair(role), foreground, background)
                installed[role] = (foreground, background)
                style = curses.color_pair(self.pair(role))
                for name in attributes:
                    style |= getattr(curses, f"A_{name.upper()}", 0)
                applied[role] = style
        except (curses.error, OSError, ValueError) as error:
            for role, entry in previous.items():
                with contextlib.suppress(curses.error, OSError, ValueError):
                    curses.init_pair(self.pair(role), entry[0], entry[1])
            raise ThemeError(
                f"cannot install theme colors: {error}. The previous colors were kept."
            ) from error
        self._styles.clear()
        self._styles.update(applied)
        self._installed.clear()
        self._installed.update(installed)
        self._repaired.clear()
        self._repaired.update(repaired)
        _INSTALLED.clear()
        _INSTALLED.update(installed)


# The pairs currently installed in this process, so a failed apply can roll back.
_INSTALLED: dict[str, tuple[int, int]] = {}


def _describe_color(index: int) -> str:
    if index < 0:
        return "terminal default"
    return _INDEX_TO_NAME.get(index, f"color {index}")


def _supported(name: str, colors: int) -> bool:
    index = color_index(name)
    return index < 0 or index < colors


def _nearest(name: str, colors: int) -> int:
    """Deterministic last resort once every configured fallback is unsupported."""
    index = color_index(name)
    if index < 0 or index < colors:
        return index
    if colors < 8:
        # Below the documented floor there is no color to choose; the terminal's
        # own default is the only legible answer.
        return -1
    if 8 <= index < 16:
        # A bright color on an eight-color terminal drops to its base; the caller
        # adds bold, which is how such terminals have always shown brightness.
        return index - 8
    target = _rgb(index)
    limit = 16 if colors >= 16 else 8
    return min(range(limit), key=lambda n: _distance(target, _SYSTEM_RGB[n]))


def _distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> int:
    # Weighted sRGB difference; good enough to pick among sixteen fixed colors.
    return sum(weight * (a - b) ** 2 for weight, a, b in zip((3, 6, 1), left, right, strict=True))


@dataclass(frozen=True)
class Theme:
    """An immutable, validated set of semantic roles."""

    roles: Mapping[str, Role]

    @classmethod
    def from_dict(cls, data: Mapping) -> Theme:
        if not isinstance(data, Mapping):
            raise ValueError("theme must be a TOML table")
        unknown = set(data) - set(ROLES)
        if unknown:
            raise ValueError(
                f"unknown theme section {next(iter(sorted(unknown)))!r}; use {', '.join(ROLES)}"
            )
        roles = _shipped()
        for role in ROLES:
            if role in data:
                roles[role] = Role.from_dict(data[role], role=role)
        return cls(MappingProxyType(roles))

    def with_role(self, role: str, **changes) -> Theme:
        """Return a copy for preview or editing. Pure; the original is unchanged."""
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}; use {', '.join(ROLES)}")
        unknown = set(changes) - {"foreground", "background", "attributes"}
        if unknown:
            raise ValueError(f"{role}: unknown option {sorted(unknown)[0]!r}")
        current = self.roles[role]
        updated = Role(
            _canonical_list(
                changes.get("foreground", list(current.foreground)), what=f"{role}.foreground"
            ),
            _canonical_list(
                changes.get("background", list(current.background)), what=f"{role}.background"
            ),
            _canonical_attributes(
                list(changes.get("attributes", list(current.attributes))),
                what=f"{role}.attributes",
            ),
        )
        return Theme(MappingProxyType(dict(self.roles) | {role: updated}))

    def to_toml(self) -> str:
        header = (
            "# tmux-workspaces viewer colors. Names, 'default' or 0-255; RGB is not\n"
            "# supported. Lists are ordered fallbacks: the first value this terminal\n"
            "# supports wins. Fonts and shell colors stay under terminal control.\n"
        )
        return header + "\n" + "\n".join(self.roles[role].to_toml_table(role) for role in ROLES)

    def resolve(self, colors: int) -> Palette:
        """Resolve against a real palette size. Never raises: colors always render."""
        colors = max(int(colors), 0)
        entries: dict[str, tuple[int, int, tuple[str, ...]]] = {}
        fallbacks: list[str] = []
        shipped = _shipped()
        for role in ROLES:
            configured = self.roles[role]
            foreground, extra = _resolve_color(configured.foreground, colors)
            background, _ = _resolve_color(configured.background, colors)
            attributes = tuple(
                name for name in ATTRIBUTES if name in configured.attributes or name in extra
            )
            if foreground >= 0 and foreground == background:
                # Reverse cannot rescue this: swapping identical colors leaves the
                # same invisible pair. Fall back to the shipped role instead.
                safe = shipped[role]
                foreground, _ = _resolve_color(safe.foreground, colors)
                background, _ = _resolve_color(safe.background, colors)
                attributes = safe.attributes
                fallbacks.append(role)
            entries[role] = (foreground, background, attributes)
        return Palette(colors, MappingProxyType(entries), tuple(fallbacks))


def _resolve_color(names: tuple[str, ...], colors: int) -> tuple[int, frozenset[str]]:
    for name in names:
        if _supported(name, colors):
            return color_index(name), frozenset()
    index = color_index(names[-1])
    nearest = _nearest(names[-1], colors)
    # Dropping a bright color to its base loses the brightness; bold restores it.
    extra = frozenset({"bold"}) if 8 <= index < 16 and nearest == index - 8 else frozenset()
    return nearest, extra


DEFAULT_THEME = Theme(MappingProxyType(_shipped()))


def _read_file(path: Path) -> bytes:
    """Read a regular file, refusing anything that could block viewer startup.

    Opening a FIFO for reading blocks until a writer appears, so a config path
    pointing at one would hang the viewer before it drew a frame. O_NONBLOCK
    makes the open return immediately and fstat then rejects anything that is
    not a regular file. Symlinks are followed deliberately: people symlink their
    dotfiles, and a link to a regular file is a regular file here.
    """
    handle = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        chunks, total = [], 0
        while total <= MAX_THEME_BYTES:
            chunk = os.read(handle, 4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)[: MAX_THEME_BYTES + 1]
    finally:
        os.close(handle)


class ThemeError(ValueError):
    """Actionable failure that never asks the caller to discard its working theme."""


class ThemeConflict(ThemeError):
    """The file changed since it was read; the caller keeps its edits."""


@dataclass(frozen=True)
class ThemeLoad:
    """A theme plus the diagnostic to show. Loading never blocks the viewer."""

    theme: Theme
    path: Path
    diagnostic: str | None = None


def theme_path(path=None, environ: Mapping[str, str] | None = None, cwd: Path | None = None):
    """Resolve which file the theme comes from and where Save writes.

    Pure: no filesystem access, no existence check, and no error for a missing
    file, so it is meaningful before any theme has been saved.

    The launcher resolves this to an absolute path and transports it rather than
    letting the viewer resolve it again. HOME and XDG_CONFIG_HOME do survive into
    the viewer's own tmux server, which starts with ``clean_env() |
    shell_context()``, but ``TMUX_WORKSPACES_THEME`` is not on that allowlist and
    the viewer's working directory is not the one you launched from, so a
    re-resolution there could name a different file.
    """
    env = os.environ if environ is None else environ
    if not path:
        path = env.get("TMUX_WORKSPACES_THEME")
    if not path:
        home = Path(env.get("HOME") or Path.home())
        config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
        path = config / "tmux-workspaces" / "theme.toml"
    selected = Path(path)
    text = str(selected)
    if text == "~" or text.startswith("~/"):
        selected = Path(env.get("HOME") or Path.home()) / text[2:]
    if not selected.is_absolute():
        selected = (Path.cwd() if cwd is None else Path(cwd)) / selected
    return selected


def parse_theme(payload: bytes) -> Theme:
    """Decode and validate file bytes. Raises ValueError; callers may degrade."""
    if len(payload) > MAX_THEME_BYTES:
        raise ValueError(f"theme exceeds {MAX_THEME_BYTES} bytes")
    return Theme.from_dict(tomllib.loads(payload.decode("utf-8")))


def parse_theme_state(state: str) -> Theme:
    """Rebuild a theme from a validated snapshot of its TOML.

    Unused by the current launcher, which transports an absolute path and lets
    the viewer read the file itself; this is the parser a snapshot transport
    would need, and it makes no claim about how the viewer is started today.
    """
    payload = state.encode("utf-8")
    if len(payload) > MAX_THEME_BYTES:
        raise ValueError(f"theme snapshot exceeds {MAX_THEME_BYTES} bytes")
    return parse_theme(payload)


def load_theme(
    path=None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    working: Theme | None = None,
) -> ThemeLoad:
    """Read the theme once at launch. Never raises: colors must not block shells."""
    selected = theme_path(path, environ=environ, cwd=cwd)
    base = DEFAULT_THEME if working is None else working
    try:
        payload = _read_file(selected)
    except FileNotFoundError:
        return ThemeLoad(base, selected)
    except OSError as error:
        return ThemeLoad(base, selected, f"{error.strerror or error}: {selected}")
    try:
        return ThemeLoad(parse_theme(payload), selected)
    except (ValueError, UnicodeDecodeError) as error:
        return ThemeLoad(base, selected, f"{error}; using current colors ({selected})")


class ThemeFile:
    """Reads and conflict-checked atomic writes for one theme file."""

    def __init__(self, path):
        self.path = Path(path)
        self._digest: str | None = None
        self._seen = False
        # A file that exists but could not be read is neither "never read" nor
        # "read these bytes": replacing it would destroy contents nobody saw.
        self._unreadable = False

    @staticmethod
    def _hash(payload: bytes) -> str:
        # The length is part of the digest: reads stop one byte past the cap, so
        # without it two different oversized files could share a prefix hash.
        return hashlib.sha256(f"{len(payload)}:".encode() + payload).hexdigest()

    def read(self) -> ThemeLoad:
        """Record the bytes seen even when they are invalid, so Apply can repair them."""
        try:
            payload = _read_file(self.path)
        except FileNotFoundError:
            self._digest, self._seen, self._unreadable = None, True, False
            return ThemeLoad(DEFAULT_THEME, self.path)
        except OSError as error:
            self._digest, self._seen, self._unreadable = None, False, True
            return ThemeLoad(DEFAULT_THEME, self.path, f"{error.strerror}: {self.path}")
        self._digest, self._seen, self._unreadable = self._hash(payload), True, False
        try:
            return ThemeLoad(parse_theme(payload), self.path)
        except (ValueError, UnicodeDecodeError) as error:
            # A file too large to digest safely cannot be replaced either, so
            # promise a repair only where write() can actually perform one.
            repair = (
                "shrink it below the size limit before saving"
                if len(payload) > MAX_THEME_BYTES
                else "saving will replace it"
            )
            return ThemeLoad(DEFAULT_THEME, self.path, f"{error}; {repair} ({self.path})")

    def writable(self) -> bool:
        """Whether a Save can be offered; a race still surfaces as ThemeError."""
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            parent = self.path.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            return os.access(parent, os.W_OK | os.X_OK)
        except OSError:
            return False
        if not stat.S_ISREG(info.st_mode):
            return False
        return os.access(self.path, os.W_OK)

    def _check_target(self) -> None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise ThemeError(f"Theme {self.path}: {error.strerror}") from error
        if stat.S_ISLNK(info.st_mode):
            raise ThemeError(
                "Saving would replace a symbolic link, so nothing was written. Save to "
                f"a regular file instead of {self.path}."
            )
        if not stat.S_ISREG(info.st_mode):
            raise ThemeError(f"Not a regular file, so it was not replaced: {self.path}")
        if not os.access(self.path, os.W_OK):
            raise ThemeError(
                "The theme file is read-only. Your colors are unchanged; fix the "
                f"permissions of {self.path} or choose another path with --theme."
            )

    def _conflict(self) -> None:
        """Compare the bytes on disk with those last read. Call under the lock."""
        if self._unreadable:
            # Permissions can change between the read and the save, so the rule
            # is stated here rather than left to whether the re-read happens to
            # fail again.
            raise ThemeError(
                "The theme file could not be read, so it was not replaced. Your colors "
                f"are unchanged; fix the permissions of {self.path}, then apply again."
            )
        try:
            payload = _read_file(self.path)
        except FileNotFoundError:
            current = None
        except OSError as error:
            raise ThemeError(f"{error.strerror}: {self.path}") from error
        else:
            if len(payload) > MAX_THEME_BYTES:
                raise ThemeError(
                    f"The theme file is larger than {MAX_THEME_BYTES} bytes, so a "
                    "concurrent edit cannot be detected safely. Your colors are unchanged; "
                    f"shrink or remove {self.path}, then apply again."
                )
            current = self._hash(payload)
        if self._seen and current != self._digest:
            raise ThemeConflict(
                "The theme file changed on disk since it was read. Your colors are "
                "unchanged; reopen the colors to see the new file, then apply again. "
                f"({self.path})"
            )

    def write(self, theme: Theme) -> None:
        """Replace the file atomically, refusing unsafe targets and concurrent edits."""
        payload = theme.to_toml().encode("utf-8")
        if len(payload) > MAX_THEME_BYTES:
            raise ThemeError(f"Theme exceeds {MAX_THEME_BYTES} bytes; remove some values.")
        self._check_target()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as error:
            raise ThemeError(
                f"{error.strerror}: {self.path.parent}. Your colors are unchanged."
            ) from error
        lock = self._lock()
        try:
            # Everything that decides whether replacing is safe happens here,
            # while the lock is held, and the digest is checked once more with
            # the replacement already on disk.
            self._check_target()
            self._conflict()
            temporary = None
            try:
                with NamedTemporaryFile(
                    dir=self.path.parent, prefix=".theme-", suffix=".toml", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                self._conflict()
                os.replace(temporary, self.path)
                temporary = None
            finally:
                if temporary is not None:
                    with contextlib.suppress(OSError):
                        temporary.unlink()
        except OSError as error:
            raise ThemeError(
                f"{error.strerror or error}: {self.path}. Your colors are unchanged."
            ) from error
        finally:
            self._release(lock)
        self._digest, self._seen, self._unreadable = self._hash(payload), True, False

    def _lock(self):
        """Serialize this application's writers on a stable sibling lock file.

        The lock cannot live on the theme file itself: write() replaces that
        inode, so two writers would end up holding locks on different inodes and
        a first save to a missing file would take no lock at all. The sibling
        file is never replaced, so its inode is stable. flock is non-blocking and
        bounded, because a viewer that blocks here stops drawing. External
        editors do not take this lock, so the byte-level digest comparison, not
        the lock, is what actually detects a concurrent edit.
        """
        try:
            import fcntl
        except ImportError:
            return None
        target = self.path.with_name("." + self.path.name + ".lock")
        try:
            handle = os.open(target, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise ThemeError(
                f"Theme lock {target}: {error.strerror}. Your colors are unchanged."
            ) from error
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except OSError as error:
                if error.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    os.close(handle)
                    raise ThemeError(
                        f"Theme lock {target}: {error.strerror}. Your colors are unchanged."
                    ) from error
                if time.monotonic() >= deadline:
                    os.close(handle)
                    raise ThemeError(
                        "The theme file is being saved by another window. Your colors "
                        "are unchanged; try again in a moment."
                    ) from error
                time.sleep(LOCK_POLL_SECONDS)

    @staticmethod
    def _release(handle) -> None:
        if handle is None:
            return
        with contextlib.suppress(OSError):
            os.close(handle)
