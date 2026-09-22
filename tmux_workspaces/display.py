"""Render split arrangements into a private tmux display server."""

import contextlib
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass

from .entrypoints import script_command
from .keymap import DEFAULT_KEYMAP, Keymap, direct_sequence
from .model import is_empty, leaves, minimum_size
from .shells import Shells
from .tmux import Tmux, command_args

# The navigation panel: a fixed number of columns, flush to the left edge,
# followed by tmux's single separator column.
SIDEBAR_WIDTH = 22
SIDEBAR_PANE = SIDEBAR_WIDTH
CONTENT_LEFT = SIDEBAR_PANE + 1
# The popups: the editors and the shortcut reference, centred over the content.
POPUP_WIDTH = 88


@dataclass(frozen=True)
class PaneState:
    active: int
    last: int
    dead: int
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class DisplayState:
    size: tuple[int, int]
    panes: dict[str, PaneState]


class _LayoutTooSmall(ValueError):
    """The live pane no longer has room for the planned subtree."""


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
        self.chooser_theme_state: str | None = None
        # Two grounds, both painted by tmux so RGB values work. The panel is
        # the sidebar's and the status row's; the surface is the terminals'.
        # tmux's own borders separate the panes: one thin line in the
        # separator color, drawn on the surface, with no padding beside it.
        self.panel_color = "default"
        self.surface = "default"
        self.separator = "default"
        # The status row's colors, as tmux styles; set with the grounds.
        self.status_styles: dict[str, str] = {}
        self._status_format: str | None = None
        self._rule_columns = 0
        # Whether the attached terminal draws an overline, as tmux reports it.
        # Then the footer is one row, its rule an overline on the text; until a
        # client is seen, and for terminals without it, a rule row sits above.
        self.overline: bool | None = None
        self._empty_label = self._compose_empty_label()
        self._setup_done = False
        self._content_panes: set[str] = set()
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

    def setup(self) -> None:
        commands = []
        for name, value in {
            "mouse": "on",
            # A compact footer: one rule row and one text row.
            "status": "2",
            "status-position": "bottom",
            "status-interval": "0",
            "status-left": "",
            "status-right": "",
            "status-left-length": "0",
            "status-right-length": "0",
            "status-style": self._status_style(),
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
        }.items():
            commands.append(["set-option", "-g", name, value])
        commands.append(["set-option", "-g", "status-format[1]", self._status_format or ""])
        # The rule spans the panel until the first render learns the width.
        commands.extend(self._rule_commands())
        for name, value in {
            "pane-border-status": "off",
            "pane-border-lines": "single",
            "pane-border-indicators": "off",
            "pane-border-style": self._border_style(),
            "pane-active-border-style": self._border_style(),
            "automatic-rename": "off",
            "allow-rename": "off",
            "window-size": "latest",
            "remain-on-exit": "on",
            "pane-border-format": "",
        }.items():
            commands.append(["set-window-option", "-g", name, value])
        commands.append(["set-option", "-p", "-t", self.sidebar, "@viewer_agent", "Workspaces"])
        commands.extend(self._ground_commands(self.sidebar, "panel"))
        # Selecting another pane ends a selection left in the one before, so a
        # highlight never lingers where typing no longer goes.
        commands.append(["set-hook", "-g", "after-select-pane", "copy-mode -q -t '{last}'"])
        # Forward wheel events to nested tmux, whose copy-mode owns agent scrollback.
        for key in ("WheelUpPane", "WheelDownPane"):
            commands.append(["bind-key", "-n", key, "send-keys", "-M"])
        # Hold this client's command queue until sidebar clicks have applied
        # their action and focus. Raw forwarding races text from the same read
        # into the sidebar before it has handled navigation.
        native_click = "select-pane -t = ; send-keys -M"
        native_double = "select-pane -t = ; copy-mode -H ; send-keys -X select-word"
        # Selection belongs to the viewer, including when a nested application
        # requests mouse events. Its frozen pane buffer cannot include a sibling
        # pane, and background output cannot erase the user's highlight.
        commands.append(
            [
                "bind-key",
                "-n",
                "MouseDrag1Pane",
                "if-shell",
                "-F",
                f"#{{==:#{{mouse_pane}},{self.sidebar}}}",
                "send-keys -M",
                "select-pane -t = ; copy-mode -M",
            ]
        )
        # The agents toggle in the status row is a click target: the range it
        # is drawn in names the action.
        commands.append(
            [
                "bind-key",
                "-n",
                "MouseDown1Status",
                "if-shell",
                "-F",
                "#{==:#{mouse_status_range},agents}",
                "run-shell "
                + shlex.quote(
                    script_command(
                        "_action",
                        "--action-socket",
                        self.action_socket,
                        "--action",
                        "show-agents",
                        "--wait-action",
                    )
                ),
                "",
            ]
        )
        for table in ("copy-mode", "copy-mode-vi"):
            commands.append(
                [
                    "bind-key",
                    "-T",
                    table,
                    "MouseDragEnd1Pane",
                    "send-keys",
                    "-X",
                    "stop-selection",
                ]
            )
            commands.append(
                [
                    "bind-key",
                    "-T",
                    table,
                    "MouseDown1Pane",
                    "copy-mode -q ; select-pane -t = ; send-keys -M",
                ]
            )
            for key, selection in (
                ("DoubleClick1Pane", "select-word"),
                ("TripleClick1Pane", "select-line"),
            ):
                commands.append(["bind-key", "-T", table, key, "send-keys", "-X", selection])
            commands.append(["bind-key", "-T", table, "Escape", "send-keys", "-X", "cancel"])
        for key, native in (
            ("MouseDown1Pane", native_click),
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
            commands.append(
                [
                    "bind-key",
                    "-n",
                    key,
                    "if-shell",
                    "-F",
                    f"#{{==:#{{mouse_pane}},{self.sidebar}}}",
                    "select-pane -t = ; run-shell " + shlex.quote(command),
                    native,
                ]
            )
        # tmux's DoubleClick is a delayed duplicate of SecondClick. The
        # sidebar already handles double clicks on the physical downs above;
        # processing this notification again can reopen a just-accepted editor.
        commands.append(
            [
                "bind-key",
                "-n",
                "DoubleClick1Pane",
                "if-shell",
                "-F",
                f"#{{!=:#{{mouse_pane}},{self.sidebar}}}",
                native_double,
            ]
        )
        # Remove native prefix commands on this private server too: an unbound
        # viewer new-tab key must not create an unmanaged tmux window instead.
        commands.append(["unbind-key", "-a", "-T", "prefix"])
        commands.append(["bind-key", self.keymap.prefix, "send-prefix"])
        commands.append(["bind-key", "Escape", "switch-client", "-T", "root"])
        for key, action in self.keymap.prefix_items():
            commands.append(
                [
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
                ]
            )
        for index, action in enumerate(
            action for action, keys in self.keymap.direct.items() if keys
        ):
            commands.append(["set-option", "-s", f"user-keys[{index}]", direct_sequence(action)])
            commands.append(
                [
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
                ]
            )

        # tmux's command IPC message is limited to 16 KiB, including metadata.
        # Only setup may span queues; focus-sensitive rendering batches stay whole.
        batch, size = [], 0
        for command in commands:
            length = sum(len(os.fsencode(arg)) + 1 for arg in command_args([command]))
            if batch and size + 2 + length > 8192:
                self.tmux.batch(batch)
                batch, size = [], 0
            size += length + (2 if batch else 0)  # Separator argument and its NUL.
            batch.append(command)
        self.tmux.batch(batch)
        self._setup_done = True

    def copy_selection(self) -> None:
        """Copy only an explicit viewer selection, leaving its highlight intact."""
        self.tmux.run(
            "if-shell",
            "-F",
            "#{selection_present}",
            "send-keys -X copy-selection-no-clear",
        )

    def _border_style(self) -> str:
        """The separators: one thin line in the separator color on the surface,
        between the sidebar and the content and between split panes alike."""
        return f"fg={self.separator},bg={self.surface}"

    def _status_style(self) -> str:
        """The status row's ground and its default text color."""
        text = self.status_styles.get("muted", "default")
        return f"fg={text},bg={self.panel_color}" + (",overline" if self.overline else "")

    def _rule_format(self) -> str:
        """Place the rule at the bottom of its cell, directly above the text.

        A terminal rule still needs a whole row, unlike a CSS border.
        """
        outline = self.status_styles.get("outline", "default")
        columns = max(self._rule_columns, SIDEBAR_PANE)
        return f"#[align=left]#[fg={outline},bg={self.panel_color}]" + "▁" * columns

    def _rule_commands(self) -> list[list[str]]:
        if self.overline:
            return []
        return [["set-option", "-g", "status-format[0]", self._rule_format()]]

    def _detect_overline(self) -> None:
        """Settle the footer's form once the terminal has attached."""
        features = self.tmux.run("list-clients", "-F", "#{client_termfeatures}", check=False)
        if not features.strip():
            return
        self.overline = "overline" in features.splitlines()[0].split(",")
        if self.overline:
            self.tmux.batch(
                [
                    ["set-option", "-g", "status", "on"],
                    ["set-option", "-g", "status-style", self._status_style()],
                    ["set-option", "-g", "status-format[0]", self._status_format or ""],
                ]
            )

    def _ground_commands(self, pane: str, ground: str) -> list[list[str]]:
        """Give a pane one of the grounds: panel, surface or default."""
        color = {"panel": self.panel_color, "surface": self.surface}.get(ground, "default")
        style = "default" if color == "default" else f"bg={color}"
        return [
            ["set-option", "-p", "-t", pane, name, style]
            for name in ("window-style", "window-active-style")
        ]

    def style_panel(
        self,
        panel: str,
        surface: str = "default",
        separator: str | None = None,
        status_styles: dict[str, str] | None = None,
    ) -> None:
        """Paint the grounds: the sidebar's panel, the terminals' surface, the
        separators between panes and the status row.

        Called whenever a palette installs, including after a saved theme edit,
        so every ground changes with the sidebar rather than a step behind it.
        No border marks the focused pane. Before ``setup`` the values are only
        remembered; ``setup`` applies them with the window options.
        """
        self.panel_color, self.surface = panel, surface
        self.separator = separator or panel
        self.status_styles = dict(status_styles or {})
        # Palette installation changes the startup palette of existing
        # choosers, even when the tab and its geometry are unchanged.
        # Invalidate both the same-tab fast path and container reuse.
        self._rendered_key = self._rendered_shape = self._rendered_geometry = None
        if not self._setup_done:
            return
        style = self._border_style()
        commands = [
            ["set-window-option", "-g", "pane-border-style", style],
            ["set-window-option", "-g", "pane-active-border-style", style],
            ["set-option", "-g", "status-style", self._status_style()],
            *self._rule_commands(),
            *self._ground_commands(self.sidebar, "panel"),
        ]
        for pane in sorted(self._content_panes):
            commands += self._ground_commands(pane, "surface")
        self.tmux.batch(commands)

    def set_status(self, status: str) -> None:
        """Replace the status row's text. Identical text is not resent."""
        if status == self._status_format:
            return
        self._status_format = status
        if self._setup_done:
            row = 0 if self.overline else 1
            self.tmux.run("set-option", "-g", f"status-format[{row}]", status)

    def popup_geometry(self, height: int) -> tuple[int, int, int, int]:
        """Where a popup of ``height`` rows goes: centred over the content area,
        as ``display-popup`` takes it (left column, bottom row, width, height).
        Panes stay visible around it; a small window gets what fits."""
        cols, rows = self.size()
        width = min(POPUP_WIDTH, max(20, cols - CONTENT_LEFT - 2))
        height = max(6, min(height, rows))
        left = CONTENT_LEFT + max(0, (cols - CONTENT_LEFT - width) // 2)
        top = max(0, (rows - height) // 2)
        return left, top + height, width, height

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
            "#{window_width} #{window_height}",
        ).splitlines():
            pane, *fields = line.split()
            values = [int(field) for field in fields]
            if len(values) != 9:
                raise ValueError("Incomplete viewer pane state")
            panes[pane] = PaneState(*values[:7])
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
        identities: dict[str, list[str]] = {}

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
            panes = {
                parts[0]: parts for row in rows.splitlines() if len(parts := row.split("|")) == 7
            }
            for pane, identity in identities.items():
                current = panes.get(pane)
                if not current or current[:3] + current[4:] != identity:
                    raise RuntimeError("Terminal attachment changed; try selecting it again")
            return next((parts for parts in panes.values() if parts[3] == "1"), None)

        try:
            initial = focused()
            while True:
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
                if initial[1:3] != [leaf["id"], tab["id"]]:
                    raise RuntimeError("Terminal attachment changed; try selecting it again")
                if leaf["agent"] or is_empty(leaf):
                    return
                if initial[4] != "0" or not initial[5]:
                    raise RuntimeError("Terminal attachment changed; try selecting it again")
                identities[initial[0]] = initial[:3] + initial[4:]
                expected = f"{Shells.name(leaf)}|{initial[5]}"
                clients = query(
                    self.shells.tmux, "list-clients", "-F", "#{session_name}|#{client_tty}"
                )
                # A click can supersede this selection while its client starts.
                # Validate the original identities, then follow the new target
                # without restarting the deadline or releasing input early.
                current = focused()
                if current != initial:
                    initial = current
                    continue
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

    def _compose_empty_label(self) -> str:
        """The empty workspace's one line: the add glyph and the keys that
        open a first tab or attach a session, in the roles the leaf paints."""
        from .attachments import LABEL_SEPARATOR
        from .keymap import short_key_label

        prefix = short_key_label(self.keymap.prefix)
        parts = [("muted", "Empty workspace. "), ("accent", "+")]
        for action, words in (
            ("new-tab", " opens the first tab"),
            ("attach", " attaches a running session"),
        ):
            keys = self.keymap.label(action)
            if not keys:
                continue
            parts += [("muted", " or " if action == "new-tab" else " · ")]
            parts += [("normal", f"{prefix} {keys.split(' / ')[0]}"), ("muted", words)]
        return (
            LABEL_SEPARATOR.join(f"{role}:{text}" for role, text in parts)
            + LABEL_SEPARATOR
            + "muted:."
        )

    def _leaf_command(self, pane: dict | None) -> str:
        if not pane:
            # The empty workspace: one quiet line naming the ways to a first tab.
            return script_command(
                "_leaf",
                "--label",
                self._empty_label or "",
                *(
                    ("--chooser-theme", self.chooser_theme_state)
                    if self.chooser_theme_state
                    else ()
                ),
            )
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
                *(
                    ("--chooser-theme", self.chooser_theme_state)
                    if self.chooser_theme_state
                    else ()
                ),
                *(("--cwd", pane["cwd"]) if pane.get("cwd") else ()),
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
        self._content_panes = set()
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
            # Every content pane sits on the surface, chooser included. A pane
            # is reused across respawns, so the ground is set every time.
            commands += self._ground_commands(pane, "surface")
            self._content_panes.add(pane)
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
        if self.overline is None and self._setup_done:
            # Before measuring: a one-row footer gives the window another row.
            self._detect_overline()
            if self.overline:
                self.invalidate_snapshot()
        state = self.state()
        for attempt in range(2):
            try:
                self._render_once(tab, focus, state)
                return
            except (RuntimeError, OSError, ValueError) as error:
                # Partial mappings must never become the user's saved focus.
                # A failed render remains dirty so the next poll can recover.
                self.panes.clear()
                self.last_size = (0, 0)
                self._rendered_key = self._rendered_shape = self._rendered_geometry = None
                self.invalidate_snapshot()
                with contextlib.suppress(RuntimeError, OSError, ValueError):
                    self.select_sidebar()
                geometry_error = isinstance(error, _LayoutTooSmall) or (
                    isinstance(error, RuntimeError) and "no space for a new pane" in str(error)
                )
                if attempt == 0 and geometry_error:
                    current = None
                    with contextlib.suppress(RuntimeError, OSError, ValueError):
                        current = self.state()
                    self.invalidate_snapshot()
                    if current and current.size != state.size:
                        state = current
                        continue
                raise

    def _render_once(self, tab: dict | None, focus: bool, state: DisplayState) -> None:
        # Only pane IDs on this dedicated server may be destroyed or rearranged.
        owned = state.panes
        content = [pane for pane in owned if pane != self.sidebar]
        cols, rows = state.size
        sidebar_width = SIDEBAR_PANE
        if cols != self._rule_columns and self._setup_done:
            # The rule is literal text: a new width needs a new one.
            self._rule_columns = cols
            self.tmux.batch(self._rule_commands())
        tree = tab["tree"] if tab else None
        # Narrow displays use temporary focus. The saved tree is never replaced.
        needed_cols, needed_rows = minimum_size(tree)
        self.small = cols - CONTENT_LEFT < needed_cols or rows < needed_rows
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
        self.panes.clear()
        self._tab_id = tab["id"] if tab else ""
        self._shell_names = self.shells.ensure_many(
            [pane for pane in leaves(tree) if not pane["agent"] and not is_empty(pane)]
        )
        first = leaves(tree)[0] if tree else None
        command = self._leaf_command(first)
        if content:
            # Keep a content pane beside the sidebar. Removing all of them lets
            # tmux expand/reflow the sidebar across the entire terminal. Batch
            # sibling removal and width restoration so intermediate layouts
            # cannot be painted or sent to the sidebar as resize events.
            pane = content[0]
            commands = [["kill-pane", "-t", sibling] for sibling in content if sibling != pane]
            commands += [
                ["resize-pane", "-t", self.sidebar, "-x", str(sidebar_width)],
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
        if tree:
            self._tree(tree, pane)
        else:
            self._label(pane, "Empty workspace")
            self.tmux.batch(self._ground_commands(pane, "surface"))
            self._content_panes = {pane}
            self.select_sidebar()
        self._pane_identity(tab)
        self._rendered_key = key
        if tree:
            self._rendered_shape = self._shape_key(tree)
            self._rendered_geometry = self._geometry(self.state())
        self.last_size = (cols, rows)

    def _tree(self, tree: dict, pane: str) -> None:
        if "agent" in tree:
            self.panes[tree["id"]] = pane
            return
        second_leaf = leaves(tree["second"])[0]
        right = tree["direction"] == "right"
        ratio = tree.get("ratio", 0.5)
        # The border between the pair is tmux's own one-cell line. The pane is
        # sized in cells so a ratio measured back from the geometry reproduces
        # it, and tab switches between like layouts reuse their containers.
        size = int(
            self.tmux.run(
                "display-message", "-p", "-t", pane, "#{pane_width}" if right else "#{pane_height}"
            )
        )
        content = size - 1
        axis = 0 if right else 1
        lower, upper = minimum_size(tree["first"])[axis], minimum_size(tree["second"])[axis]
        if content < lower + upper:
            raise _LayoutTooSmall("Window is too small to render this split")
        first = min(max(round(ratio * content), lower), content - upper)
        length = str(content - first)
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
