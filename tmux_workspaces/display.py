"""Render split arrangements into a private tmux display server."""

import contextlib
import os
import re
import shlex
from dataclasses import dataclass

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
        # Pane border colors follow the viewer theme: the muted role for borders
        # at rest and the accent role for the focused pane. Until a theme
        # installs, these are the shipped values.
        self.border_colors = ("colour243", "colour110")
        self._setup_done = False
        self._tab_id = ""
        self.panes: dict[str, str] = {}
        self.last_size = (0, 0)
        self.small = False
        self._shell_names: dict[str, str] = {}
        self._rendered_key = None
        self._rendered_shape = None
        self._rendered_geometry = None
        self._snapshot_enabled = False
        self._snapshot: DisplayState | None = None

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
        }.items():
            self.tmux.run("set-option", "-g", name, value)
        for name, value in {
            "pane-border-status": "off",
            "pane-border-style": "fg=" + self.border_colors[0],
            "pane-active-border-style": "fg=" + self.border_colors[1],
            "automatic-rename": "off",
            "allow-rename": "off",
            "window-size": "latest",
            "remain-on-exit": "on",
            "pane-border-format": "",
        }.items():
            self.tmux.run("set-window-option", "-g", name, value)
        self.tmux.run("set-option", "-p", "-t", self.sidebar, "@viewer_agent", "Workspaces")
        # Forward wheel events to nested tmux, whose copy-mode owns agent scrollback.
        for key in ("WheelUpPane", "WheelDownPane"):
            self.tmux.run("bind-key", "-n", key, "send-keys", "-M")
        # Hold this client's command queue until sidebar clicks have applied
        # their action and focus. Raw forwarding races text from the same read
        # into the sidebar before it has handled navigation.
        native_click = "select-pane -t = ; send-keys -M"
        native_double = (
            "select-pane -t = ; if-shell -F '#{||:#{pane_in_mode},#{mouse_any_flag}}' "
            "{ send-keys -M } "
            "{ copy-mode -H ; send-keys -X select-word ; run-shell -d 0.3 ; "
            "send-keys -X copy-pipe-and-cancel }"
        )
        for key, native in (
            ("MouseDown1Pane", native_click),
            ("SecondClick1Pane", "send-keys -M"),
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

    def style_borders(self, inactive: str, active: str) -> None:
        """Color the pane borders from the theme: two options, applied at once.

        Called whenever a palette installs, including a preview in the colors
        editor, so the borders change with the sidebar rather than a step
        behind it. Before ``setup`` the values are only remembered; ``setup``
        applies them with the rest of the window options.
        """
        self.border_colors = (inactive, active)
        if not self._setup_done:
            return
        self.tmux.batch(
            [
                ["set-window-option", "-g", "pane-border-style", "fg=" + inactive],
                ["set-window-option", "-g", "pane-active-border-style", "fg=" + active],
            ]
        )

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
            size = tuple(values[7:])
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

    def _leaf_command(self, pane: dict | None) -> str:
        if not pane:
            return script_command("_leaf")
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
        content = [pane for pane in owned if pane != self.sidebar]
        cols, rows = state.size
        sidebar_width = min(28, max(24, cols // 4))
        tree = tab["tree"] if tab else None
        # Narrow displays use temporary focus. The saved tree is never replaced.
        needed_cols, needed_rows = minimum_size(tree)
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
        if content:
            # Keep a content pane beside the sidebar. Removing all of them lets
            # tmux expand/reflow the sidebar across the entire terminal. Batch
            # sibling removal and width restoration so intermediate layouts
            # cannot be painted or sent to the sidebar as resize events.
            pane = content[0]
            commands = [["kill-pane", "-t", sibling] for sibling in content[1:]]
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
        sibling = self.tmux.run(
            "split-window",
            "-d",
            "-h" if tree["direction"] == "right" else "-v",
            "-t",
            pane,
            "-l",
            str(round(100 * (1 - tree.get("ratio", 0.5)))) + "%",
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
                # its ratios must not turn the next name edit into a rebuild.
                self._rendered_key = (self._rendered_key[0], self._layout_key(tree))
