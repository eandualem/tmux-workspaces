"""Workspace navigation and input. Terminal programs stay in tmux attachments."""

from __future__ import annotations

import contextlib
import curses
import os
import time
from collections.abc import Callable

from .controls import DIRECT_SHORTCUTS, Actions
from .display import Display
from .events import InputEvents
from .model import LayoutConflict, Model, leaves
from .name_editor import NameEditor, cells
from .persistence import Store
from .source import Source


def visible(text: str) -> str:
    return "".join(char for char in str(text) if char.isprintable())


class Sidebar:
    def __init__(
        self,
        screen,
        model: Model,
        store: Store,
        source: Source,
        display: Display,
        actions: Actions,
        shortcut_hints: str = "prefix",
    ):
        self.screen, self.model, self.store = screen, model, store
        self.source, self.display = source, display
        self.actions = actions
        self.shortcut_hints = shortcut_hints
        self.hits: list[tuple[int, int, int, Callable]] = []
        self.context_hits: list[tuple[int, int, int, Callable]] = []
        self.menu: str | None = None
        self.name_hits: list[tuple[int, int, int, str]] = []
        self.last_name_click: tuple[str, float, int, int] | None = None
        self.inline_editor: NameEditor | None = None
        self.inline_target: tuple[str, str | None, str] | None = None
        self.pending = ""
        self.query = ""
        self.replace_name = False
        self.attach_target: tuple[str, str, str | None, str | None] | None = None
        self.offset = 0
        self.tab_offset = 0
        self.message = ""
        self.running = True
        self.last_frame = None

    def save(self) -> None:
        try:
            self.store.save(self.model)
        except LayoutConflict:
            self.display.render(self.model.tab, self.model.state["focus"])
            self.display.tmux.run("select-pane", "-t", self.display.sidebar)
            raise

    def remember(self) -> None:
        tab = self.model.tab
        if tab:
            focused = self.display.focused_leaf()
            if focused:
                tab["focus"] = focused
            self.display.remember_ratios(tab["tree"])
            self.display.shells.remember_many(leaves(tab["tree"]))

    def clear_inline(self, *, focus: bool = False) -> None:
        if self.inline_editor and self.message in {"Enter a tab name", "Enter a workspace name"}:
            self.message = ""
        self.inline_editor, self.inline_target, self.last_name_click = None, None, None
        if self.menu == "inline-name":
            self.menu = None
        if focus and self.model.tab:
            self.display.select(self.model.tab["focus"])

    def click_name(self, key: str, x: int, y: int, double: bool = False) -> None:
        workspace = key.startswith("workspace:")
        item = (
            self.model.space
            if workspace and key == "workspace:" + self.model.space["id"]
            else next((t for t in self.model.space["tabs"] if t["id"] == key), None)
        )
        previous, self.last_name_click = self.last_name_click, None
        if item is None:
            return
        if not workspace and item != self.model.tab:
            self.choose_tab(item)
            return
        now = time.monotonic()
        if double or (
            previous
            and previous[0] == key
            and now - previous[1] <= 0.45
            and abs(x - previous[2]) <= 1
            and y == previous[3]
        ):
            self.open_menu("inline-name")
            self.inline_target = (self.model.space["id"], None if workspace else key, item["name"])
            self.inline_editor = NameEditor(item["name"])
            self.message = ""
        else:
            # An active-name click should not rebuild any attachment clients.
            self.remember()
            if self.model.tab:
                self.display.select(self.model.tab["focus"])
            self.last_name_click = (key, now, x, y)

    def accept_inline(self) -> None:
        if not self.inline_editor or not self.inline_target:
            return
        workspace = self.inline_target[1] is None
        label = "Workspace" if workspace else "Tab"
        name = visible(self.inline_editor.value.strip())[:80]
        if not name:
            self.message = f"Enter a {label.lower()} name"
            return
        target = self.inline_target
        try:
            self.store.refresh(self.model)
        except LayoutConflict:
            target = None
        item = self.model.space if workspace else self.model.tab
        identity = (
            (self.model.space["id"], None if workspace else item["id"], item["name"])
            if item
            else None
        )
        if not item or identity != target:
            self.clear_inline()
            self.display.render(self.model.tab, self.model.state["focus"])
            self.display.tmux.run("select-pane", "-t", self.display.sidebar)
            self.message = f"{label} changed; rename again"
            return
        item["name"] = name
        self.message = ""
        self.show()

    def draw_inline(self, row: int, x: int, width: int) -> tuple[int, int]:
        text, column = self.inline_editor.viewport(width)
        style = curses.color_pair(2) | (
            curses.A_REVERSE if self.inline_editor.selected else curses.A_UNDERLINE
        )
        self.put(row, x, text + " " * (width - cells(text)), style, width)
        return row, x + column

    def show(self) -> None:
        self.clear_inline()
        self.menu, self.query, self.offset = None, "", 0
        self.replace_name = False
        self.attach_target = None
        self.save()
        self.display.render(self.model.tab, self.model.state["focus"])

    def choose_tab(self, tab: dict) -> None:
        self.remember()
        index = next(
            (i for i, item in enumerate(self.model.space["tabs"]) if item["id"] == tab["id"]),
            None,
        )
        if index is None:
            self.message = "Tab changed; choose a tab"
            return
        self.model.space["selected"] = tab["id"]
        available = self.tab_capacity()
        if index < self.tab_offset:
            self.tab_offset = index
        elif index >= self.tab_offset + available:
            self.tab_offset = index - available + 1
        self.show()

    def choose_workspace(self, space: dict) -> None:
        self.remember()
        if not any(item["id"] == space["id"] for item in self.model.state["workspaces"]):
            self.message = "Workspace changed; choose a workspace"
            return
        self.model.state["selected"] = space["id"]
        self.tab_offset = 0
        self.show()

    def context_tab(self, tab: dict) -> None:
        if not self.model.tab or self.model.tab["id"] != tab["id"]:
            self.choose_tab(tab)
        if self.model.tab and self.model.tab["id"] == tab["id"]:
            self.open_menu("tab")

    def context_workspace(self, space: dict) -> None:
        if self.model.space["id"] != space["id"]:
            self.choose_workspace(space)
        if self.model.space["id"] == space["id"]:
            self.open_menu("workspace")

    def open_menu(self, name: str, pending: str = "") -> None:
        self.clear_inline()
        self.remember()
        self.menu, self.pending, self.query, self.offset = name, pending, "", 0
        self.replace_name = False
        tab, pane = self.model.tab, self.model.pane
        self.attach_target = (
            (tab["id"], pane["id"], pane["agent"], pane.get("source_socket"))
            if name == "agents" and tab and pane
            else None
        )
        self.display.tmux.run("select-pane", "-t", self.display.sidebar)

    def attach_pane(self, tab_id: str, leaf_id: str) -> None:
        tab = self.model.tab
        pane = next((p for p in leaves(tab["tree"]) if p["id"] == leaf_id), None) if tab else None
        if (
            not tab
            or tab["id"] != tab_id
            or not pane
            or pane["agent"] is not None
            or leaf_id not in self.display.panes
        ):
            self.message = "Pane changed; choose Attach again"
            return
        self.remember()
        tab["focus"] = leaf_id
        self.display.select(leaf_id)
        self.open_menu("agents")

    def new_tab(self) -> None:
        self.remember()
        self.model.add_tab()
        self.tab_offset = max(0, len(self.model.space["tabs"]) - 1)
        self.show()

    def attach(self, name: str | None) -> None:
        self.remember()
        if self.menu == "agents":
            target = self.attach_target
            try:
                self.store.refresh(self.model)
            except LayoutConflict:
                target = None
            tab = self.model.tab
            pane = (
                next((p for p in leaves(tab["tree"]) if p["id"] == target[1]), None)
                if tab and target and tab["id"] == target[0]
                else None
            )
            if not pane or (pane["agent"], pane.get("source_socket")) != target[2:]:
                self.show()
                self.display.tmux.run("select-pane", "-t", self.display.sidebar)
                self.message = "Pane changed; choose Attach again"
                return
            # A click in another pane while the chooser is open must not change
            # the destination selected when opening the chooser.
            tab["focus"] = pane["id"]
        # Demo fixture servers are recreated per window; other references retain
        # their server even if the next launch uses a different chooser socket.
        self.model.attach(name, self.source.socket if self.source.persistent_socket else None)
        self.show()

    def split(self, direction: str) -> None:
        self.remember()
        cwd = self.model.pane.get("cwd") if self.model.pane else None
        self.model.split(direction, cwd)
        self.show()

    def next_tab(self, offset: int) -> None:
        tabs, tab = self.model.space["tabs"], self.model.tab
        if tab:
            self.choose_tab(tabs[(tabs.index(tab) + offset) % len(tabs)])

    def next_workspace(self, offset: int) -> None:
        spaces = self.model.state["workspaces"]
        self.choose_workspace(spaces[(spaces.index(self.model.space) + offset) % len(spaces)])

    def focus_sidebar(self) -> None:
        self.remember()
        self.save()
        self.display.tmux.run("select-pane", "-t", self.display.sidebar)

    def action(self, name: str) -> None:
        self.clear_inline()
        if name.startswith("attach-pane:"):
            _, tab_id, leaf_id = name.split(":")
            self.attach_pane(tab_id, leaf_id)
            return
        if name.startswith("select-tab-"):
            index = int(name.removeprefix("select-tab-")) - 1
            tabs = self.model.space["tabs"]
            if 0 <= index < len(tabs):
                self.choose_tab(tabs[index])
            return
        if name.startswith("select-workspace-"):
            index = int(name.removeprefix("select-workspace-")) - 1
            spaces = self.model.state["workspaces"]
            if 0 <= index < len(spaces):
                self.choose_workspace(spaces[index])
            return
        actions = {
            "new-tab": self.new_tab,
            "split-right": lambda: self.split("right"),
            "split-below": lambda: self.split("below"),
            "attach": lambda: self.open_menu("agents"),
            "rename-tab": lambda: self.rename("rename-tab"),
            "next-tab": lambda: self.next_tab(1),
            "previous-tab": lambda: self.next_tab(-1),
            "next-pane": self.next_pane,
            "previous-pane": lambda: self.next_pane(-1),
            "focus": self.toggle_focus,
            "workspaces": lambda: self.open_menu("spaces"),
            "new-workspace": lambda: self.rename("new-workspace"),
            "next-workspace": lambda: self.next_workspace(1),
            "previous-workspace": lambda: self.next_workspace(-1),
            "rename-workspace": lambda: self.rename("rename-workspace"),
            "sidebar": self.focus_sidebar,
            "quit": self.quit,
            "close-pane": self.close_pane,
            "close-tab": self.close_tab,
        }
        actions[name]()

    def toggle_focus(self) -> None:
        self.remember()
        self.model.state["focus"] = not self.model.state["focus"]
        self.show()

    def next_pane(self, offset: int = 1) -> None:
        self.remember()
        self.model.next_pane(offset)
        if self.model.state["focus"] or self.display.small:
            self.show()
        elif self.model.tab:
            self.display.select(self.model.tab["focus"])
            self.save()

    def rename(self, kind: str) -> None:
        self.open_menu("name", kind)
        if kind == "rename-tab" and self.model.tab:
            self.query = self.model.tab["name"]
            self.replace_name = True
        elif kind == "rename-workspace":
            self.query = self.model.space["name"]
            self.replace_name = True

    def accept_name(self) -> None:
        name = visible(self.query.strip())[:80]
        if not name:
            return
        if self.pending == "new-workspace":
            self.model.add_workspace(name)
        elif self.pending == "rename-tab" and self.model.tab:
            self.model.tab["name"] = name
        elif self.pending == "rename-workspace":
            self.model.space["name"] = name
        self.show()

    def close_tab(self) -> None:
        panes = leaves(self.model.tab["tree"]) if self.model.tab else []
        self.model.close_tab()
        self.show()
        for pane in panes:
            self.display.shells.close(pane)

    def close_pane(self) -> None:
        self.remember()
        pane = self.model.pane
        self.model.close_pane()
        self.show()
        if pane:
            self.display.shells.close(pane)

    def move_tab(self, offset: int) -> None:
        tabs, tab = self.model.space["tabs"], self.model.tab
        if tab:
            index = tabs.index(tab)
            target = min(len(tabs) - 1, max(0, index + offset))
            tabs.insert(target, tabs.pop(index))
        self.show()

    def transfer_tab(self, space: dict) -> None:
        tab = self.model.tab
        if tab:
            self.model.close_tab()
            space["tabs"].append(tab)
            space["selected"] = tab["id"]
            self.model.state["selected"] = space["id"]
        self.show()

    def delete_workspace(self) -> None:
        if self.model.space["tabs"]:
            self.message = "Move/close its tabs first"
            return
        spaces = self.model.state["workspaces"]
        if len(spaces) > 1:
            spaces.remove(self.model.space)
            self.model.state["selected"] = spaces[0]["id"]
        self.show()

    def quit(self) -> None:
        self.remember()
        self.save()
        self.running = False

    def put(self, y: int, x: int, text: str, style: int = 0, width: int | None = None) -> None:
        height, columns = self.screen.getmaxyx()
        if 0 <= y < height and 0 <= x < columns:
            with contextlib.suppress(curses.error):
                self.screen.addnstr(
                    y, x, visible(text), min(width or columns, columns - x - 1), style
                )

    def button(
        self,
        y: int,
        text: str,
        action: Callable,
        *,
        x: int = 1,
        width: int | None = None,
        active: bool = False,
        context: Callable | None = None,
    ) -> None:
        columns = self.screen.getmaxyx()[1]
        width = width or columns - x - 1
        self.put(y, x, text.ljust(width), curses.color_pair(2 if active else 1), width)
        self.hits.append((y, x, x + width, action))
        if context:
            self.context_hits.append((y, x, x + width, context))

    def _options(self, agents: dict) -> list[tuple[str, Callable]]:
        command_shortcuts = self.menu == "command-shortcuts" or (
            self.menu == "shortcuts" and self.shortcut_hints == "command"
        )
        if command_shortcuts:
            labels = {
                "new-tab": "New tab",
                "split-right": "Split right",
                "split-below": "Split below",
                "previous-pane": "Previous pane",
                "next-pane": "Next pane",
                "previous-tab": "Previous tab",
                "next-tab": "Next tab",
                "previous-workspace": "Prev workspace",
                "next-workspace": "Next workspace",
                "new-workspace": "New workspace",
                "rename-tab": "Rename tab",
                "rename-workspace": "Rename workspace",
                "focus": "Focus / layout",
                "attach": "Attach session",
                "sidebar": "Focus navigation",
                "close-pane": "Close pane",
                "close-tab": "Close tab",
            }
            return [
                (
                    f"{DIRECT_SHORTCUTS[action][1]:<8} {label}",
                    lambda action=action: self.action(action),
                )
                for action, label in labels.items()
            ] + [("Ctrl-g shortcuts…", lambda: self.open_menu("prefix-shortcuts"))]
        if self.menu in {"shortcuts", "prefix-shortcuts"}:
            return [
                ("t   New tab", self.new_tab),
                ("v / %   Split right", lambda: self.split("right")),
                ('h / "   Split below', lambda: self.split("below")),
                ("a   Attach session", lambda: self.open_menu("agents")),
                ("r   Rename tab", lambda: self.rename("rename-tab")),
                ("n / p   Next / prev tab", lambda: self.next_tab(1)),
                ("o   Next pane", self.next_pane),
                ("z   Focus / layout", self.toggle_focus),
                ("w   Workspaces", lambda: self.open_menu("spaces")),
                ("W   New workspace", lambda: self.rename("new-workspace")),
                ("[ / ]   Prev / next space", lambda: self.next_workspace(1)),
                ("R   Rename workspace", lambda: self.rename("rename-workspace")),
                ("s   Focus navigation", self.focus_sidebar),
                ("d   Detach viewer", self.quit),
                ("Command profile keys…", lambda: self.open_menu("command-shortcuts")),
            ]
        if self.menu == "agents":
            return [
                (name, lambda name=name: self.attach(name))
                for name in sorted(agents)
                if self.query.casefold() in name.casefold()
            ]
        if self.menu in {"spaces", "move"}:
            return [
                (
                    space["name"],
                    lambda space=space: (
                        self.transfer_tab(space)
                        if self.menu == "move"
                        else self.choose_workspace(space)
                    ),
                )
                for space in self.model.state["workspaces"]
                if self.menu != "move" or space != self.model.space
            ]
        if self.menu == "tab":
            return [
                ("Rename tab", lambda: self.rename("rename-tab")),
                ("Move tab up", lambda: self.move_tab(-1)),
                ("Move tab down", lambda: self.move_tab(1)),
                ("Move to workspace", lambda: self.open_menu("move")),
                ("Attach session", lambda: self.open_menu("agents")),
                ("Return pane to shell", lambda: self.attach(None)),
                ("Close focused pane", self.close_pane),
                ("Close tab", self.close_tab),
            ]
        if self.menu == "workspace":
            return [
                ("Switch workspace", lambda: self.open_menu("spaces")),
                ("New workspace", lambda: self.rename("new-workspace")),
                ("Rename workspace", lambda: self.rename("rename-workspace")),
                ("Delete empty workspace", self.delete_workspace),
            ]
        return []

    def tab_capacity(self) -> int:
        # One row per tab, plus one detail row for the active tab.
        return max(1, self.screen.getmaxyx()[0] - 13)

    def workspace_buttons(self, row: int, width: int) -> None:
        spaces = self.model.state["workspaces"]
        selected = spaces.index(self.model.space)
        button_width = max(5, len(str(len(spaces))) + 4)
        overflow = len(spaces) * (button_width + 1) > width - 6
        if overflow:
            button_width = min(button_width, width - 12)
            count = max(1, (width - 12) // (button_width + 1))
            left = 4
            self.button(row, "[<]", lambda: self.next_workspace(-1), width=3)
            self.button(row, "[>]", lambda: self.next_workspace(1), x=width - 8, width=3)
        else:
            count, left = len(spaces), 1
        start = max(0, min(selected - count // 2, len(spaces) - count))
        for index, space in enumerate(spaces[start : start + count], start):
            self.button(
                row,
                f"[{index + 1:^{button_width - 2}}]",
                lambda space=space: self.choose_workspace(space),
                x=left + (index - start) * (button_width + 1),
                width=button_width,
                active=space["id"] == self.model.space["id"],
                context=lambda space=space: self.context_workspace(space),
            )
        self.button(row, "[+]", lambda: self.rename("new-workspace"), x=width - 4, width=3)

    def draw(self) -> None:
        agents, error = self.source.snapshot()
        height, width = self.screen.getmaxyx()
        # Skip identical frames: idle sidebar does not repaint the terminal.
        frame = (
            repr(self.model.state),
            repr(agents),
            error,
            height,
            width,
            self.menu,
            repr(self.inline_editor),
            self.query,
            self.replace_name,
            self.offset,
            self.tab_offset,
            self.message,
            self.display.small,
        )
        if frame == self.last_frame:
            return
        self.last_frame = frame
        self.screen.erase()
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        self.name_hits.clear()
        cursor = None
        self.hits.clear()
        self.context_hits.clear()
        if height < 16 or width < 18:
            self.put(0, 0, "Enlarge terminal")
            self.button(2, "Exit viewer", self.quit)
            self.screen.refresh()
            return
        if self.menu and self.menu != "inline-name":
            self.button(0, "< Back", self.show)
            titles = {
                "agents": "Attach to selected pane",
                "spaces": "Workspaces",
                "move": "Move tab to",
                "tab": "Tab options",
                "workspace": "Workspace options",
                "name": "Type a name",
                "shortcuts": (
                    "Command shortcuts" if self.shortcut_hints == "command" else "Ctrl-g, then…"
                ),
                "prefix-shortcuts": "Ctrl-g, then…",
                "command-shortcuts": "Command profile keys",
            }
            self.put(2, 1, titles[self.menu], curses.A_BOLD)
            if self.menu == "command-shortcuts" or (
                self.menu == "shortcuts" and self.shortcut_hints == "command"
            ):
                self.put(
                    3,
                    1,
                    "⌘1–9 tabs · ⌘⌥1–9 spaces"
                    if self.shortcut_hints == "command"
                    else "Enable with ./ghostty",
                    curses.color_pair(3),
                )
            if self.menu in {"name", "agents"}:
                self.put(3, 1, "> ", curses.color_pair(2))
                style = curses.color_pair(2) | (curses.A_REVERSE if self.replace_name else 0)
                self.put(3, 3, self.query[-(width - 5) :], style)
            if self.menu == "name":
                self.button(5, "Save name", self.accept_name)
            else:
                options = self._options(agents)
                start = 5 if self.menu == "agents" else 4
                available = max(1, height - start - 3)
                self.offset = min(self.offset, max(0, len(options) - available))
                for row, (label, action) in enumerate(
                    options[self.offset : self.offset + available], start
                ):
                    self.button(row, label, action)
                if not options:
                    self.put(
                        start, 1, "No matching sessions" if self.menu == "agents" else "No entries"
                    )
                self.button(height - 2, "↑", lambda: self.scroll(-1), width=5)
                self.button(height - 2, "↓", lambda: self.scroll(1), x=8, width=5)
        else:
            workspace_edit = self.inline_editor and self.inline_target[1] is None
            self.name_hits.append((0, 1, width - 7, "workspace:" + self.model.space["id"]))
            self.put(0, 1, self.model.space["name"], curses.A_BOLD, width - 8)
            if workspace_edit:
                cursor = self.draw_inline(0, 1, width - 8)
            self.context_hits.append(
                (0, 1, width - 7, lambda: self.context_workspace(self.model.space))
            )
            self.button(0, "[ + ]", self.new_tab, x=width - 6, width=5)
            hint = "Enter save · Esc cancel" if width >= 25 else "↵ save · Esc cancel"
            self.put(
                1,
                1,
                hint if workspace_edit else "─" * (width - 2),
                curses.color_pair(3 if workspace_edit else 4),
            )
            tab = self.model.tab
            tabs = self.model.space["tabs"]
            bottom = height - 9
            available = self.tab_capacity()
            self.tab_offset = min(self.tab_offset, max(0, len(tabs) - available))
            row = 2
            for index, item in enumerate(
                tabs[self.tab_offset : self.tab_offset + available], self.tab_offset
            ):

                def action(item=item):
                    self.choose_tab(item)

                def context(item=item):
                    self.context_tab(item)

                members = leaves(item["tree"])
                active = item == tab
                prefix = f"{'▶' if active else ' '} {index + 1} "
                count = str(len(members))
                room = width - len(prefix) - len(count) - 3
                name = visible(item["name"])
                if len(name) > room:
                    name = name[: max(0, room - 1)] + "…"
                self.button(row, prefix + name, action, x=0, active=active, context=context)
                self.put(
                    row,
                    width - len(count) - 2,
                    count,
                    curses.color_pair(2 if active else 4),
                )
                self.name_hits.append((row, len(prefix), len(prefix) + room, item["id"]))
                tab_edit = active and self.inline_editor and self.inline_target[1] == item["id"]
                if tab_edit:
                    cursor = self.draw_inline(row, len(prefix), room)
                row += 1
                if not active:
                    continue
                attached = [p["agent"] for p in members if p["agent"]]
                if len(members) > 1:
                    label = f"{len(members)} panes"
                    if attached:
                        label += f" · {len(attached)} attached"
                elif attached:
                    source_socket = members[0].get("source_socket") or self.source.socket
                    if os.path.realpath(source_socket) != os.path.realpath(self.source.socket):
                        state = "saved server"
                    else:
                        session = agents.get(attached[0], {})
                        state = (
                            session.get("state", "offline") if session.get("online") else "offline"
                        )
                    label = attached[0] + " · " + state
                else:
                    label = "Shell"
                if tab_edit:
                    label = hint
                self.put(row, 1 if tab_edit else 3, label, curses.color_pair(3))
                self.hits.append((row, 0, width - 1, action))
                self.context_hits.append((row, 0, width - 1, context))
                row += 1
            if not tabs:
                self.put(2, 1, "No tabs yet", curses.color_pair(4))
                self.button(3, "Open a terminal +", self.new_tab)
            half = (width - 2) // 2
            self.put(bottom - 1, 1, "─" * (width - 2), curses.color_pair(4))
            if len(tabs) > available:
                self.button(bottom - 1, "↑ Tabs", lambda: self.scroll(-1), width=half)
                self.button(bottom - 1, "↓ Tabs", lambda: self.scroll(1), x=1 + half)
            self.button(bottom, "Split →", lambda: self.split("right"), width=half)
            self.button(bottom, "Split ↓", lambda: self.split("below"), x=1 + half)
            label = "Layout" if self.model.state["focus"] else "Focus"
            self.button(bottom + 1, label, self.toggle_focus, width=half)
            self.button(bottom + 1, "Next →", self.next_pane, x=1 + half)
            self.button(bottom + 2, "Attach session…", lambda: self.open_menu("agents"))
            self.put(bottom + 2, 1, "Attach session…", curses.color_pair(3))
            self.button(bottom + 3, "Tab actions…", lambda: self.open_menu("tab"))
            self.button(
                bottom + 4, "Shortcuts", lambda: self.open_menu("shortcuts"), width=width - 8
            )
            self.button(bottom + 4, "Exit", self.quit, x=width - 6, width=5)
            self.put(bottom + 5, 1, "─" * (width - 2), curses.color_pair(4))
            self.button(bottom + 6, "Workspaces…", lambda: self.open_menu("workspace"))
            self.workspace_buttons(bottom + 7, width)
            self.put(
                bottom + 8,
                1,
                error
                or self.message
                or ("Narrow: focus view" if self.display.small and tab else "Layouts saved"),
                curses.color_pair(3 if error or self.message else 4),
            )
        if cursor:
            with contextlib.suppress(curses.error):
                curses.curs_set(1)
                self.screen.move(*cursor)
        self.screen.refresh()

    def scroll(self, amount: int) -> None:
        self.clear_inline()
        if self.menu:
            self.offset = max(0, self.offset + amount)
        else:
            self.tab_offset = max(0, self.tab_offset + amount)

    def mouse(self, x: int, y: int, buttons: int) -> None:
        left = buttons & (
            curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED
        )
        right = buttons & (curses.BUTTON3_PRESSED | curses.BUTTON3_CLICKED)
        if buttons & curses.BUTTON4_PRESSED:
            self.scroll(-1)
        elif buttons & getattr(curses, "BUTTON5_PRESSED", 0):
            self.scroll(1)
        elif left or right:
            field = next((h for h in self.name_hits if h[0] == y and h[1] <= x < h[2]), None)
            edit_key = (
                self.inline_target[1] or "workspace:" + self.inline_target[0]
                if self.inline_target
                else None
            )
            if left and self.inline_editor and field and field[3] == edit_key:
                self.inline_editor.click(x - field[1], field[2] - field[1])
                return
            if self.inline_editor:
                self.clear_inline()
            if left and field and not self.menu:
                self.click_name(field[3], x, y, bool(buttons & curses.BUTTON1_DOUBLE_CLICKED))
                return
            self.last_name_click = None
            for row, start, end, action in self.context_hits if right else self.hits:
                if row == y and start <= x < end:
                    action()
                    break

    def input(self, key) -> None:
        if key == curses.KEY_MOUSE:
            with contextlib.suppress(curses.error):
                _, x, y, _, buttons = curses.getmouse()
                self.mouse(x, y, buttons)
        elif self.inline_editor:
            if key in ("\n", "\r"):
                self.accept_inline()
            elif key == "\x1b":
                self.clear_inline(focus=True)
                self.message = ""
            elif key == curses.KEY_RESIZE:
                self.last_name_click = None
            else:
                self.inline_editor.key(key)
        elif key == "\x1b":
            self.show()
        elif key == "\n" and self.menu == "name":
            self.accept_name()
        elif self.menu in {"name", "agents"}:
            if key == "\x15":
                self.query = ""
                self.replace_name = False
            elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                self.query = "" if self.replace_name else self.query[:-1]
                self.replace_name = False
            elif (
                isinstance(key, str)
                and key.isprintable()
                and (self.replace_name or len(self.query) < 80)
            ):
                self.query = key if self.replace_name else self.query + key
                self.replace_name = False
            self.offset = 0
        elif key == curses.KEY_UP:
            self.scroll(-1)
        elif key == curses.KEY_DOWN:
            self.scroll(1)

    def run(self) -> None:
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, -1, -1)
        curses.init_pair(
            2,
            231 if curses.COLORS >= 256 else curses.COLOR_WHITE,
            238 if curses.COLORS >= 256 else curses.COLOR_BLUE,
        )
        curses.init_pair(4, 245 if curses.COLORS >= 256 else curses.COLOR_WHITE, -1)
        curses.init_pair(3, 108 if curses.COLORS >= 256 else curses.COLOR_CYAN, -1)
        curses.mouseinterval(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        self.screen.keypad(True)
        self.screen.timeout(0)
        events = InputEvents(self.screen, self.actions.receiver)
        self.source.start()
        self.display.setup()
        self.show()
        next_poll = 0.0
        while self.running:
            try:
                for action in self.actions.pending():
                    self.action(action)
                    self.draw()
                now = time.monotonic()
                if now >= next_poll:
                    next_poll = now + 0.6
                    if (
                        self.inline_editor
                        and self.display.tmux.run(
                            "display-message", "-p", "-t", "viewer:", "#{pane_id}"
                        )
                        != self.display.sidebar
                    ):
                        self.clear_inline()
                    if not self.menu:
                        before = repr(self.model.tab)
                        before_tree = repr(self.model.tab["tree"]) if self.model.tab else None
                        try:
                            self.store.refresh(self.model)
                        except LayoutConflict as exc:
                            self.message = str(exc)
                        if before != repr(self.model.tab):
                            self.display.render(self.model.tab, self.model.state["focus"])
                            if before_tree != (
                                repr(self.model.tab["tree"]) if self.model.tab else None
                            ):
                                # A peer changed the pane or attachment. Do not redirect typing.
                                self.display.tmux.run("select-pane", "-t", self.display.sidebar)
                                self.message = "Tab changed; choose a pane"
                    focused = self.display.focused_leaf()
                    if focused and self.model.tab and focused != self.model.tab["focus"]:
                        self.model.tab["focus"] = focused
                        self.save()
                    size = self.display.size()
                    if size != self.display.last_size:
                        # tmux already resized the panes; don't mistake this for a user ratio edit.
                        self.display.render(self.model.tab, self.model.state["focus"])
                        self.last_name_click = None
                        if self.inline_editor:
                            self.display.tmux.run("select-pane", "-t", self.display.sidebar)
                self.draw()
                key = events.read_or_wait(next_poll)
                if key is not None:
                    self.input(key)
                    if key == curses.KEY_RESIZE:
                        next_poll = 0.0
            except (RuntimeError, OSError, ValueError) as exc:
                self.message = visible(str(exc))[:100]
                self.last_frame = None
                time.sleep(0.1)
        self.source.close()
