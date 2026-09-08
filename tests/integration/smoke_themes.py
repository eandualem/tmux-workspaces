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

from tests.integration.support import FixtureResources, click_button, saved, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux

SGR = re.compile(r"\x1b\[([0-9;]*)m")
CSI = re.compile(r"\x1b\[[0-9;:?]*[A-Za-z]")
DEFAULT = -1

# Roles are located by text the sidebar always draws, so the assertions survive
# row reordering and width changes.
ANCHORS = {
    "title": "Workspace",
    "muted": "─────",
    "active": "▶",
    "accent": "Shell",
}
_ATTRIBUTES = {1: "bold", 2: "dim", 4: "underline", 7: "reverse"}
_CLEAR = {22: ("bold", "dim"), 24: ("underline",), 27: ("reverse",)}

# The appearance shipped before any theme configuration existed: pair 1 is the
# terminal default, 2 active, 3 accent and 4 muted.
SHIPPED_256 = {
    "title": (DEFAULT, DEFAULT, ("bold",)),
    "muted": (245, DEFAULT, ()),
    "active": (231, 238, ()),
    "accent": (108, DEFAULT, ()),
}
SHIPPED_BASIC = {
    "title": (DEFAULT, DEFAULT, ("bold",)),
    "muted": (7, DEFAULT, ()),
    "active": (7, 4, ()),
    "accent": (6, DEFAULT, ()),
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

    def at(self, anchor: str):
        """The style in force where `anchor` starts, or None when it is not drawn."""
        for text, states in self.lines:
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
        state = drawn.at(anchor)
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
        "(muted 245, accent 108, active 231 on 238) and marks its selection without color",
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

CUSTOM_256 = {
    "title": (DEFAULT, DEFAULT, ("bold",)),
    "active": (3, 27, ("bold",)),
    "accent": (13, DEFAULT, ()),
    "muted": (244, DEFAULT, ()),
}


def config_path(resources: FixtureResources) -> Path:
    return resources.root / "config" / "tmux-workspaces" / "theme.toml"


def write_config(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def status(viewer: Tmux) -> str:
    return screen(viewer).plain().splitlines()[-1].strip()


def configured_colors(directory: Path) -> None:
    """A valid file is honored; a broken one keeps the shipped colors and says so."""
    with FixtureResources(parent=directory) as resources:
        write_config(config_path(resources), CUSTOM)
        library = resources.library("configured")
        _client, viewer = launch(resources, library)
        assert_styles(viewer, CUSTOM_256, "configured viewer")
        assert_colorless_cues(viewer, "configured viewer")

    with FixtureResources(parent=directory) as resources:
        write_config(config_path(resources), "[active]\nforeground = '#ff0000'\n")
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
            blanks = [states for text, states in drawn.lines if states and not text.strip()]
            return (
                drawn.at("Workspace") == (7, 4, ("bold",))
                and bool(blanks)
                and all(state[1] == 4 for row in blanks for state in row)
            )

        wait(client, base_is_configured, "configured normal background missing from blank cells")
        # Exercise the shared keyboard menu entry after integrating menu navigation.
        client.type("\x07M")
        wait(client, lambda: "Workspace options" in screen(viewer).plain(), "workspace menu absent")
        client.type("\x1b[F")
        client.type("\r")
        wait(
            client,
            lambda: "Viewer colors" in screen(viewer).plain(),
            "keyboard Colors entry failed",
        )
        client.type("d")
        client.type("\x1b")
        wait(client, base_is_configured, "Cancel did not restore configured blank-cell background")
        assert config.read_text() == original, "preview changed saved normal colors"
    print(
        "PASS: normal background covers blank cells and keyboard preview/Cancel restores it",
        flush=True,
    )


def defaults_and_apply(directory: Path) -> None:
    """Restore-defaults previews the shipped colors; only Apply writes the file."""
    with FixtureResources(parent=directory) as resources:
        config = write_config(config_path(resources), CUSTOM)
        library = resources.library("defaults")
        client, viewer = launch(resources, library)
        assert_styles(viewer, CUSTOM_256, "configured viewer")

        open_editor(client, viewer)
        client.type("d")
        wait(
            client,
            lambda: styles(viewer).get("active") == SHIPPED_256["active"],
            "restore defaults did not preview the shipped colors",
        )
        assert config.read_text() == CUSTOM, "previewing defaults must not write the file"
        client.type("\x1b")
        wait(
            client,
            lambda: styles(viewer) == CUSTOM_256,
            "cancelling restore-defaults did not bring the configured colors back",
        )

        # Apply is the only route that writes, and it changes the viewer in place.
        open_editor(client, viewer)
        client.type("da")
        wait(
            client,
            lambda: config.read_text() != CUSTOM,
            "Apply did not write the theme file",
        )
        wait(
            client,
            lambda: styles(viewer) == SHIPPED_256,
            "Apply did not leave the shipped colors installed",
        )
        assert "[active]" in config.read_text(), config.read_text()

        # The saved file is what a newly opened viewer reads.
        second_library = resources.library("reopened")
        _second, reopened = launch(resources, second_library)
        assert_styles(reopened, SHIPPED_256, "viewer reopened on the saved theme")
    print(
        "PASS: restore-defaults previews without writing, Apply writes the file and restyles "
        "the running viewer, and a new viewer reads what was saved",
        flush=True,
    )


def in_editor(viewer: Tmux) -> bool:
    return "Viewer colors" in screen(viewer).plain()


def refused_save(directory: Path, name: str, prepare, verify, cleanup=None) -> None:
    """Edit a color, save into a target that must be refused, then check the damage."""
    with FixtureResources(parent=directory) as resources:
        config = config_path(resources)
        config.parent.mkdir(parents=True, exist_ok=True)
        try:
            guarded = prepare(config)
            library = resources.library(name)
            client, viewer = launch(resources, library)
            open_editor(client, viewer)
            click_button(client, viewer, "Selected fg")
            client.type("magenta\n")
            wait(
                client,
                lambda: pair_colors(styles(viewer).get("active", SHIPPED_256["active"]))[0] == 5,
                f"{name}: the edit was not previewed",
            )
            previewed = styles(viewer)
            client.type("a")
            client.pump(0.6)
            assert in_editor(viewer), f"{name}: a refused save closed the editor"
            assert styles(viewer) == previewed, f"{name}: a refused save changed the colors"
            verify(config, guarded)
        finally:
            if cleanup:
                cleanup(config)


def unsafe_targets(directory: Path) -> None:
    """A theme file the viewer must not write through or replace."""

    def symlink(config: Path) -> str:
        real = config.parent / "real.toml"
        real.write_text(CUSTOM)
        config.symlink_to(real)
        return real.read_text()

    def check_symlink(config: Path, before: str) -> None:
        assert config.is_symlink(), "the viewer replaced a symbolic link"
        real = config.parent / "real.toml"
        assert real.read_text() == before, "the viewer wrote through a symbolic link"

    refused_save(directory, "symlinked", symlink, check_symlink)
    if os.geteuid() == 0:
        print(
            "PASS: symlinked theme is preserved; SKIP: mode-based permission checks as root",
            flush=True,
        )
        return

    def read_only(config: Path) -> str:
        config.write_text(CUSTOM)
        config.chmod(0o400)
        return config.read_text()

    def check_read_only(config: Path, before: str) -> None:
        assert config.read_text() == before, "the viewer wrote a read-only theme"

    refused_save(directory, "read-only", read_only, check_read_only)

    def locked_directory(config: Path) -> str:
        # A writable file inside a directory nobody may write: the replacement
        # and the lock beside it both need a directory entry.
        config.write_text(CUSTOM)
        config.parent.chmod(0o500)
        return config.read_text()

    def check_locked_directory(config: Path, before: str) -> None:
        assert config.read_text() == before, "the viewer wrote into a read-only directory"
        assert not list(config.parent.glob(".theme-*")), "a temporary theme file was left behind"

    refused_save(
        directory,
        "locked-directory",
        locked_directory,
        check_locked_directory,
        cleanup=lambda config: config.parent.chmod(0o700),
    )

    print(
        "PASS: a symlinked file, a read-only file and a read-only directory are all left "
        "untouched, the edit stays on screen and the editor stays open to retry",
        flush=True,
    )


def concurrent_edits(directory: Path) -> None:
    """Another writer must not cost the user the colors they are working on."""
    for name, existing in (("conflict", CUSTOM), ("first-save", None)):
        with FixtureResources(parent=directory) as resources:
            config = config_path(resources)
            config.parent.mkdir(parents=True, exist_ok=True)
            if existing is not None:
                config.write_text(existing)
            library = resources.library(name)
            client, viewer = launch(resources, library)
            open_editor(client, viewer)
            click_button(client, viewer, "Selected fg")
            client.type("magenta\n")
            wait(
                client,
                lambda view=viewer: (
                    pair_colors(styles(view).get("active", SHIPPED_256["active"]))[0] == 5
                ),
                f"{name}: the edit was not previewed",
            )
            previewed = styles(viewer)
            # A second writer lands between the read and the save.
            other = '[muted]\nforeground = "green"\n'
            config.write_text(other)
            client.type("a")
            client.pump(0.6)
            assert config.read_text() == other, f"{name}: the other writer's file was overwritten"
            assert styles(viewer) == previewed, f"{name}: the refused save changed the colors"
            assert in_editor(viewer), f"{name}: the refused save closed the editor"
    print(
        "PASS: a concurrent write is refused for both an existing and a newly created theme "
        "file, keeping the other writer's file and the user's unsaved colors",
        flush=True,
    )


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


def unreadable_file_is_never_replaced(directory: Path) -> None:
    """Contents the viewer never saw must survive, even once they become writable."""
    if os.geteuid() == 0:
        print("SKIP: replacing an unreadable theme file needs a non-root user", flush=True)
        return
    with FixtureResources(parent=directory) as resources:
        config = config_path(resources)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(CUSTOM)
        config.chmod(0o200)
        library = resources.library("unreadable-file")
        client, viewer = launch(resources, library)
        assert_styles(viewer, SHIPPED_256, "viewer with an unreadable theme")

        open_editor(client, viewer)
        click_button(client, viewer, "Selected fg")
        client.type("magenta\n")
        wait(
            client,
            lambda: pair_colors(styles(viewer).get("active", SHIPPED_256["active"]))[0] == 5,
            "the edit was not previewed",
        )
        previewed = styles(viewer)
        # The permissions are repaired only after the editor opened. The viewer
        # still has not seen what is in the file, so saving would destroy colors
        # nobody read; the digest it would compare against was never taken.
        config.chmod(0o600)
        client.type("a")
        client.pump(0.6)
        assert config.read_text() == CUSTOM, "the viewer replaced a file it could not read"
        assert in_editor(viewer), "the refused save closed the editor"
        assert styles(viewer) == previewed, "the refused save changed the colors"
    print(
        "PASS: a theme file the viewer could not read is never replaced, even after it "
        "becomes writable, and the unsaved edit stays on screen",
        flush=True,
    )


def isolation_and_idle(directory: Path) -> None:
    """Editing colors must not reach the shells, and idling must not re-read the file."""
    with FixtureResources(parent=directory) as resources:
        config = config_path(resources)
        library = resources.library("isolated")
        client, viewer = launch(resources, library)
        shells = Tmux(socket_path(library, "terminals"))
        target = "=" + Shells.name(saved(library).pane) + ":"
        wait(
            client,
            lambda: shells.run("display-message", "-p", "-t", target, "#{pane_pid}") != "",
            "the tab's ordinary shell did not start",
        )
        pid = shells.run("display-message", "-p", "-t", target, "#{pane_pid}")
        before = styles(viewer)

        # An external edit under a running viewer must not drift it. The poll
        # runs every 0.6s, so wait out several cycles before concluding.
        write_config(config, CUSTOM)
        client.pump(2.5)
        assert styles(viewer) == before, "the running viewer re-read the theme file while idle"

        # Opening the editor does show the file as it now stands, which restyles
        # the viewer even though the user changed nothing. Pin that, so it stays
        # a deliberate choice rather than drifting into a surprise.
        open_editor(client, viewer)
        wait(
            client,
            lambda: pair_colors(styles(viewer)["active"]) == pair_colors(CUSTOM_256["active"]),
            "opening the editor did not show the theme file as it now stands",
        )
        opened = styles(viewer)["active"]

        token = "ZZTHEMEZZ"
        click_button(client, viewer, "Selected fg")
        client.type(token)
        client.pump(0.3)
        client.type("\n")
        client.pump(0.4)
        # The value is rejected, so the colors stay as the editor opened them.
        # The open field adds its own underline, so compare the colors only.
        assert pair_colors(styles(viewer)["active"]) == pair_colors(opened), (
            f"an invalid value was previewed: {styles(viewer)!r}"
        )
        # Escape closes the rejected field; a second Escape leaves the editor.
        client.type("\x1b")
        client.pump(0.3)
        client.type("\x1b")
        wait(client, lambda: not in_editor(viewer), "Escape did not leave the colors editor")
        assert config.read_text() == CUSTOM, "leaving the editor wrote the theme file"

        text = shells.run("capture-pane", "-S", "-", "-p", "-t", target)
        assert token not in text, "editor typing reached an ordinary shell"
        assert "magenta" not in text and "Colors" not in text, (
            "editor input or labels reached an ordinary shell"
        )
        assert pid == shells.run("display-message", "-p", "-t", target, "#{pane_pid}"), (
            "editing colors restarted the tab's shell"
        )
        assert styles(viewer) == before, (
            "cancelling did not restore the colors the viewer had before the editor opened"
        )
    print(
        "PASS: an external edit does not drift a running viewer while idle, opening the "
        "editor shows the file and cancelling restores what was on screen, rejected input "
        "is not previewed, and nothing typed in the editor reaches the ordinary shells",
        flush=True,
    )


def open_editor(client, viewer: Tmux):
    click_button(client, viewer, "Colors…")
    wait(client, lambda: in_editor(viewer), "colors editor did not open")


def editing_preview_and_cancel(directory: Path) -> None:
    """Preview shows at once, and every exit route restores what it opened with."""
    with FixtureResources(parent=directory) as resources:
        config = config_path(resources)
        library = resources.library("editing")
        client, viewer = launch(resources, library)
        before = styles(viewer)
        assert not config.exists(), "opening the viewer must not create a theme file"

        # The mouse route: click the field, replace its value, accept it.
        open_editor(client, viewer)
        click_button(client, viewer, "Selected fg")
        client.type("yellow\n")
        wait(
            client,
            lambda: pair_colors(styles(viewer).get("active", SHIPPED_256["active"]))[0] == 3,
            "previewing a color did not change the running viewer",
        )
        assert not config.exists(), "preview must not write the theme file"

        client.type("\x1b")
        wait(client, lambda: styles(viewer) == before, "cancel did not restore the opened colors")
        assert not config.exists(), "cancel must not write the theme file"

        # The keyboard route reaches the same field and previews the same way.
        open_editor(client, viewer)
        client.type("\x1b[B\x1b[B\x1b[B\nmagenta\n")
        wait(
            client,
            lambda: pair_colors(styles(viewer).get("active", SHIPPED_256["active"]))[0] == 5,
            "the keyboard route did not preview a color",
        )
        # Escape closes the value field first, then leaves the editor.
        client.type("\x1b\x1b")
        wait(client, lambda: styles(viewer) == before, "Escape did not restore the opened colors")

        assert not config.exists(), "no route through the editor may write without Apply"
    print(
        "PASS: mouse and keyboard edits preview immediately without touching the file, "
        "and cancel restores the colors the editor opened with",
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
        editing_preview_and_cancel(Path(directory))
        defaults_and_apply(Path(directory))
        unsafe_targets(Path(directory))
        concurrent_edits(Path(directory))
        unreadable_targets(Path(directory))
        unreadable_file_is_never_replaced(Path(directory))
        isolation_and_idle(Path(directory))
