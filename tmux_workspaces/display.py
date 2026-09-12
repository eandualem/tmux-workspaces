"""Render split arrangements into a private tmux display server."""

import contextlib
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass

from . import padded_layout
from .entrypoints import script_command
from .keymap import DEFAULT_KEYMAP, Keymap, direct_sequence
from .model import is_empty, leaves, minimum_size
from .shells import Shells
from .tmux import Tmux


@dataclass(frozen=True)
class PaneState:
    active: int
    last: int
    dead: int
    left: int
    top: int
    width: int
    height: int
    gutter: int = 0


# A gutter is a one-cell pane that holds nothing: the blank column that pads a
# content pane on each side, or the separator between two split panes. A blank
# one runs a process that never writes; a horizontal separator draws one thin
# rule. Neither is ever selected for long.
GUTTER_COMMAND = "tail -f /dev/null"


def gutter_allowance(tree: dict | None) -> tuple[int, int]:
    """Columns and rows the gutters of a tree add to its minimum size.

    Two edge gutters and their hidden borders cost four columns; each split
    adds a band gutter and one more border across its axis.
    """
    if not tree:
        return 0, 0

    def inner(node: dict) -> tuple[int, int]:
        """Columns and rows a subtree's own gutters add, along each axis."""
        if "agent" in node:
            return 0, 0
        first, second = inner(node["first"]), inner(node["second"])
        if node["direction"] == "right":
            return first[0] + second[0] + 2, max(first[1], second[1])
        return max(first[0], second[0]), first[1] + second[1] + 2

    cols, rows = inner(tree)
    return 4 + cols, rows


@dataclass(frozen=True)
class DisplayState:
    size: tuple[int, int]
    panes: dict[str, PaneState]


class Display:
    def __init__(
        self,
        viewer_socket: str,
        source_socket: str,
        sidebar: str,
        shell_socket: str,
        action_socket: str,
        host_socket: str | None = None,
        host_pane: str | None = None,
        keymap: Keymap | None = None,
        roster_args: tuple[str, ...] = (),
    ):
        if len({os.path.realpath(p) for p in (viewer_socket, source_socket, shell_socket)}) != 3:
            raise ValueError("Viewer, terminal and source sockets must differ")
        self.tmux = Tmux(viewer_socket)
        self.source_socket = source_socket
        self.shells = Shells(shell_socket)
        self.action_socket = action_socket
        self.host_socket, self.host_pane = host_socket, host_pane
        self.sidebar = sidebar
        self.keymap = keymap or DEFAULT_KEYMAP
        # How a chooser pane builds the sidebar's session roster for itself.
        self.roster_args = tuple(roster_args)
        # Two grounds, both painted by tmux so RGB values work. The panel is
        # the sidebar's; the surface is the terminals'. With a surface of its
        # own, every content pane is padded by a blank gutter column on each
        # side and tmux's border glyphs are painted in the surface so they
        # vanish; the separators between split panes are then thin gutters in
        # the outline color. With the terminal's own surface (``default``)
        # there are no gutters, since a border on an unknown ground cannot be
        # hidden, and the borders form a band of the panel color instead.
        self.panel_color = "default"
        self.surface = "default"
        self.separator = "default"
        self._empty_panes: set[str] = set()
        self._setup_done = False
        self._band_gutters: dict[str, bool] = {}
        self._blank_gutters: set[str] = set()
        self._surface_panes: set[str] = set()
        self._tab_id = ""
        self.panes: dict[str, str] = {}
        self.last_size = (0, 0)
        self.small = False
        self._shell_names: dict[str, str] = {}
        self._external_targets: dict[str, tuple[str, str]] = {}
        self._attachment_windows: dict[tuple[str, str, str], str] = {}
        self._rendered_key = None
        self._rendered_shape = None
        self._rendered_geometry = None
        self._snapshot_enabled = False
        self._snapshot: DisplayState | None = None
        self._split_drag = None

    def setup(self) -> None:
        for name, value in {
            "mouse": "on",
            "status": "off",
            "prefix": self.keymap.prefix,
            "prefix2": "None",
            "escape-time": "10",
            # tmux's timing-based paste guess can bypass bindings for CSI
            # actions after rapid text. Explicit bracketed paste still works.
            "assume-paste-time": "0",
            "focus-events": "on",
            "set-clipboard": "on",
            # Keep the session/window alive until all detach/control clients
            # finish. exit-unattached then retires this entire private server.
            "destroy-unattached": "off",
            "exit-unattached": "on",
            "@viewer_padded": "1" if self.padded else "0",
        }.items():
            self.tmux.run("set-option", "-g", name, value)
        for name, value in {
            "pane-border-status": "off",
            "pane-border-style": self._band_style(),
            "pane-active-border-style": self._band_style(),
            "automatic-rename": "off",
            "allow-rename": "off",
            "window-size": "latest",
            "remain-on-exit": "on",
            "pane-border-format": "",
        }.items():
            self.tmux.run("set-window-option", "-g", name, value)
        self.tmux.run("set-option", "-p", "-t", self.sidebar, "@viewer_agent", "Workspaces")
        self.tmux.batch(self._ground_commands(self.sidebar, "surface" if self.padded else "panel"))
        # A click lands focus on a gutter as on any pane; send it straight
        # back to the pane that had it, so typing never goes nowhere.
        self.tmux.run(
            "set-hook",
            "-g",
            "after-select-pane",
            'if-shell -F "#{@viewer_gutter}" "select-pane -l" "copy-mode -q -t \'{last}\'"',
        )
        # Forward wheel events to nested tmux, whose copy-mode owns agent scrollback.
        for key in ("WheelUpPane", "WheelDownPane"):
            self.tmux.run("bind-key", "-n", key, "send-keys", "-M")
        # Hold this client's command queue until sidebar clicks have applied
        # their action and focus. Raw forwarding races text from the same read
        # into the sidebar before it has handled navigation.
        native_click = "select-pane -t = ; send-keys -M"
        native_double = "select-pane -t = ; copy-mode -H ; send-keys -X select-word"

        def resize_command(phase):
            # tmux exposes coordinates relative to the mouse pane, and none
            # on its border cells. Those hidden borders remain inert padding;
            # only the visible rule starts a bounded split resize.
            action = (
                "resize:end:-1:-1"
                if phase == "cancel"
                else f"resize:{phase}:"
                "#{?#{!=:#{mouse_x},},#{e|+:#{mouse_x},#{pane_left}},-1}:"
                "#{?#{!=:#{mouse_y},},#{e|+:#{mouse_y},#{pane_top}},-1}"
            )
            return "run-shell " + shlex.quote(
                script_command(
                    "_action",
                    "--action-socket",
                    self.action_socket,
                    "--action",
                    action,
                    "--wait-action",
                )
            )

        drag_active = "#{==:#{@viewer_resizing},1}"
        native_click = (
            "if-shell -F "
            + shlex.quote(drag_active)
            + " "
            + shlex.quote(resize_command("cancel"))
            + " ; "
            + native_click
        )
        drag_target = f"#{{||:{drag_active},#{{==:#{{@viewer_gutter}},1}}}}"
        # Selection belongs to the viewer, including when a nested application
        # requests mouse events. Its frozen pane buffer cannot include a sibling
        # pane, and background output cannot erase the user's highlight.
        self.tmux.run(
            "bind-key",
            "-n",
            "MouseDrag1Pane",
            "if-shell",
            "-F",
            drag_target,
            resize_command("move"),
            f"if-shell -F '#{{==:#{{mouse_pane}},{self.sidebar}}}' 'send-keys -M' "
            "'select-pane -t = ; copy-mode -M'",
        )
        for key, phase, native in (
            ("MouseDown1Border", "start", "select-pane -M"),
            ("MouseDrag1Border", "move", "resize-pane -M"),
        ):
            self.tmux.run(
                "bind-key",
                "-n",
                key,
                "if-shell",
                "-F",
                "#{==:#{@viewer_padded},1}",
                resize_command(phase),
                native,
            )
        for key in ("MouseDragEnd1Pane", "MouseDragEnd1Border", "MouseUp1Pane", "MouseUp1Border"):
            self.tmux.run(
                "bind-key",
                "-n",
                key,
                "if-shell",
                "-F",
                drag_active,
                resize_command("end"),
                "send-keys -M",
            )
        for table in ("copy-mode", "copy-mode-vi"):
            self.tmux.run(
                "bind-key",
                "-T",
                table,
                "MouseDragEnd1Pane",
                "send-keys",
                "-X",
                "stop-selection",
            )
            self.tmux.run(
                "bind-key",
                "-T",
                table,
                "MouseDown1Pane",
                "copy-mode -q ; select-pane -t = ; send-keys -M",
            )
            for key, selection in (
                ("DoubleClick1Pane", "select-word"),
                ("TripleClick1Pane", "select-line"),
            ):
                self.tmux.run("bind-key", "-T", table, key, "send-keys", "-X", selection)
            self.tmux.run("bind-key", "-T", table, "Escape", "send-keys", "-X", "cancel")
        for key, native in (
            (
                "MouseDown1Pane",
                "if-shell -F '#{==:#{@viewer_gutter},1}' "
                + shlex.quote(resize_command("start"))
                + " "
                + shlex.quote(native_click),
            ),
            # Keep the second down here: forwarding it lets nested tmux
            # schedule its default delayed double-click clipboard copy,
            # which can overwrite this viewer's explicit selection copy.
            ("SecondClick1Pane", "select-pane -t ="),
            ("TripleClick1Pane", native_double.replace("select-word", "select-line")),
        ):
            command = script_command(
                "_action",
                "--action-socket",
                self.action_socket,
                "--action",
                "mouse:left:#{mouse_x}:#{mouse_y}",
                "--wait-action",
            )
            self.tmux.run(
                "bind-key",
                "-n",
                key,
                "if-shell",
                "-F",
                f"#{{==:#{{mouse_pane}},{self.sidebar}}}",
                "select-pane -t = ; run-shell " + shlex.quote(command),
                native,
            )
        # tmux's DoubleClick is a delayed duplicate of SecondClick. The
        # sidebar already handles double clicks on the physical downs above;
        # processing this notification again can reopen a just-accepted editor.
        self.tmux.run(
            "bind-key",
            "-n",
            "DoubleClick1Pane",
            "if-shell",
            "-F",
            f"#{{!=:#{{mouse_pane}},{self.sidebar}}}",
            native_double,
        )
        # Remove native prefix commands on this private server too: an unbound
        # viewer new-tab key must not create an unmanaged tmux window instead.
        self.tmux.run("unbind-key", "-a", "-T", "prefix")
        self.tmux.run("bind-key", self.keymap.prefix, "send-prefix")
        self.tmux.run("bind-key", "Escape", "switch-client", "-T", "root")
        self._setup_done = True
        for key, action in self.keymap.prefix_items():
            self.tmux.run(
                "bind-key",
                key,
                "run-shell",
                script_command(
                    "_action",
                    "--action-socket",
                    self.action_socket,
                    "--action",
                    action,
                    "--wait-action",
                ),
            )
        for index, action in enumerate(
            action for action, keys in self.keymap.direct.items() if keys
        ):
            self.tmux.run("set-option", "-s", f"user-keys[{index}]", direct_sequence(action))
            self.tmux.run(
                "bind-key",
                "-n",
                f"User{index}",
                "run-shell",
                script_command(
                    "_action",
                    "--action-socket",
                    self.action_socket,
                    "--action",
                    action,
                    "--wait-action",
                ),
            )

    @property
    def padded(self) -> bool:
        """Whether panes are padded: only on a surface of the theme's own."""
        return self.surface != "default"

    def copy_selection(self) -> None:
        """Copy only an explicit viewer selection, leaving its highlight intact."""
        self.tmux.run(
            "if-shell",
            "-F",
            "#{selection_present}",
            "send-keys -X copy-selection-no-clear",
        )

    def _band_style(self) -> str:
        # Foreground and background alike: the line glyphs vanish into the
        # surface between padded panes, or into a band of the panel otherwise.
        ground = self.surface if self.padded else self.panel_color
        return f"fg={ground},bg={ground}"

    def _ground_commands(self, pane: str, ground: str) -> list[list[str]]:
        """Give a pane one of the grounds: panel, surface, separator or default."""
        color = {
            "panel": self.panel_color,
            "surface": self.surface,
            "separator": self.separator,
        }.get(ground, "default")
        style = "default" if color == "default" else f"bg={color}"
        return [
            ["set-option", "-p", "-t", pane, name, style]
            for name in ("window-style", "window-active-style")
        ]

    def _panel_commands(self, pane: str, panel: bool) -> list[list[str]]:
        """Give a pane the panel background, or the terminal's own."""
        return self._ground_commands(pane, "panel" if panel else "default")

    def _rule_command(self, vertical: bool) -> str:
        """A separator: one thin rule in the outline color, across or down."""
        return script_command(
            "_leaf", "--rule", *(["--vertical"] if vertical else []), "--color", self.separator
        )

    def _gutter(self, target: str, *, vertical: bool, before: bool, band: bool) -> str:
        """Split a one-cell gutter off ``target``.

        A blank gutter pads a pane in the surface color. A band separates two
        split panes with one thin rule in the outline color, drawn down a
        column or across a row, so both directions weigh the same.
        """
        command = self._rule_command(vertical=not vertical) if band else GUTTER_COMMAND
        pane = self.tmux.run(
            "split-window",
            "-d",
            "-v" if vertical else "-h",
            *(["-b"] if before else []),
            "-t",
            target,
            "-l",
            "1",
            "-P",
            "-F",
            "#{pane_id}",
            command,
        )
        self.tmux.batch(
            [
                ["set-option", "-p", "-t", pane, "@viewer_gutter", "1"],
                *self._ground_commands(pane, "surface"),
            ]
        )
        if band:
            # Remembered with its direction, so a new outline color can redraw it.
            self._band_gutters[pane] = not vertical
        else:
            self._blank_gutters.add(pane)
        return pane

    def style_panel(
        self, panel: str, surface: str = "default", separator: str | None = None
    ) -> None:
        """Paint the grounds: the sidebar's panel, the terminals' surface and
        the separators between split panes.

        Called whenever a palette installs, including after a saved theme edit,
        so every ground changes with the sidebar rather than a step
        behind it. The focused pane is not marked by its border: the owner
        asked for no highlighted border at all. Before ``setup`` the values are
        only remembered; ``setup`` applies them with the window options. A
        change that turns padding on or off takes effect at the next render.
        """
        self.panel_color, self.surface = panel, surface
        self.separator = separator or panel
        # Palette installation can change padding and the startup palette of
        # existing choosers, even when the tab and its geometry are unchanged.
        # Invalidate both the same-tab fast path and container reuse.
        self._rendered_key = self._rendered_shape = self._rendered_geometry = None
        if not self._setup_done:
            return
        style = self._band_style()
        commands = [
            ["set-option", "-g", "@viewer_padded", "1" if self.padded else "0"],
            ["set-window-option", "-g", "pane-border-style", style],
            ["set-window-option", "-g", "pane-active-border-style", style],
            # The sidebar's own cells are curses'; its ground shows only where
            # curses leaves a cell alone, such as inside a rounded corner.
            *self._ground_commands(self.sidebar, "surface" if self.padded else "panel"),
        ]
        for pane in sorted(self._empty_panes):
            commands += self._ground_commands(pane, "panel")
        for pane in sorted(self._surface_panes | self._blank_gutters | set(self._band_gutters)):
            commands += self._ground_commands(pane, "surface")
        for pane, vertical in sorted(self._band_gutters.items()):
            # The rule carries its color; a new one is drawn by a new process.
            commands.append(["respawn-pane", "-k", "-t", pane, self._rule_command(vertical)])
        self.tmux.batch(commands)

    @contextlib.contextmanager
    def snapshot_scope(self):
        """Share fresh reads within one controller event, never across events."""
        previous = self._snapshot_enabled
        self._snapshot_enabled = True
        self.invalidate_snapshot()
        try:
            yield
        finally:
            self.invalidate_snapshot()
            self._snapshot_enabled = previous

    def invalidate_snapshot(self) -> None:
        self._snapshot = None

    def state(self) -> DisplayState:
        if self._snapshot_enabled and self._snapshot is not None:
            return self._snapshot
        panes = {}
        size = None
        for line in self.tmux.run(
            "list-panes",
            "-t",
            "=viewer:",
            "-F",
            "#{pane_id} #{pane_active} #{pane_last} #{pane_dead} "
            "#{pane_left} #{pane_top} #{pane_width} #{pane_height} "
            "#{window_width} #{window_height} #{?@viewer_gutter,1,0}",
        ).splitlines():
            pane, *fields = line.split()
            values = [int(field) for field in fields]
            if len(values) != 10:
                raise ValueError("Incomplete viewer pane state")
            panes[pane] = PaneState(*values[:7], gutter=values[9])
            size = tuple(values[7:9])
        if size is None or self.sidebar not in panes:
            raise RuntimeError("Viewer navigation pane disappeared")
        snapshot = DisplayState(size, panes)
        if self._snapshot_enabled:
            self._snapshot = snapshot
        return snapshot

    def size(self) -> tuple[int, int]:
        return self.state().size

    def focused_leaf(self) -> str | None:
        panes = self.state().panes
        # A sidebar click follows the selected content pane immediately, without
        # depending on the polling interval catching the earlier terminal click.
        for flag in ("active", "last"):
            for pane, state in panes.items():
                if getattr(state, flag):
                    leaf_id = next(
                        (key for key, value in self.panes.items() if value == pane), None
                    )
                    if leaf_id:
                        return leaf_id
        return None

    def select(self, leaf_id: str) -> None:
        if leaf_id in self.panes:
            self.invalidate_snapshot()
            self.tmux.run("select-pane", "-t", self.panes[leaf_id])

    def select_sidebar(self) -> None:
        self.invalidate_snapshot()
        self.tmux.run("select-pane", "-t", self.sidebar)

    def wait_for_input(self, tab: dict | None, *, timeout: float = 3) -> None:
        """Keep shortcut input queued until this ordinary shell client attaches.

        A respawned pane exists before its nested tmux client starts. A later
        navigation can kill that wrapper with typed input still in its PTY.
        Match both session and viewer TTY: another viewer is not readiness here.
        External sessions and empty choosers have their own recovery UI.
        """
        if not tab:
            return
        deadline = time.monotonic() + timeout

        def query(tmux: Tmux, *args: str) -> str:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Terminal attachment is not ready; try selecting it again")
            return tmux.run(*args, timeout=remaining)

        def focused() -> list[str] | None:
            rows = query(
                self.tmux,
                "list-panes",
                "-t",
                self.sidebar,
                "-F",
                "#{pane_id}|#{@viewer_leaf_id}|#{@viewer_tab_id}|#{pane_active}|"
                "#{pane_dead}|#{pane_tty}|#{pane_pid}",
            )
            return next(
                (
                    parts
                    for row in rows.splitlines()
                    if len(parts := row.split("|")) == 7 and parts[3] == "1"
                ),
                None,
            )

        try:
            initial = focused()
            if initial and initial[0] == self.sidebar:
                return
            leaf = next(
                (
                    item
                    for item in leaves(tab["tree"])
                    if initial and self.panes.get(item["id"]) == initial[0]
                ),
                None,
            )
            if not leaf:
                raise RuntimeError("Terminal attachment disappeared; try selecting it again")
            if leaf["agent"] or is_empty(leaf):
                return
            if initial[1:3] != [leaf["id"], tab["id"]] or initial[4] != "0" or not initial[5]:
                raise RuntimeError("Terminal attachment changed; try selecting it again")
            expected = f"{Shells.name(leaf)}|{initial[5]}"
            while True:
                clients = query(
                    self.shells.tmux, "list-clients", "-F", "#{session_name}|#{client_tty}"
                )
                # Recheck after probing the source: pane IDs can survive respawn.
                if focused() != initial:
                    raise RuntimeError("Terminal attachment changed; try selecting it again")
                if expected in clients.splitlines():
                    return
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            self.select_sidebar()
            if isinstance(error, subprocess.TimeoutExpired):
                raise RuntimeError(
                    "Terminal attachment is not ready; try selecting it again"
                ) from error
            raise

    def _capture_attachment_windows(self) -> None:
        """Remember each owned helper's window before its wrapper is replaced."""
        from .attachments import GROUPED_MARKER, GROUPED_SOURCE_SESSION

        targets = {
            leaf: self._external_targets[leaf]
            for leaf in self.panes
            if leaf in self._external_targets
        }
        if not targets:
            return
        try:
            tty_by_pane = dict(
                row.split("|", 1)
                for row in self.tmux.run(
                    "list-panes", "-t", self.sidebar, "-F", "#{pane_id}|#{pane_tty}"
                ).splitlines()
                if "|" in row
            )
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            return
        for source in {target[0] for target in targets.values()}:
            try:
                clients = Tmux(source).run(
                    "list-clients",
                    "-F",
                    f"#{{client_tty}}|#{{window_id}}|#{{{GROUPED_MARKER}}}|"
                    f"#{{pid}}|#{{{GROUPED_SOURCE_SESSION}}}",
                    check=False,
                    timeout=0.5,
                )
            except (RuntimeError, OSError, subprocess.TimeoutExpired):
                continue
            windows = {}
            for row in clients.splitlines():
                parts = row.split("|")
                if (
                    len(parts) == 5
                    and parts[2] == "1"
                    and parts[1].startswith("@")
                    and parts[3].isdigit()
                    and parts[4].startswith("$")
                ):
                    windows[parts[0]] = f"{parts[3]}:{parts[4]}:{parts[1]}"
            for leaf, target in targets.items():
                tty = tty_by_pane.get(self.panes[leaf])
                if target[0] == source and tty in windows:
                    self._attachment_windows[(leaf, *target)] = windows[tty]

    def _leaf_command(self, pane: dict | None) -> str:
        if not pane:
            return script_command("_leaf")
        if not pane["agent"]:
            self._external_targets.pop(pane["id"], None)
        if is_empty(pane):
            return script_command(
                "_leaf",
                "--chooser",
                "--tab",
                self._tab_id,
                "--leaf",
                pane["id"],
                "--action-socket",
                self.action_socket,
                "--source-socket",
                self.source_socket,
                *self.roster_args,
            )
        if pane["agent"]:
            source_socket = pane.get("source_socket") or self.source_socket
            if os.path.realpath(source_socket) in {
                os.path.realpath(self.tmux.socket),
                os.path.realpath(self.shells.tmux.socket),
            }:
                raise ValueError("Saved attachment must use an external source socket")
            args = ["_leaf", "--source-socket", source_socket, "--agent=" + pane["agent"]]
            self._external_targets[pane["id"]] = (source_socket, pane["agent"])
            window = self._attachment_windows.get((pane["id"], source_socket, pane["agent"]))
            if window:
                args += ["--attachment-window", window]
            if self.host_socket and self.host_pane:
                args += ["--host-socket", self.host_socket, "--host-pane", self.host_pane]
            return script_command(*args)
        name = self._shell_names.get(pane["id"]) or self.shells.ensure(pane)
        return script_command(
            "_leaf", "--source-socket", self.shells.tmux.socket, "--terminal", name
        )

    def _label(self, pane: str, name: str) -> None:
        label = re.sub(r"[^\w .-]", "", name)
        self.tmux.run("set-option", "-p", "-t", pane, "@viewer_agent", label)

    def _identity_commands(self, tab: dict | None, panes: dict[str, str]) -> list[list[str]]:
        if not tab:
            return []
        focused = panes.get(tab["focus"])
        # A stale focus may be absent from the displayed fallback tree. Preserve
        # the existing safe no-op selection while still applying pane metadata.
        select = [["select-pane", "-t", focused]] if focused else []
        commands = []
        self._empty_panes, self._surface_panes = set(), set()
        for leaf in leaves(tab["tree"]):
            pane = panes.get(leaf["id"])
            if not pane:
                continue
            for name, value in {
                "@viewer_leaf_id": leaf["id"],
                "@viewer_tab_id": tab["id"],
                "@viewer_agent": re.sub(
                    r"[^\w .-]", "", leaf["agent"] or ("Empty" if is_empty(leaf) else "Terminal")
                ),
            }.items():
                commands.append(["set-option", "-p", "-t", pane, name, value])
            # On a surface of its own every content pane sits on it, chooser
            # included; on the terminal's own, an empty pane is part of the
            # panel until something runs in it. A pane is reused across
            # respawns, so the ground is set every time.
            if self.padded:
                commands += self._ground_commands(pane, "surface")
                self._surface_panes.add(pane)
            elif is_empty(leaf):
                commands += self._ground_commands(pane, "panel")
                self._empty_panes.add(pane)
            else:
                commands += self._ground_commands(pane, "default")
        return commands + select

    def _pane_identity(self, tab: dict | None) -> None:
        commands = self._identity_commands(tab, self.panes)
        if not commands:
            return
        self.invalidate_snapshot()
        self.tmux.batch(commands)

    @staticmethod
    def _shape_key(tree: dict | None):
        if not tree:
            return None
        if "agent" in tree:
            return ("leaf",)
        return (
            tree["direction"],
            tree.get("ratio", 0.5),
            Display._shape_key(tree["first"]),
            Display._shape_key(tree["second"]),
        )

    @staticmethod
    def _geometry(state: DisplayState):
        # Include the sidebar: matching content shape must not preserve an
        # externally resized navigation panel or a rearranged tmux layout.
        return tuple(
            (pane, item.left, item.top, item.width, item.height)
            for pane, item in sorted(state.panes.items())
        )

    def _reuse_containers(self, tab: dict, tree: dict, key) -> None:
        try:
            self._capture_attachment_windows()
            displayed = leaves(tree)
            containers = list(self.panes.values())
            replacement = {
                leaf["id"]: pane for leaf, pane in zip(displayed, containers, strict=True)
            }
            shape, geometry = self._rendered_shape, self._rendered_geometry
            self._tab_id = tab["id"]
            self._shell_names = self.shells.ensure_many(
                [leaf for leaf in displayed if not leaf["agent"] and not is_empty(leaf)]
            )
            # Construct and validate every target before touching displayed clients.
            commands = [["select-pane", "-t", self.sidebar]]
            commands += [
                ["respawn-pane", "-k", "-t", pane, self._leaf_command(leaf)]
                for leaf, pane in zip(displayed, containers, strict=True)
            ]
            commands += self._identity_commands(tab, replacement)
            self.invalidate_snapshot()
            self._rendered_key = self._rendered_shape = self._rendered_geometry = None
            # Restart wrappers; a living wrapper would recover to its old target.
            # Focus leaves the sidebar only after all targets/metadata succeed.
            self.tmux.batch(commands)
        except (RuntimeError, OSError, ValueError):
            self._rendered_key = self._rendered_shape = self._rendered_geometry = None
            self.panes.clear()
            with contextlib.suppress(RuntimeError, OSError, ValueError):
                self.select_sidebar()
            raise
        self.panes = replacement
        self._rendered_key, self._rendered_shape, self._rendered_geometry = key, shape, geometry

    @staticmethod
    def _layout_key(tree: dict | None):
        if not tree:
            return None
        if "agent" in tree:
            # cwd is a saved restart location; changing it does not change the
            # attachment client for an already-running shell.
            return tree["id"], tree["agent"], tree.get("source_socket"), is_empty(tree)
        return (
            tree["id"],
            tree["direction"],
            tree.get("ratio", 0.5),
            Display._layout_key(tree["first"]),
            Display._layout_key(tree["second"]),
        )

    def render(self, tab: dict | None, focus: bool) -> None:
        # Only pane IDs on this dedicated server may be destroyed or rearranged.
        state = self.state()
        owned = state.panes
        # Gutters are the display's own furniture; only content panes are
        # matched against the tab's leaves.
        everything = [pane for pane in owned if pane != self.sidebar]
        content = [pane for pane in everything if not owned[pane].gutter]
        cols, rows = state.size
        sidebar_width = min(28, max(24, cols // 4))
        tree = tab["tree"] if tab else None
        # Narrow displays use temporary focus. The saved tree is never replaced.
        needed_cols, needed_rows = minimum_size(tree)
        if self.padded:
            extra_cols, extra_rows = gutter_allowance(tree)
            needed_cols, needed_rows = needed_cols + extra_cols, needed_rows + extra_rows
        self.small = cols - sidebar_width - 1 < needed_cols or rows < needed_rows
        if tree and (focus or self.small):
            tree = next(
                (item for item in leaves(tree) if item["id"] == tab["focus"]), leaves(tree)[0]
            )
        key = (tab["id"], self._layout_key(tree)) if tab else None
        healthy = (
            bool(self.panes)
            and set(content) == set(self.panes.values())
            and all(not owned[pane].dead for pane in content)
        )
        if (
            key is not None
            and key == self._rendered_key
            and self.last_size == (cols, rows)
            and healthy
        ):
            # Name-only edits, including peer renames, do not replace terminals.
            self.select(tab["focus"])
            return
        if (
            tree
            and healthy
            and self.last_size == state.size
            and self._rendered_shape == self._shape_key(tree)
            and self._rendered_geometry == self._geometry(state)
        ):
            self._reuse_containers(tab, tree, key)
            return
        self._capture_attachment_windows()
        self.invalidate_snapshot()
        self._rendered_key = self._rendered_shape = self._rendered_geometry = None
        self.last_size = (cols, rows)
        self.panes.clear()
        self._tab_id = tab["id"] if tab else ""
        self._shell_names = self.shells.ensure_many(
            [pane for pane in leaves(tree) if not pane["agent"] and not is_empty(pane)]
        )
        first = leaves(tree)[0] if tree else None
        command = self._leaf_command(first)
        self._band_gutters, self._blank_gutters = {}, set()
        if everything:
            # Keep a content pane beside the sidebar. Removing all of them lets
            # tmux expand/reflow the sidebar across the entire terminal. Batch
            # sibling removal and width restoration so intermediate layouts
            # cannot be painted or sent to the sidebar as resize events.
            pane = content[0] if content else everything[0]
            commands = [["kill-pane", "-t", sibling] for sibling in everything if sibling != pane]
            commands += [
                ["resize-pane", "-t", self.sidebar, "-x", str(sidebar_width)],
                # The survivor may be a gutter; respawn-pane keeps pane options,
                # and a content pane still flagged as one would bounce focus.
                ["set-option", "-p", "-t", pane, "-u", "@viewer_gutter"],
                ["set-option", "-p", "-t", pane, "@viewer_leaf_id", ""],
                ["set-option", "-p", "-t", pane, "@viewer_tab_id", ""],
                ["respawn-pane", "-k", "-t", pane, command],
            ]
            self.tmux.batch(commands)
        else:
            pane = self.tmux.run(
                "split-window",
                "-d",
                "-h",
                "-t",
                self.sidebar,
                "-l",
                str(max(8, cols - sidebar_width - 1)),
                "-P",
                "-F",
                "#{pane_id}",
                command,
            )
            self.tmux.run("resize-pane", "-t", self.sidebar, "-x", str(sidebar_width))
        if self.padded:
            # The padding at both edges of the content area, split off before
            # the tree so they span its full height.
            self._gutter(pane, vertical=False, before=True, band=False)
            self._gutter(pane, vertical=False, before=False, band=False)
        if tree:
            self._tree(tree, pane)
        else:
            self._label(pane, "Create a tab")
            self.select_sidebar()
        self._pane_identity(tab)
        self._rendered_key = key
        if tree:
            self._rendered_shape = self._shape_key(tree)
            self._rendered_geometry = self._geometry(self.state())

    def _tree(self, tree: dict, pane: str) -> None:
        if "agent" in tree:
            self.panes[tree["id"]] = pane
            return
        second_leaf = leaves(tree["second"])[0]
        right = tree["direction"] == "right"
        ratio = tree.get("ratio", 0.5)
        if self.padded:
            # The separator and its two hidden borders take three cells from
            # the pair, so the split is sized in cells: the first pane gets its
            # share of what remains, and the second is everything after the
            # border, from which the separator is then cut. A ratio measured
            # back from that geometry reproduces these exact sizes, so tab
            # switches between like layouts reuse their containers instead of
            # rebuilding, and no pane drifts by a column per render.
            size = int(
                self.tmux.run(
                    "display-message",
                    "-p",
                    "-t",
                    pane,
                    "#{pane_width}" if right else "#{pane_height}",
                )
            )
            content = max(2, size - 3)
            first = min(max(round(ratio * content), 1), content - 1)
            length = str(max(1, size - 1 - first))
        else:
            length = str(round(100 * (1 - ratio))) + "%"
        sibling = self.tmux.run(
            "split-window",
            "-d",
            "-h" if right else "-v",
            "-t",
            pane,
            "-l",
            length,
            "-P",
            "-F",
            "#{pane_id}",
            self._leaf_command(second_leaf),
        )
        if self.padded:
            # Between the two: a blank cell, the separator, a blank cell; the
            # separator is a one-cell gutter and the blanks its hidden borders.
            self._gutter(sibling, vertical=tree["direction"] != "right", before=True, band=True)
        self._tree(tree["first"], pane)
        self._tree(tree["second"], sibling)

    def remember_ratios(self, tree: dict | None) -> None:
        if not tree or "agent" in tree or len(self.panes) != len(leaves(tree)):
            return
        geometry = {
            pane: [state.left, state.top, state.width, state.height]
            for pane, state in self.state().panes.items()
        }

        def measure(node):
            if "agent" in node:
                return geometry[self.panes[node["id"]]]
            a, b = measure(node["first"]), measure(node["second"])
            axis = 2 if node["direction"] == "right" else 3
            total = a[axis] + b[axis]
            node["ratio"] = min(0.85, max(0.15, a[axis] / total))
            return [
                min(a[0], b[0]),
                min(a[1], b[1]),
                max(a[0] + a[2], b[0] + b[2]) - min(a[0], b[0]),
                max(a[1] + a[3], b[1] + b[3]) - min(a[1], b[1]),
            ]

        with contextlib.suppress(KeyError):
            measure(tree)
            if self._rendered_key is not None:
                # A border drag already changed these clients' geometry. Saving
                # its ratios must not turn the next name edit into a rebuild,
                # nor the next switch to a like tab: the shape on display is
                # the measured one.
                self._rendered_key = (self._rendered_key[0], self._layout_key(tree))
                self._rendered_shape = self._shape_key(tree)

    def cancel_resize(self) -> None:
        if self._split_drag is not None:
            self._split_drag = None
            self.tmux.run("set-option", "-g", "@viewer_resizing", "0")

    def resize_split(self, tab: dict | None, phase: str, x: int, y: int) -> bool:
        """Move one padded separator over existing panes; never resize a gutter."""
        if phase == "start":
            self._split_drag = None
            self.tmux.run("set-option", "-g", "@viewer_resizing", "0")
        drag = self._split_drag
        if phase == "end":
            self._split_drag = None
            self.tmux.run("set-option", "-g", "@viewer_resizing", "0")
        if x < 0 or y < 0:
            if phase == "start" and self.padded and tab:
                # Hidden borders have no pane-relative coordinates. Consume
                # their whole gesture: older tmux releases classify motion
                # crossing into content as MouseDrag1Pane, which otherwise
                # starts a selection and changes the focused pane.
                self.tmux.batch([["copy-mode", "-q", "-t", pane] for pane in self.panes.values()])
                self._split_drag = {}  # Captured padding gesture, without a split to resize.
                self.tmux.run("set-option", "-g", "@viewer_resizing", "1")
            return False
        if drag == {}:
            return False  # Padding remains captured even in a focused or narrow layout.
        if not self.padded or not tab or len(self.panes) != len(leaves(tab["tree"])):
            self.cancel_resize()
            return False
        self.invalidate_snapshot()
        state = self.state()
        nodes = padded_layout.splits(tab["tree"])
        if len(nodes) != len(self._band_gutters) or len(self._blank_gutters) != 2:
            self.cancel_resize()
            return False
        bands = dict(zip((node["id"] for node in nodes), self._band_gutters, strict=True))
        if phase == "start":
            for node in nodes:
                band = state.panes[bands[node["id"]]]
                right = node["direction"] == "right"
                hit = (
                    band.left - 1 <= x <= band.left + 1 and band.top <= y < band.top + band.height
                    if right
                    else band.top - 1 <= y <= band.top + 1
                    and band.left <= x < band.left + band.width
                )
                if not hit:
                    continue
                # A frozen viewer selection must not switch later drag events
                # into a copy-mode key table as the pointer crosses content.
                self.tmux.batch([["copy-mode", "-q", "-t", pane] for pane in self.panes.values()])
                self.remember_ratios(tab["tree"])
                rect = padded_layout.bounds(node, self.panes, state.panes)
                axis = 0 if right else 1
                available = rect[axis + 2] - 3
                first = padded_layout.bounds(node["first"], self.panes, state.panes)[axis + 2]
                self._split_drag = {
                    "tab": tab["id"],
                    "node": node["id"],
                    "size": state.size,
                    "panes": dict(self.panes),
                    "geometry": self._geometry(state),
                    "start": x if right else y,
                    "first": first,
                    "available": available,
                }
                self.tmux.run("set-option", "-g", "@viewer_resizing", "1")
                break
            return False
        if not drag:
            return False
        if (
            drag["tab"] != tab["id"]
            or drag["size"] != state.size
            or drag["panes"] != self.panes
            or drag["geometry"] != self._geometry(state)
        ):
            self.cancel_resize()
            return False
        node = next((node for node in nodes if node["id"] == drag["node"]), None)
        if node is None:
            self.cancel_resize()
            return False
        axis = 0 if node["direction"] == "right" else 1
        available = drag["available"]
        lower = max(padded_layout.minimum(node["first"])[axis], round(available * 0.15))
        upper = min(
            available - padded_layout.minimum(node["second"])[axis], round(available * 0.85)
        )
        if lower > upper:
            return False
        first = min(max(drag["first"] + (x if axis == 0 else y) - drag["start"], lower), upper)
        previous = node.get("ratio", 0.5)
        ratio = first / available
        if ratio == previous:
            return False
        node["ratio"] = ratio
        try:
            encoded = padded_layout.layout(
                tab["tree"], self.panes, bands, self.sidebar, self._blank_gutters, state
            )
            self.tmux.run("select-layout", "-t", self.sidebar, encoded)
        except (RuntimeError, OSError, ValueError):
            node["ratio"] = previous
            raise
        self.invalidate_snapshot()
        self.remember_ratios(tab["tree"])
        self._rendered_geometry = self._geometry(self.state())
        drag["geometry"] = self._rendered_geometry
        return True
