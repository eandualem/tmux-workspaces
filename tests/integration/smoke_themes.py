"""Viewer theme colors observed through real PTYs and real reduced terminfo.

Colors are read back from the viewer's own tmux server with `capture-pane -e`,
so every assertion is about bytes the terminal actually received rather than a
rendering guess. Reduced palettes use a compiled terminfo entry, never a patched
capability constant, so the fallback path runs exactly as it would for a user.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.integration.support import FixtureResources, wait
from tmux_workspaces.tmux import Tmux

SGR = re.compile(r"\x1b\[([0-9;]*)m")
CSI = re.compile(r"\x1b\[[0-9;:?]*[A-Za-z]")
DEFAULT = -1

# Roles are located by text the sidebar always draws, so the assertions survive
# row reordering and width changes.
ANCHORS = {
    "title": "Workspace",
    "muted": "tabs",
    "active": "▶",
    # The add button on the tabs label row; the detail row is secondary text now.
    "accent": "+",
}
# The workspace header occupies the top row, and the "Workspaces…" button
# repeats its word further down the sidebar. Searching the whole screen for the
# title would report that button's style as the header's, and would accept a
# screen whose header is not drawn yet, asserting against a partial paint.
# Match the top row instead of the row's first column: a configured background
# shifts where the captured header text begins, but never which row holds it.
FIRST_ROW_ROLES = frozenset({"title"})
_ATTRIBUTES = {1: "bold", 2: "dim", 4: "underline", 7: "reverse"}
_CLEAR = {22: ("bold", "dim"), 24: ("underline",), 27: ("reverse",)}

# The shipped appearance: pair 1 (normal) is the terminal default, so the
# panel tmux paints behind the pane shows through; 2 active, 3 accent and 4
# muted sit on that same default. The panel itself is a pane style, which a
# capture of the pane's cells does not carry.
# On 256 colors the shipped roles are exact RGB values in the pane's own
# palette slots, defined through OSC 4: 16 text, 17 panel, 18 selection,
# 19 accent, 20 secondary text, 21 outline, 22 surface.
SHIPPED_256 = {
    "title": (16, 17, ("bold",)),
    "muted": (20, 17, ()),
    "active": (16, 18, ()),
    "accent": (19, 17, ("bold",)),
}
SHIPPED_BASIC = {
    "title": (7, 0, ("bold",)),
    "muted": (7, 0, ()),
    "active": (7, 4, ()),
    "accent": (6, 0, ("bold",)),
}


class Screen:
    """Track SGR state across a whole capture, not one line at a time.

    tmux emits only the changes it needs, so a color set on one row is still in
    force on the next. Reading a line in isolation therefore reports a role as
    unstyled when nothing changed, and credits it with a later run's colors.
    This walks the capture and records the state in force at each character.
    """

    def __init__(self, text: str):
        self.foreground, self.background = DEFAULT, DEFAULT
        self.attributes: set[str] = set()
        self.lines = [self._line(line) for line in text.split("\n")]

    def _apply(self, parameters: str) -> None:
        codes = [int(part or 0) for part in parameters.split(";")] if parameters else [0]
        index = 0
        while index < len(codes):
            code = codes[index]
            if code == 0:
                self.foreground, self.background = DEFAULT, DEFAULT
                self.attributes.clear()
            elif code in _ATTRIBUTES:
                self.attributes.add(_ATTRIBUTES[code])
            elif code in _CLEAR:
                self.attributes.difference_update(_CLEAR[code])
            elif code in (38, 48) and codes[index + 1 : index + 2] == [5]:
                value = codes[index + 2] if index + 2 < len(codes) else DEFAULT
                if code == 38:
                    self.foreground = value
                else:
                    self.background = value
                index += 2
            elif 30 <= code <= 37 or 90 <= code <= 97:
                self.foreground = code - (82 if code >= 90 else 30)
            elif 40 <= code <= 47 or 100 <= code <= 107:
                self.background = code - (92 if code >= 100 else 40)
            elif code == 39:
                self.foreground = DEFAULT
            elif code == 49:
                self.background = DEFAULT
            index += 1

    def _state(self) -> tuple[int, int, tuple[str, ...]]:
        ordered = tuple(name for name in _ATTRIBUTES.values() if name in self.attributes)
        return self.foreground, self.background, ordered

    def _line(self, line: str) -> tuple[str, list]:
        text, states, index = [], [], 0
        while index < len(line):
            style = SGR.match(line, index)
            if style:
                self._apply(style.group(1))
                index = style.end()
                continue
            other = CSI.match(line, index)
            if other:
                index = other.end()
                continue
            text.append(line[index])
            states.append(self._state())
            index += 1
        return "".join(text), states

    def at(self, anchor: str, *, first_row: bool = False):
        """The style in force where `anchor` starts, or None when it is not drawn.

        `first_row` searches only the heading row, the first inside the panel's
        outline, for an anchor whose word also appears elsewhere in the sidebar.
        """
        for text, states in self.lines[1:2] if first_row else self.lines:
            position = text.find(anchor)
            if position >= 0:
                return states[position]
        return None

    def plain(self) -> str:
        return "\n".join(text for text, _states in self.lines)


def reduced_terminfo(directory: Path, terms: tuple[str, ...], colors: int, pairs: int):
    """Compile `terms` with a smaller palette; None when ncurses tools are absent."""
    if not (shutil.which("infocmp") and shutil.which("tic")):
        return None
    target = directory / ("terminfo-" + "-".join((*terms, str(colors))))
    target.mkdir()
    for term in terms:
        source = directory / f"{term}-{colors}.src"
        described = subprocess.run(
            ["infocmp", "-x", term], capture_output=True, text=True, timeout=10
        )
        if described.returncode:
            raise RuntimeError(f"infocmp {term} failed: {described.stderr.strip()}")
        body = re.sub(r"\bcolors#\d+", f"colors#{colors}", described.stdout)
        body = re.sub(r"\bpairs#\d+", f"pairs#{pairs}", body)
        if f"colors#{colors}" not in body or f"pairs#{pairs}" not in body:
            raise RuntimeError(f"terminfo entry {term} has no colors/pairs numbers to reduce")
        source.write_text(body)
        compiled = subprocess.run(
            ["tic", "-x", "-o", str(target), str(source)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if compiled.returncode:
            raise RuntimeError(f"tic failed for {term}: {compiled.stderr.strip()}")
    return target


def missing_tools(required: bool, what: str) -> bool:
    """Report absent ncurses tools once, or fail when the caller demands coverage."""
    message = (
        f"SKIP: {what} needs infocmp and tic from ncurses; "
        "install ncurses-bin (Debian) or run with --required to fail instead"
    )
    if required:
        raise AssertionError(message.replace("SKIP", "FAIL", 1))
    print(message, flush=True)
    return True


def screen(viewer: Tmux) -> Screen:
    return Screen(viewer.run("capture-pane", "-e", "-p", "-t", "%0"))


def styles(viewer: Tmux) -> dict[str, tuple[int, int, tuple[str, ...]]]:
    """Map each semantic role to the style actually in force where it is drawn."""
    drawn = screen(viewer)
    found = {}
    for role, anchor in ANCHORS.items():
        state = drawn.at(anchor, first_row=role in FIRST_ROW_ROLES)
        if state is not None:
            found[role] = state
    return found


def pair_colors(style) -> tuple[int, int]:
    return style[0], style[1]


def launch(resources: FixtureResources, library: Path, *, terminal_env=None, arguments=None):
    directory = resources.root
    client = resources.client(
        [
            "--data-dir",
            str(library),
            "--source-socket",
            str(directory / "absent.sock"),
            *(arguments or []),
        ],
        terminal_env={
            "HOME": str(directory),
            "XDG_CONFIG_HOME": str(directory / "config"),
            **(terminal_env or {}),
        },
    )
    wait(client, lambda: client.manifest(library), "theme viewer did not launch")
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    wait(
        client,
        lambda: set(styles(viewer)) == set(ANCHORS),
        "sidebar did not draw every themed role",
    )
    return client, viewer


def assert_styles(viewer: Tmux, expected, context: str) -> None:
    actual = styles(viewer)
    for role, style in expected.items():
        assert actual.get(role) == style, (
            f"{context}: {role} drew {actual.get(role)!r}, expected {style!r}"
        )


def assert_colorless_cues(viewer: Tmux, context: str) -> None:
    """Selection must stay identifiable with every color sequence removed."""
    marked = [line for line in screen(viewer).plain().splitlines() if ANCHORS["active"] in line]
    assert len(marked) == 1, f"{context}: expected exactly one marked selection, got {marked}"


def assert_within_palette(viewer: Tmux, limit: int, context: str) -> None:
    """No role may request a color the pane's terminal cannot render."""
    for role, style in styles(viewer).items():
        for index in pair_colors(style):
            assert index < limit, (
                f"{context}: {role} requested color {index} on a {limit}-color terminal"
            )


def assert_roles_distinct(viewer: Tmux, context: str) -> None:
    """Reduced palettes must not collapse the roles onto one another."""
    drawn = styles(viewer)
    foreground, background = pair_colors(drawn["active"])
    assert foreground != background, (
        f"{context}: the selected label is unreadable, both sides are color {foreground}"
    )
    assert pair_colors(drawn["muted"]) != pair_colors(drawn["accent"]), (
        f"{context}: muted and accent collapsed onto {pair_colors(drawn['muted'])}"
    )


def default_appearance(directory: Path) -> None:
    """An unconfigured viewer keeps the shipped colors on a 256-color terminal."""
    with FixtureResources(parent=directory) as resources:
        library = resources.library("shipped")
        client, viewer = launch(resources, library)
        assert_styles(viewer, SHIPPED_256, "unconfigured 256-color viewer")
        assert_colorless_cues(viewer, "unconfigured 256-color viewer")
        assert_roles_distinct(viewer, "unconfigured 256-color viewer")
        # Redrawing after a resize must not lose or re-resolve the palette.
        client.resize(100, 30)
        wait(client, lambda: set(styles(viewer)) == set(ANCHORS), "roles lost after resize")
        assert_styles(viewer, SHIPPED_256, "resized 256-color viewer")
        client.resize(160, 38)
        wait(client, lambda: set(styles(viewer)) == set(ANCHORS), "roles lost after resize back")
        assert_styles(viewer, SHIPPED_256, "restored 256-color viewer")
    print(
        "PASS: an unconfigured viewer draws the shipped 256-color roles "
        "(exact RGB roles in slots 16-20) and marks its selection without color",
        flush=True,
    )


CUSTOM = """[active]
foreground = "yellow"
background = ["27", "blue"]
attributes = ["bold"]

[accent]
foreground = ["bright-magenta", "magenta"]

[muted]
foreground = 244
"""

# Roles the file leaves alone keep the panel as their ground, slot 17.
CUSTOM_256 = {
    "title": (16, 17, ("bold",)),
    "active": (3, 27, ("bold",)),
    "accent": (13, 17, ("bold",)),
    "muted": (244, 17, ()),
}


def config_path(resources: FixtureResources) -> Path:
    return resources.root / "config" / "tmux-workspaces" / "theme.toml"


def write_config(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def status(viewer: Tmux) -> str:
    """The message row: above the four-row footer, inside the outline."""
    return screen(viewer).plain().splitlines()[-6].strip().strip("│").strip()


def configured_colors(directory: Path) -> None:
    """A valid file is honored; a broken one keeps the shipped colors and says so."""
    with FixtureResources(parent=directory) as resources:
        write_config(config_path(resources), CUSTOM)
        library = resources.library("configured")
        _client, viewer = launch(resources, library)
        assert_styles(viewer, CUSTOM_256, "configured viewer")
        assert_colorless_cues(viewer, "configured viewer")

    with FixtureResources(parent=directory) as resources:
        write_config(config_path(resources), "[active]\nforeground = '#ff000'\n")
        library = resources.library("broken")
        client, viewer = launch(resources, library)
        assert_styles(viewer, SHIPPED_256, "viewer with an invalid theme")
        wait(
            client,
            lambda: status(viewer).startswith("Error:"),
            "invalid theme was not reported in the status line",
        )
        # Reporting the problem must not stop the workspace layer working.
        assert "Tab" in screen(viewer).plain()
    print(
        "PASS: a valid theme file is honored, and an invalid one keeps the shipped colors, "
        "reports the theme in the status line and still opens the workspace",
        flush=True,
    )


def normal_background(directory: Path) -> None:
    """Configured normal colors cover blank cells and restore after a preview."""
    with FixtureResources(parent=directory) as resources:
        config = write_config(
            config_path(resources), '[normal]\nforeground = "white"\nbackground = "blue"\n'
        )
        original = config.read_text()
        client, viewer = launch(resources, resources.library("normal-background"))

        def base_is_configured():
            drawn = Screen(viewer.run("capture-pane", "-e", "-N", "-p", "-t", "%0"))
            # A blank interior row: nothing between the outline's two cells.
            blanks = [
                states[1:-1]
                for text, states in drawn.lines
                if len(states) > 2 and not text.strip().strip("│").strip()
            ]
            return (
                drawn.at("Workspace") == (7, 4, ("bold",))
                and bool(blanks)
                and all(state[1] == 4 for row in blanks for state in row)
            )

        wait(client, base_is_configured, "configured normal background missing from blank cells")
        assert config.read_text() == original
    print("PASS: configured normal background covers blank cells", flush=True)


def unreadable_targets(directory: Path) -> None:
    """A theme path that cannot be read must not block or crash startup."""
    for name, prepare in (
        ("fifo", lambda path: os.mkfifo(path)),
        ("directory", lambda path: path.mkdir()),
    ):
        with FixtureResources(parent=directory) as resources:
            config = config_path(resources)
            config.parent.mkdir(parents=True, exist_ok=True)
            prepare(config)
            library = resources.library(name)
            _client, viewer = launch(resources, library)
            assert_styles(viewer, SHIPPED_256, f"viewer with a {name} theme path")
    print(
        "PASS: a theme path that is a FIFO or a directory leaves the viewer running on the "
        "shipped colors instead of blocking or failing",
        flush=True,
    )


PANE_TERM = "tmux-256color"
OUTER_TERM = "xterm-256color"


def reduced_palette(directory: Path, colors: int, *, required: bool) -> None:
    """Both the terminal and its pane are reduced: the ordinary small-palette host."""
    with FixtureResources(parent=directory) as resources:
        target = reduced_terminfo(resources.root, (PANE_TERM, OUTER_TERM), colors, 64)
        if target is None:
            missing_tools(required, f"{colors}-color coverage")
            return
        library = resources.library(f"reduced-{colors}")
        context = f"{colors}-color terminal and pane"
        # A trailing empty entry keeps the system terminfo for every other name.
        _client, viewer = launch(resources, library, terminal_env={"TERMINFO_DIRS": f"{target}:"})
        # Exact shipped values are pinned only where the palette forces one
        # answer; 16 colors leaves a real choice, so assert the properties the
        # acceptance criteria actually require instead of freezing a preference.
        if colors == 8:
            assert_styles(viewer, SHIPPED_BASIC, context)
        assert_within_palette(viewer, colors, context)
        assert_roles_distinct(viewer, context)
        assert_colorless_cues(viewer, context)
    print(
        f"PASS: a real {colors}-color terminal and pane stay inside that palette "
        "with distinct, marked roles",
        flush=True,
    )


def reduced_terminal_only(directory: Path, *, required: bool) -> None:
    """The user's terminal is 8-color while the pane is not: choose, do not approximate.

    tmux would otherwise pick the nearest color for us, and its approximation is
    not even reliable: with COLORTERM set it forwards 256-color codes to an
    eight-color terminal unchanged.
    """
    with FixtureResources(parent=directory) as resources:
        target = reduced_terminfo(resources.root, (OUTER_TERM,), 8, 64)
        if target is None:
            missing_tools(required, "reduced-terminal coverage")
            return
        library = resources.library("outer-8")
        context = "8-color terminal with a 256-color pane"
        client, viewer = launch(
            resources, library, terminal_env={"TERMINFO_DIRS": f"{target}:", "COLORTERM": ""}
        )
        assert_styles(viewer, SHIPPED_BASIC, context)
        assert_within_palette(viewer, 8, context)
        assert_colorless_cues(viewer, context)
        client.pump(0.5)
        received = set(SGR.findall(client.output.decode(errors="replace")))
        leaked = sorted(code for code in received if code.startswith(("38;5;", "48;5;")))
        assert not leaked, f"{context}: 256-color codes reached the terminal: {leaked}"
    print(
        "PASS: an 8-color terminal gets the deliberate basic colors, and no 256-color "
        "code reaches it, even though its pane could carry them",
        flush=True,
    )


def reduced_pane_only(directory: Path, *, required: bool) -> None:
    """The pane is smaller than the terminal. Colors must degrade, never block work.

    tmux only uses its 256-color entry when that terminfo exists on the host, so a
    full-color terminal on a host without ncurses-term lands exactly here.
    """
    with FixtureResources(parent=directory) as resources:
        target = reduced_terminfo(resources.root, (PANE_TERM,), 8, 64)
        if target is None:
            missing_tools(required, "reduced-pane coverage")
            return
        library = resources.library("pane-8")
        context = "256-color terminal with an 8-color pane"
        _client, viewer = launch(resources, library, terminal_env={"TERMINFO_DIRS": f"{target}:"})
        assert_within_palette(viewer, 8, context)
        assert_roles_distinct(viewer, context)
        assert_colorless_cues(viewer, context)
    print(
        "PASS: a pane with a smaller palette than its terminal still starts and stays "
        "inside the palette it actually has",
        flush=True,
    )


if __name__ == "__main__":
    require = "--required" in sys.argv[1:]
    with tempfile.TemporaryDirectory(prefix="tw-themes-smoke-", dir="/tmp") as directory:
        default_appearance(Path(directory))
        for palette in (8, 16):
            reduced_palette(Path(directory), palette, required=require)
        reduced_terminal_only(Path(directory), required=require)
        reduced_pane_only(Path(directory), required=require)
        configured_colors(Path(directory))
        normal_background(Path(directory))
        unreadable_targets(Path(directory))
