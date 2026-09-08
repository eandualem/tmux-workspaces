"""Render split arrangements into a private tmux display server."""

import contextlib
import os
import re
import shlex

from .controls import DIRECT_SHORTCUTS, SHORTCUTS, direct_sequence
from .entrypoints import script_command
from .model import leaves, minimum_size
from .shells import Shells
from .tmux import Tmux


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
    ):
        if len({os.path.realpath(p) for p in (viewer_socket, source_socket, shell_socket)}) != 3:
            raise ValueError("Viewer, terminal and source sockets must differ")
        self.tmux = Tmux(viewer_socket)
        self.source_socket = source_socket
        self.shells = Shells(shell_socket)
        self.action_socket = action_socket
        self.host_socket, self.host_pane = host_socket, host_pane
        self.sidebar = sidebar
        self.panes: dict[str, str] = {}
        self.last_size = (0, 0)
        self.small = False
        self._shell_names: dict[str, str] = {}
        self._rendered_key = None

    def setup(self) -> None:
        for name, value in {
            "mouse": "on",
            "status": "off",
            "prefix": "C-g",
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
            "pane-border-style": "fg=colour238",
            "pane-active-border-style": "fg=colour108",
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
        self.tmux.run("bind-key", "s", "select-pane", "-t", self.sidebar)
        for key, action in SHORTCUTS.items():
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
        for index, action in enumerate(DIRECT_SHORTCUTS):
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
        # c is tmux's usual new-window key; here it creates a saved sidebar tab.
        self.tmux.run(
            "bind-key",
            "c",
            "run-shell",
            script_command(
                "_action",
                "--action-socket",
                self.action_socket,
                "--action",
                "new-tab",
                "--wait-action",
            ),
        )

    def size(self) -> tuple[int, int]:
        return tuple(
            map(
                int,
                self.tmux.run(
                    "display-message", "-p", "-t", self.sidebar, "#{window_width} #{window_height}"
                ).split(),
            )
        )

    def focused_leaf(self) -> str | None:
        states = [
            line.split()
            for line in self.tmux.run(
                "list-panes", "-t", "viewer:", "-F", "#{pane_active} #{pane_last} #{pane_id}"
            ).splitlines()
        ]
        # A sidebar click follows the selected content pane immediately, without
        # depending on the polling interval catching the earlier terminal click.
        for flag in (0, 1):
            for state in states:
                if state[flag] == "1":
                    leaf_id = next(
                        (key for key, pane in self.panes.items() if pane == state[2]), None
                    )
                    if leaf_id:
                        return leaf_id
        return None

    def select(self, leaf_id: str) -> None:
        if leaf_id in self.panes:
            self.tmux.run("select-pane", "-t", self.panes[leaf_id])

    def _leaf_command(self, pane: dict | None) -> str:
        if not pane:
            return script_command("_leaf")
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

    def _pane_identity(self, tab: dict | None) -> None:
        if not tab:
            return
        commands = []
        for leaf in leaves(tab["tree"]):
            pane = self.panes.get(leaf["id"])
            if not pane:
                continue
            for name, value in {
                "@viewer_leaf_id": leaf["id"],
                "@viewer_tab_id": tab["id"],
                "@viewer_agent": re.sub(r"[^\w .-]", "", leaf["agent"] or "Terminal"),
            }.items():
                commands.append(["set-option", "-p", "-t", pane, name, value])
        self.tmux.batch(commands)

    @staticmethod
    def _layout_key(tree: dict | None):
        if not tree:
            return None
        if "agent" in tree:
            # cwd is a saved restart location; changing it does not change the
            # attachment client for an already-running shell.
            return tree["id"], tree["agent"], tree.get("source_socket")
        return (
            tree["id"],
            tree["direction"],
            tree.get("ratio", 0.5),
            Display._layout_key(tree["first"]),
            Display._layout_key(tree["second"]),
        )

    def render(self, tab: dict | None, focus: bool) -> None:
        # Only pane IDs on this dedicated server may be destroyed or rearranged.
        owned = dict(
            line.split()
            for line in self.tmux.run(
                "list-panes", "-t", "viewer:", "-F", "#{pane_id} #{pane_dead}"
            ).splitlines()
        )
        content = [pane for pane in owned if pane != self.sidebar]
        cols, rows = self.size()
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
        if (
            key is not None
            and key == self._rendered_key
            and self.last_size == (cols, rows)
            and set(content) == set(self.panes.values())
            and all(owned[pane] == "0" for pane in content)
        ):
            # Name-only edits, including peer renames, do not replace terminals.
            self.select(tab["focus"])
            return
        self._rendered_key = None
        self.last_size = (cols, rows)
        self.panes.clear()
        self._shell_names = self.shells.ensure_many(
            [pane for pane in leaves(tree) if not pane["agent"]]
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
            self.select(tab["focus"])
        else:
            self._label(pane, "Create a tab")
            self.tmux.run("select-pane", "-t", self.sidebar)
        self._pane_identity(tab)
        self._rendered_key = key

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
        geometry = {}
        for line in self.tmux.run(
            "list-panes",
            "-t",
            "viewer:",
            "-F",
            "#{pane_id} #{pane_left} #{pane_top} #{pane_width} #{pane_height}",
        ).splitlines():
            pane, *values = line.split()
            geometry[pane] = list(map(int, values))

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
