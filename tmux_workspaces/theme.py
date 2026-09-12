"""Validated semantic viewer colors resolved against real terminal capability.

Files are TOML data naming four semantic roles and a panel color, never terminal
escape sequences. A file may start from a named preset and override any part of
it. The panel is painted by tmux rather than curses, so it may be an RGB value.
This module never imports curses: the caller injects the module, as
``preflight.load_curses`` does, so resolution and pair installation stay testable
without a terminal. Roles map one-to-one onto curses pairs 1-5, which keeps the
documented startup floor of eight colors and six pairs intact.

The viewer styles its own layer only. Fonts, glyph availability and the colors a
shell program prints remain the terminal's, because a curses application cannot
choose a font; see docs for that boundary.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType

from .configfile import ConfigConflict, ConfigError, ConfigFile, Vocabulary, read_file

MAX_THEME_BYTES = 16 * 1024
MAX_FALLBACKS = 8
SUBSTITUTE_FOREGROUND = 7
SUBSTITUTE_BACKGROUND = 0

# Order is the curses pair number minus one, matching the pairs the viewer has
# always installed: normal=1, active=2, accent=3, muted=4.
ROLES = ("normal", "active", "accent", "muted", "outline")
ROLE_LABELS = MappingProxyType(
    {
        "normal": "Normal",
        "active": "Selected",
        "accent": "Accent",
        "muted": "Muted",
        "outline": "Outline",
    }
)
ROLE_DETAILS = MappingProxyType(
    {
        "normal": "Body text and buttons, drawn over the panel.",
        "active": "The selected tab and the inline name editor.",
        "accent": (
            "Hints, the tab detail row, the selection marker and error text. Error text "
            "shares this pair, so it always adds bold: the colour alone cannot "
            "distinguish it."
        ),
        "muted": "Section labels, tab numbers and counts.",
        "outline": (
            "The panel's perimeter, drawn on the surface, and the separators between split panes."
        ),
    }
)
ATTRIBUTES = ("bold", "dim", "reverse", "standout", "underline")
_BASIC_NAMES = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
COLOR_NAMES = ("default", *_BASIC_NAMES, *(f"bright-{name}" for name in _BASIC_NAMES))
COLOR_CHOICES = COLOR_NAMES
DEFAULT_COLOR = "default"

_HEX_COLOR = re.compile(r"#[0-9a-f]{6}")
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
    if _HEX_COLOR.fullmatch(text):
        # An exact color. It needs a 256-color pane, where it takes one of the
        # pane's own palette slots; a fallback list covers smaller palettes.
        return text
    if text.startswith("#") or text.startswith("0x") or text.startswith("rgb"):
        raise ValueError(
            f"{value!r} is not a color; use '#rrggbb', a color name, "
            "'default', or a number from 0 to 255"
        )
    raise ValueError(f"unsupported color {value!r}; use one of {', '.join(COLOR_NAMES)} or 0-255")


def is_rgb(name: str) -> bool:
    return name.startswith("#")


def color_index(name: str) -> int:
    """Return the curses color number for a canonical name; -1 means terminal default.

    An RGB value has no fixed number: ``Theme.resolve`` gives it a palette slot.
    """
    if is_rgb(name):
        raise ValueError(f"{name} is an RGB color; it is given a slot when a theme resolves")
    return _NAME_TO_INDEX[name] if name in _NAME_TO_INDEX else int(name)


def _hex_rgb(name: str) -> tuple[int, int, int]:
    return int(name[1:3], 16), int(name[3:5], 16), int(name[5:7], 16)


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
    if is_rgb(name):
        if colors < 256:
            return f"{name} needs a 256-color terminal; this terminal reports {colors}"
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
    def from_dict(cls, data: Mapping, *, role: str, base: Role | None = None) -> Role:
        """Read a role table. Keys left out keep ``base``'s values, so a table
        naming only a foreground changes only that; without a base they are
        the terminal's defaults."""
        if not isinstance(data, Mapping):
            raise ValueError(f"{role}: expected a table of foreground/background/attributes")
        unknown = set(data) - {"foreground", "background", "attributes"}
        if unknown:
            raise ValueError(f"{role}: unknown option {next(iter(sorted(unknown)))!r}")
        base = base or cls((DEFAULT_COLOR,), (DEFAULT_COLOR,), ())
        return cls(
            _canonical_list(
                data.get("foreground", list(base.foreground)), what=f"{role}.foreground"
            ),
            _canonical_list(
                data.get("background", list(base.background)), what=f"{role}.background"
            ),
            _canonical_attributes(
                data.get("attributes", list(base.attributes)), what=f"{role}.attributes"
            ),
        )

    def to_toml_table(self, role: str, *, inherit_background: bool = False) -> str:
        def emit(names: tuple[str, ...]) -> str:
            values = [name if name.isdigit() else json.dumps(name) for name in names]
            return values[0] if len(values) == 1 else "[" + ", ".join(values) + "]"

        return (
            f"[{role}]\n"
            f"foreground = {emit(self.foreground)}\n"
            + ("" if inherit_background else f"background = {emit(self.background)}\n")
            + f"attributes = {json.dumps(list(self.attributes))}\n"
        )


# The panel: the sidebar's own background and the band between panes. tmux
# paints it, so unlike the roles it may be an RGB value; tmux approximates it
# for a terminal without truecolor. ``default`` leaves the terminal's own.
DEFAULT_PANEL = "default"


def canonical_panel(value, what: str = "panel") -> str:
    """The tmux spelling of a surface color: default, a name, 0-255 or #rrggbb."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{what}: expected 'default', a color name, 0-255 or '#rrggbb'")
    if isinstance(value, int):
        if not 0 <= value <= 255:
            raise ValueError(f"{what}: color index {value} is outside 0-255")
        return f"colour{value}"
    text = value.strip().lower().replace("_", "-")
    if _HEX_COLOR.fullmatch(text):
        return text
    if text.isdigit():
        return canonical_panel(int(text), what)
    if text == DEFAULT_PANEL:
        return DEFAULT_PANEL
    if text.startswith("colour") and text[6:].isdigit():
        return canonical_panel(int(text[6:]), what)
    if text.startswith("bright") and text[6:] in _BASIC_NAMES:
        return text
    if text in _NAME_TO_INDEX:
        return text.replace("bright-", "bright")
    raise ValueError(
        f"{what}: unsupported color {value!r}; use 'default', a color name, 0-255 or '#rrggbb'"
    )


def tmux_spelling(name: str) -> str:
    """A canonical role color as tmux writes it, for the separators between panes."""
    if name == DEFAULT_COLOR or is_rgb(name):
        return name
    if name.isdigit():
        return f"colour{name}"
    return name.replace("bright-", "bright")


# Named starting points. The second entry of each color list is the deliberate
# basic-palette choice: nearest-color approximation would pick black for 238 and
# green for 108, silently changing the look on an eight-color terminal. Every
# preset keeps the terminal's font and the colors shell programs print.
# Each preset: name, description, panel (the sidebar's ground), surface (the
# terminals' ground) and the roles. The shipped palette is the one the owner
# specified from VS Code: surface #292c33, panel #22252b, outline #31343b,
# selection #343841, text #cccccc, secondary #999999, accent #608af7.
_DARK_PANEL, _DARK_SURFACE = "#22252b", "#292c33"
_LIGHT_PANEL, _LIGHT_SURFACE = "#f8f8f8", "#ffffff"
_PRESETS: tuple[tuple[str, str, str, str, dict[str, Role]], ...] = (
    (
        "default",
        "A slate panel inset in a slightly lighter surface, grey text, blue accent",
        _DARK_PANEL,
        _DARK_SURFACE,
        {
            "normal": Role(("#cccccc", "252", "white"), (_DARK_PANEL, "235", "black"), ()),
            "active": Role(("#cccccc", "252", "white"), ("#343841", "237", "blue"), ()),
            "accent": Role(("#608af7", "69", "cyan"), (_DARK_PANEL, "235", "black"), ()),
            "muted": Role(("#999999", "246", "white"), (_DARK_PANEL, "235", "black"), ()),
            "outline": Role(("#31343b", "237", "white"), (_DARK_SURFACE, "236", "black"), ()),
        },
    ),
    (
        "plain",
        "The same colors on the terminal's own background, no panel or padding",
        DEFAULT_PANEL,
        DEFAULT_PANEL,
        {
            "normal": Role(("#cccccc", "252", "white"), (DEFAULT_COLOR,), ()),
            "active": Role(("#cccccc", "252", "white"), ("#343841", "237", "blue"), ()),
            "accent": Role(("#608af7", "69", "cyan"), (DEFAULT_COLOR,), ()),
            "muted": Role(("#999999", "246", "white"), (DEFAULT_COLOR,), ()),
            "outline": Role(("#31343b", "237", "white"), (DEFAULT_COLOR,), ()),
        },
    ),
    (
        "forest",
        "Terminal background with a sage-green accent",
        DEFAULT_PANEL,
        DEFAULT_PANEL,
        {
            "normal": Role((DEFAULT_COLOR,), (DEFAULT_COLOR,), ()),
            "active": Role(("231", "white"), ("238", "blue"), ()),
            "accent": Role(("108", "cyan"), (DEFAULT_COLOR,), ()),
            "muted": Role(("245", "white"), (DEFAULT_COLOR,), ()),
            "outline": Role(("238", "white"), (DEFAULT_COLOR,), ()),
        },
    ),
    (
        "paper",
        "VS Code Light Modern: a pale panel on white, dark text, a blue accent",
        _LIGHT_PANEL,
        _LIGHT_SURFACE,
        {
            "normal": Role(("#3b3b3b", "237", "black"), (_LIGHT_PANEL, "255", "white"), ()),
            "active": Role(("#3b3b3b", "237", "black"), ("#e8e8e8", "254", "cyan"), ()),
            "accent": Role(("#005fb8", "25", "blue"), (_LIGHT_PANEL, "255", "white"), ()),
            "muted": Role(("#6e6e6e", "243", "black"), (_LIGHT_PANEL, "255", "white"), ()),
            "outline": Role(("#e5e5e5", "254", "black"), (_LIGHT_SURFACE, "231", "white"), ()),
        },
    ),
    (
        "mono",
        "The terminal's own two colors, with bold, dim and reverse only",
        DEFAULT_PANEL,
        DEFAULT_PANEL,
        {
            "normal": Role((DEFAULT_COLOR,), (DEFAULT_COLOR,), ()),
            "active": Role((DEFAULT_COLOR,), (DEFAULT_COLOR,), ("reverse",)),
            "accent": Role((DEFAULT_COLOR,), (DEFAULT_COLOR,), ("bold",)),
            "muted": Role((DEFAULT_COLOR,), (DEFAULT_COLOR,), ("dim",)),
            "outline": Role((DEFAULT_COLOR,), (DEFAULT_COLOR,), ("dim",)),
        },
    ),
)
PRESET_NAMES: tuple[str, ...] = tuple(name for name, *_ in _PRESETS)
PRESET_DETAILS = MappingProxyType({name: detail for name, detail, *_ in _PRESETS})
_PRESET_ROLES = MappingProxyType({name: roles for name, _, _, _, roles in _PRESETS})
_PRESET_PANELS = MappingProxyType({name: panel for name, _, panel, _, _ in _PRESETS})
_PRESET_SURFACES = MappingProxyType({name: surface for name, _, _, surface, _ in _PRESETS})
DEFAULT_PRESET = PRESET_NAMES[0]


def _shipped() -> dict[str, Role]:
    return dict(_PRESET_ROLES[DEFAULT_PRESET])


def preset_theme(name: str) -> Theme:
    """The theme a preset names. Raises ValueError for an unknown name."""
    if not isinstance(name, str) or name.strip().lower() not in _PRESET_ROLES:
        raise ValueError(f"unknown preset {name!r}; use one of {', '.join(PRESET_NAMES)}")
    key = name.strip().lower()
    return Theme(
        MappingProxyType(dict(_PRESET_ROLES[key])),
        _PRESET_PANELS[key],
        _PRESET_SURFACES[key],
        frozenset(
            role
            for role, value in _PRESET_ROLES[key].items()
            if role != "outline" and tmux_spelling(value.background[0]) == _PRESET_PANELS[key]
        ),
        key,
        frozenset(
            role
            for role, value in _PRESET_ROLES[key].items()
            if role == "outline" and tmux_spelling(value.background[0]) == _PRESET_SURFACES[key]
        ),
    )


def tmux_color(index: int) -> str:
    """The tmux spelling of an installed curses color number."""
    return "default" if index < 0 else f"colour{index}"


@dataclass(frozen=True)
class Palette:
    """Colors resolved for one terminal. Built once; never recomputed per frame."""

    colors: int
    entries: Mapping[str, tuple[int, int, tuple[str, ...]]]
    fallbacks: tuple[str, ...] = ()
    # RGB colors by the palette slot each takes in the pane.
    rgb: Mapping[int, str] = field(default_factory=dict)
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

    def describe_color(self, index: int) -> str:
        return self.rgb.get(index) or _describe_color(index)

    def describe(self, role: str) -> str:
        foreground, background, attributes = self.entries[role]
        text = f"{self.describe_color(foreground)} on {self.describe_color(background)}"
        if attributes:
            text += " " + "+".join(attributes)
        if role in self.fallbacks:
            text += " (shipped fallback; configured colors were identical)"
        elif role in self._repaired:
            text += " (adjusted; this terminal has no default-color support)"
        elif self.colors < 256:
            text += f" ({self.colors}-color fallback)"
        return text

    def install(self, curses, write=None, *, previous_rgb=()) -> None:
        """Install pairs 1-4 once. On failure, restore the pairs already in use.

        ``write`` sends text to the pane's terminal; it receives the palette
        definitions for any RGB colors. Without it, such colors show as
        whatever the slots held, so a viewer always passes one. ``previous_rgb``
        identifies this pane's old RGB overrides; released slots are reset before
        the new palette is displayed.
        """
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
        if write is not None:
            released = sorted(set(previous_rgb) - set(self.rgb))
            sequence = "".join(f"\x1b]104;{slot}\x1b\\" for slot in released)
            sequence += palette_sequence({name: slot for slot, name in self.rgb.items()})
            if sequence:
                write(sequence)


# The pairs currently installed in this process, so a failed apply can roll back.
_INSTALLED: dict[str, tuple[int, int]] = {}


def _describe_color(index: int) -> str:
    if index < 0:
        return "terminal default"
    return _INDEX_TO_NAME.get(index, f"color {index}")


def _supported(name: str, colors: int) -> bool:
    if is_rgb(name):
        return colors >= 256
    index = color_index(name)
    return index < 0 or index < colors


def _nearest(name: str, colors: int) -> int:
    """Deterministic last resort once every configured fallback is unsupported."""
    if colors < 8:
        # Below the documented floor there is no color to choose; the terminal's
        # own default is the only legible answer.
        return -1
    if is_rgb(name):
        target = _hex_rgb(name)
        limit = 16 if colors >= 16 else 8
        return min(range(limit), key=lambda n: _distance(target, _SYSTEM_RGB[n]))
    index = color_index(name)
    if index < 0 or index < colors:
        return index
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
    """An immutable, validated set of semantic roles plus two grounds: the
    panel the sidebar sits on and the surface the terminals sit on."""

    roles: Mapping[str, Role]
    panel: str = DEFAULT_PANEL
    surface: str = DEFAULT_PANEL
    # Retain inheritance through Save/reopen; explicit role backgrounds stay fixed.
    _panel_roles: frozenset[str] = field(default=frozenset(), repr=False)
    _base_preset: str = field(default=DEFAULT_PRESET, compare=False, repr=False)
    _surface_roles: frozenset[str] = field(default=frozenset(), repr=False)

    @classmethod
    def from_dict(cls, data: Mapping) -> Theme:
        if not isinstance(data, Mapping):
            raise ValueError("theme must be a TOML table")
        unknown = set(data) - set(ROLES) - {"preset", "panel", "surface"}
        if unknown:
            raise ValueError(
                f"unknown theme section {next(iter(sorted(unknown)))!r}; "
                f"use preset, panel, surface, {', '.join(ROLES)}"
            )
        # A preset is the starting point; a ground value or role table overrides it.
        base = preset_theme(data["preset"]) if "preset" in data else DEFAULT_THEME
        if "panel" in data:
            base = base.with_panel(data["panel"])
        if "surface" in data:
            base = base.with_surface(data["surface"])
        roles = dict(base.roles)
        panel_roles = set(base._panel_roles)
        surface_roles = set(base._surface_roles)
        for role in ROLES:
            if role in data:
                roles[role] = Role.from_dict(data[role], role=role, base=roles[role])
                if "background" in data[role]:
                    panel_roles.discard(role)
                    surface_roles.discard(role)
        panel = base.panel
        surface = base.surface
        return cls(
            MappingProxyType(roles),
            panel,
            surface,
            frozenset(panel_roles),
            base._base_preset,
            frozenset(surface_roles),
        )

    def _with_ground(self, value, ground: str, inherited: frozenset[str]) -> Theme:
        color = canonical_panel(value, ground)
        if color == getattr(self, ground):
            return self
        role_color = (
            color[6:]
            if color.startswith("colour")
            else next((name for name in COLOR_NAMES if tmux_spelling(name) == color), color)
        )
        preset_ground = (_PRESET_PANELS if ground == "panel" else _PRESET_SURFACES)[
            self._base_preset
        ]
        roles = {
            role: Role(
                value.foreground,
                _PRESET_ROLES[self._base_preset][role].background
                if color == preset_ground
                else (role_color,),
                value.attributes,
            )
            if role in inherited
            else value
            for role, value in self.roles.items()
        }
        return replace(self, roles=MappingProxyType(roles), **{ground: color})

    def with_panel(self, value) -> Theme:
        """Change the panel and its inherited roles, preserving explicit backgrounds."""
        return self._with_ground(value, "panel", self._panel_roles)

    def with_surface(self, value) -> Theme:
        """Change the surface and its inherited outline background."""
        return self._with_ground(value, "surface", self._surface_roles)

    def separator(self) -> str:
        """The tmux color for the separators between split panes: the outline's
        foreground, or the panel where the outline is the terminal's own."""
        name = self.roles["outline"].foreground[0]
        return self.panel if name == DEFAULT_COLOR else tmux_spelling(name)

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
        inherited = self._panel_roles - {role} if "background" in changes else self._panel_roles
        surface_inherited = (
            self._surface_roles - {role} if "background" in changes else self._surface_roles
        )
        return replace(
            self,
            roles=MappingProxyType(dict(self.roles) | {role: updated}),
            _panel_roles=inherited,
            _surface_roles=surface_inherited,
        )

    def preset_name(self) -> str | None:
        """The preset this theme equals exactly, or None for custom colors."""
        return next((name for name in PRESET_NAMES if preset_theme(name) == self), None)

    def to_toml(self) -> str:
        header = (
            "# tmux-workspaces viewer colors. A color is '#rrggbb', a name, 'default'\n"
            "# or 0-255; a list is ordered fallbacks and the first value this terminal\n"
            "# supports wins ('#rrggbb' needs 256 colors). The panel is the sidebar's\n"
            "# ground and the surface the terminals' ground. Fonts and shell colors\n"
            "# stay under terminal control.\n"
            f"# Presets: {', '.join(PRESET_NAMES)}. panel, surface and [role] tables "
            "override a preset.\n"
        )
        preset = self.preset_name()
        if preset is not None:
            return (
                header
                + "\n"
                + f"preset = {json.dumps(preset)}\n"
                + "\n# Add a ground value or a role table below to change part of this\n"
                + "# preset, for example:\n"
                + '# panel = "#1f2430"\n'
                + "# [accent]\n"
                + '# foreground = "red"\n'
            )
        tables = "\n".join(
            self.roles[role].to_toml_table(
                role, inherit_background=role in self._panel_roles | self._surface_roles
            )
            for role in ROLES
        )
        grounds = f"panel = {json.dumps(self.panel)}\nsurface = {json.dumps(self.surface)}\n"
        return (
            header + "\n" + f"preset = {json.dumps(self._base_preset)}\n" + grounds + "\n" + tables
        )

    def resolve(self, colors: int) -> Palette:
        """Resolve against a real palette size. Never raises: colors always render."""
        colors = max(int(colors), 0)
        entries: dict[str, tuple[int, int, tuple[str, ...]]] = {}
        fallbacks: list[str] = []
        slots: dict[str, int] = {}
        shipped = _shipped()
        reserved = frozenset(
            color_index(name)
            for role in (*self.roles.values(), *shipped.values())
            for names in (role.foreground, role.background)
            for name in names
            if not is_rgb(name) and color_index(name) in RGB_SLOTS
        )
        for role in ROLES:
            configured = self.roles[role]
            foreground, extra = _resolve_color(configured.foreground, colors, slots, reserved)
            background, _ = _resolve_color(configured.background, colors, slots, reserved)
            attributes = tuple(
                name for name in ATTRIBUTES if name in configured.attributes or name in extra
            )
            if foreground >= 0 and foreground == background:
                # Reverse cannot rescue this: swapping identical colors leaves the
                # same invisible pair. Fall back to the shipped role instead.
                safe = shipped[role]
                foreground, _ = _resolve_color(safe.foreground, colors, slots, reserved)
                background, _ = _resolve_color(safe.background, colors, slots, reserved)
                attributes = safe.attributes
                fallbacks.append(role)
            entries[role] = (foreground, background, attributes)
        rgb = MappingProxyType({slot: name for name, slot in slots.items()})
        return Palette(colors, MappingProxyType(entries), tuple(fallbacks), rgb)


# Palette slots an RGB role color may take in the viewer's own pane. tmux keeps
# a palette per pane, so redefining these changes nothing outside the viewer;
# the range is one no preset names by number.
RGB_SLOTS = range(16, 24)


def _resolve_color(
    names: tuple[str, ...], colors: int, slots: dict[str, int], reserved: frozenset[int]
) -> tuple[int, frozenset[str]]:
    for name in names:
        if _supported(name, colors):
            if is_rgb(name):
                if name not in slots:
                    # A slot a role names by number is left alone, so redefining
                    # the others cannot change that role's color.
                    free = [s for s in RGB_SLOTS if s not in reserved and s not in slots.values()]
                    if not free:
                        # More exact colors than slots: the next fallback serves.
                        continue
                    slots[name] = free[0]
                return slots[name], frozenset()
            return color_index(name), frozenset()
    last = names[-1]
    nearest = _nearest(last, colors)
    if is_rgb(last):
        return nearest, frozenset()
    index = color_index(last)
    # Dropping a bright color to its base loses the brightness; bold restores it.
    extra = frozenset({"bold"}) if 8 <= index < 16 and nearest == index - 8 else frozenset()
    return nearest, extra


def palette_sequence(slots: Mapping[str, int]) -> str:
    """The OSC 4 text that defines each RGB slot in the pane that receives it."""
    return "".join(
        f"\x1b]4;{slot};rgb:{name[1:3]}/{name[3:5]}/{name[5:7]}\x1b\\"
        for name, slot in sorted(slots.items(), key=lambda item: item[1])
    )


DEFAULT_THEME = preset_theme(DEFAULT_PRESET)


def _read_file(path: Path) -> bytes:
    return read_file(path, MAX_THEME_BYTES)


class ThemeError(ConfigError):
    """Actionable failure that never asks the caller to discard its working theme."""


class ThemeConflict(ThemeError, ConfigConflict):
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


THEME_WORDS = Vocabulary(
    noun="theme",
    title="Theme",
    unchanged="Your colors are unchanged",
    reopen="reopen the colors to see the new file",
    option="--theme",
    temp_prefix=".theme-",
)


class ThemeFile(ConfigFile):
    """Reads and conflict-checked atomic writes for one theme file."""

    def __init__(self, path):
        super().__init__(
            path,
            limit=MAX_THEME_BYTES,
            words=THEME_WORDS,
            error=ThemeError,
            conflict=ThemeConflict,
        )

    def read(self) -> ThemeLoad:
        """Record the bytes seen even when they are invalid, so Apply can repair them."""
        payload, diagnostic = self.read_bytes()
        if payload is None:
            return ThemeLoad(DEFAULT_THEME, self.path, diagnostic)
        try:
            return ThemeLoad(parse_theme(payload), self.path)
        except (ValueError, UnicodeDecodeError) as error:
            # A file too large to digest safely cannot be replaced either, so
            # promise a repair only where write() can actually perform one.
            repair = (
                "shrink it below the size limit before saving"
                if self.oversized(payload)
                else "saving will replace it"
            )
            return ThemeLoad(DEFAULT_THEME, self.path, f"{error}; {repair} ({self.path})")

    def write(self, theme: Theme) -> None:
        """Replace the file atomically, refusing unsafe targets and concurrent edits."""
        self.write_bytes(theme.to_toml().encode("utf-8"))
