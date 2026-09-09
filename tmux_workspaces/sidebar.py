"""Workspace navigation and input. Terminal programs stay in tmux attachments."""

from __future__ import annotations

import contextlib
import curses
import os
import textwrap
import time
from collections.abc import Callable

from .controls import Actions, mouse_action
from .display import Display
from .events import InputEvents
from .keymap import ACTION_LABELS, DEFAULT_KEYMAP, KeymapFile, tmux_key_label
from .menu import Entry, Selection
from .model import LayoutConflict, Model, leaves
from .name_editor import NameEditor, cells
from .persistence import Store
from .shortcut_editor import CAPTURE_HINT, CONFIRM_HINT, ShortcutEditor
from .shortcut_editor import FIELD_HINT as KEY_FIELD_HINT
from .shortcut_editor import hint as shortcut_hint
from .source import Source
from .theme import DEFAULT_THEME, ROLE_LABELS, ThemeError, ThemeFile, load_theme, theme_path
from .theme_editor import FIELD_HINT, ThemeEditor, failed, failure, fit_labels, hint


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
        theme_path=None,
        terminal_colors: int | None = None,
        relaunch=None,
        keymap_path=None,
    ):
        self.screen, self.model, self.store = screen, model, store
        self.source, self.display = source, display
        self.actions = actions
        self.shortcut_hints = shortcut_hints
        # Colors are read once in run(); construction touches no user file.
        self.theme_path, self.terminal_colors = theme_path, terminal_colors
        self.colors, self.theme, self.palette = 8, None, None
        self.theme_editor: ThemeEditor | None = None
        # The file a shortcut edit would write. Absent means the viewer
        # inherited a map rather than reading one, so Save has no implicit
        # destination and the editor says so instead of guessing a path.
        self.keymap_path = keymap_path
        self.shortcut_editor: ShortcutEditor | None = None

        # Absent outside a launched window; refresh then reports its own limit
        # instead of closing a viewer that nothing would reopen.
        self.relaunch = relaunch
        self.refresh_problem: str | None = None
        self.refresh_command: str | None = None
        self.command_offset = 0
        # Shown inside a menu, where the ordinary status line is not drawn.
        self.menu_message = ""
        self.keymap = display.keymap
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
        self.selection = Selection()
        self.tab_offset = 0
        self.message = ""
        self.running = True
        self.last_frame = None

    @property
    def offset(self) -> int:
        """Scroll position of the open menu."""
        return self.selection.offset

    @offset.setter
    def offset(self, value: int) -> None:
        self.selection.offset = value

    @property
    def options(self) -> list[tuple[str, Callable]]:
        """The menu rows the last frame worked with."""
        return self.selection.rows

    @property
    def selected(self) -> int:
        """Index of the active menu row within those options."""
        return self.selection.index

    def style(self, role: str) -> int:
        """Attributes for a semantic role. Pairs are installed, never per frame."""
        return self.palette.style(role) if self.palette else 0

    def describe(self, role: str) -> str:
        """What a role will actually render as; empty until a palette installs."""
        return self.palette.describe(role) if self.palette else ""

    def message_style(self, message: str, notice: bool) -> int:
        """Failures add bold, so severity survives a reduced or reversed palette."""
        return self.style("accent" if notice else "muted") | (
            curses.A_BOLD if failed(message) else 0
        )

    def install(self, theme) -> None:
        """Show a theme in place: four pair updates, no reopen and no redraw loop."""
        palette = theme.resolve(self.colors)
        palette.install(curses)
        self.theme, self.palette, self.last_frame = theme, palette, None
        # The role names the sidebar's own base, so its empty cells and the
        # cleared frame carry the configured background rather than the
        # terminal's, which is only visible once someone configures one.
        with contextlib.suppress(curses.error):
            self.screen.bkgdset(" ", palette.style("normal"))

    def setup_theme(self) -> None:
        """Read the user's colors once. An unusable file keeps working colors."""
        curses.start_color()
        # The outer client bounds what the user can see; this terminal bounds
        # what init_pair will accept. Neither alone is safe.
        ceiling = getattr(curses, "COLORS", 0) or 8
        self.colors = min(self.terminal_colors or ceiling, ceiling)
        loaded = load_theme(self.theme_path)
        self.theme_path = loaded.path
        if loaded.diagnostic:
            self.message = failure(visible(loaded.diagnostic))[:100]
        try:
            self.install(loaded.theme)
        except ThemeError as error:
            # Colors must never stop the workspace layer: fall back to the
            # shipped theme, and to the terminal's own colors if even that fails.
            self.message = failure(visible(str(error)))[:100]
            with contextlib.suppress(ThemeError):
                self.install(DEFAULT_THEME)

    def save(self) -> None:
        try:
            self.store.save(self.model)
        except LayoutConflict:
            self.display.render(self.model.tab, self.model.state["focus"])
            self.display.select_sidebar()
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
            self.display.select_sidebar()
            self.message = f"{label} changed; rename again"
            return
        item["name"] = name
        self.message = ""
        self.show()

    def draw_inline(self, row: int, x: int, width: int) -> tuple[int, int]:
        return self.draw_field(self.inline_editor, row, x, width)

    def open_theme(self) -> None:
        """Edit the viewer's semantic colors: same route by click or by key."""
        if not self.leave_theme():
            return
        self.leave_shortcuts()
        colors = ThemeFile(theme_path(self.theme_path))
        # Reading now records what is on disk, so an edit made while the editor
        # is open is reported as a conflict instead of being overwritten.
        loaded = colors.read()
        self.open_menu("theme")
        working = self.theme or DEFAULT_THEME
        self.theme_editor = ThemeEditor(
            working,
            colors.write,
            self.install,
            DEFAULT_THEME,
            labels=ROLE_LABELS,
            writable=colors.writable(),
        )
        if loaded.diagnostic:
            self.theme_editor.message = failure(visible(loaded.diagnostic))[:100]
        elif loaded.theme != working and self.theme_editor.preview(loaded.theme):
            # Someone saved other colors since this viewer read the file. Show
            # them, so Apply cannot replace values the user never saw. Cancel
            # still restores the colors the viewer is running.
            self.theme_editor.message = "Saved colors shown"

    def open_shortcut_editor(self) -> None:
        """Open the shortcut editor over the keymap this viewer is running."""
        if self.shortcut_editor:
            return
        if self.keymap_path is None:
            # An inherited snapshot has no file behind it. Writing the implicit
            # default path would create a map the user never chose and silently
            # freeze the keys they are using, so refuse and name the option.
            self.menu_message = failure(
                "this viewer inherited its shortcuts; start it with --keymap FILE to edit them"
            )
            return
        keys = KeymapFile(self.keymap_path)
        # Reading now records what is on disk, so an edit made while the editor
        # is open is reported as a conflict instead of being overwritten.
        loaded = keys.read()
        # The editor edits the file, so it opens on what the file holds. That is
        # not always what this viewer is running: the map was snapshotted at
        # launch and someone may have saved since. Showing the running map here
        # would let Apply replace bindings the user never saw, so the file wins
        # and the difference is named instead.
        stale = loaded.diagnostic is None and loaded.keymap != self.keymap
        self.open_menu("edit-shortcuts")
        self.shortcut_editor = ShortcutEditor(
            loaded.keymap,
            keys.write,
            DEFAULT_KEYMAP,
            self.keymap_path,
            writable=keys.writable(),
            lossy=not keys.saves_cleanly(loaded.keymap),
        )
        if loaded.diagnostic:
            self.shortcut_editor.message = failure(visible(loaded.diagnostic))[:100]
        elif stale:
            self.shortcut_editor.message = "Showing the saved file; it differs from the keys here"
        elif self.shortcut_editor.lossy:
            self.shortcut_editor.message = "Saving rewrites the file; comments are not kept"

    def shortcut_action(self, action: Callable, *args) -> None:
        action(*args)
        editor = self.shortcut_editor
        if editor and editor.closed:
            self.shortcut_editor = None
            self.show()
            self.message = editor.effect() if editor.saved else ""

    def draw_shortcuts(self, height: int, width: int) -> tuple[int, int] | None:
        editor = self.shortcut_editor
        rows = editor.rows()
        if editor.pending is not None:
            guide = CONFIRM_HINT
        elif editor.capturing:
            guide = CAPTURE_HINT
        elif editor.field:
            guide = KEY_FIELD_HINT
        else:
            guide = shortcut_hint(width - 2)
        self.put(3, 1, guide, self.style("accent"))
        start, cursor = 4, None
        available = max(1, height - start - 6)
        offset = max(0, min(editor.index, len(rows) - available))
        if editor.index >= offset + available:
            offset = editor.index - available + 1
        value_width = max(6, (width - 6) // 2)
        column = width - value_width - 1
        for index in range(offset, min(len(rows), offset + available)):
            row = start + index - offset
            label, section, value, active, changed = rows[index]
            if len(value) > value_width:
                value = value[: max(0, value_width - 1)] + "…"
            # The section word disambiguates two rows that name one action, and
            # the marker shows which rows a Save would actually write.
            name = f"{'▶' if active else ' '} {'*' if changed else ''}{label} ({section})"
            name = name[: max(1, column)].ljust(column)
            self.button(
                row,
                name + value,
                lambda index=index: self.shortcut_action(editor.edit, index),
                x=0,
                width=width - 1,
                active=active,
            )
            if active and editor.field:
                cursor = self.draw_field(editor.field, row, column, value_width)
        half = (width - 3) // 2
        self.button(height - 4, "Apply", lambda: self.shortcut_action(editor.apply), width=half)
        self.button(height - 4, "Cancel", lambda: self.shortcut_action(editor.cancel), x=1 + half)
        self.button(height - 3, "Restore default", lambda: self.shortcut_action(editor.restore))
        message = editor.message or str(editor.destination)
        self.put(height - 2, 1, message, self.message_style(message, bool(editor.message)))
        return cursor

    def leave_shortcuts(self) -> bool:
        """Close the shortcut editor if one is open, discarding staged changes."""
        if self.shortcut_editor:
            self.shortcut_editor.cancel()
            if not self.shortcut_editor.closed:
                return False
            self.shortcut_editor = None
            if self.menu == "edit-shortcuts":
                self.menu = None
        return True

    def theme_action(self, action: Callable, *args) -> None:
        action(*args)
        editor = self.theme_editor
        if editor and editor.closed:
            # The editor closed, so its colors are installed: Apply saved them
            # and Cancel put back the ones it opened with.
            self.theme_editor = None
            self.show()
            self.message = "Colors saved" if editor.saved else ""

    def draw_field(self, editor, row: int, x: int, width: int) -> tuple[int, int]:
        text, column = editor.viewport(width)
        style = self.style("active") | (curses.A_REVERSE if editor.selected else curses.A_UNDERLINE)
        self.put(row, x, text + " " * (width - cells(text)), style, width)
        return row, x + column

    def draw_theme(self, height: int, width: int) -> tuple[int, int] | None:
        editor = self.theme_editor
        rows = editor.rows()
        self.put(3, 1, FIELD_HINT if editor.field else hint(width - 2), self.style("accent"))
        start, cursor = 4, None
        available = max(1, height - start - 6)
        offset = max(0, min(editor.index, len(rows) - available))
        if editor.index >= offset + available:
            offset = editor.index - available + 1
        value_width = max(6, (width - 6) // 2)
        column = width - value_width - 1
        # One column of air between a truncated label and its value.
        label_width = max(1, column - 1)
        labels = fit_labels(rows, max(1, label_width - 2))
        for index in range(offset, min(len(rows), offset + available)):
            row = start + index - offset
            value, active = rows[index][2], rows[index][3]
            if len(value) > value_width:
                value = value[: max(0, value_width - 1)] + "…"
            # One styled run per row, so a selected row highlights as a whole.
            name = f"{'▶' if active else ' '} {labels[index]}".ljust(column)
            self.button(
                row,
                name + value,
                lambda index=index: self.theme_action(editor.edit, index),
                x=0,
                width=width - 1,
                active=active,
            )
            if active and editor.field:
                cursor = self.draw_field(editor.field, row, column, value_width)
        half = (width - 3) // 2
        self.button(height - 4, "Apply", lambda: self.theme_action(editor.apply), width=half)
        self.button(height - 4, "Cancel", lambda: self.theme_action(editor.cancel), x=1 + half)
        self.button(height - 3, "Restore defaults", lambda: self.theme_action(editor.defaults))
        message = editor.message or self.describe(editor.target[0])
        self.put(height - 2, 1, message, self.message_style(message, bool(editor.message)))
        return cursor

    def leave_theme(self) -> bool:
        """Close the color editor if one is open, restoring the colors it opened
        with. False means the terminal refused that restore, so the editor and
        its reason stay on screen and the caller must not proceed."""
        if self.theme_editor:
            self.theme_editor.cancel()
            if not self.theme_editor.closed:
                return False
            self.theme_editor = None
            if self.menu == "theme":
                # The screen belongs to the editor, so it goes when the editor
                # does, whether or not the caller opens something else.
                self.menu = None
        return True

    def close_menu(self) -> None:
        """Drop an open menu without moving the keyboard away from its pane."""
        if not self.leave_theme():
            return
        self.leave_shortcuts()
        self.menu, self.query = None, ""
        self.selection.reset()
        self.menu_message = ""
        self.replace_name = False
        self.attach_target = None

    def show(self) -> None:
        if not self.leave_theme():
            return
        self.leave_shortcuts()
        self.clear_inline()
        self.close_menu()
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
        if not self.leave_theme():
            return
        self.leave_shortcuts()
        self.clear_inline()
        self.remember()
        self.menu, self.pending, self.query = name, pending, ""
        self.selection.reset()
        self.menu_message = ""
        self.replace_name = False
        tab, pane = self.model.tab, self.model.pane
        # Tab options own Return pane to shell, so they bind the same target as
        # the chooser: a peer moving the pane must not redirect either command.
        self.attach_target = (
            (tab["id"], pane["id"], pane["agent"], pane.get("source_socket"))
            if name in {"agents", "tab"} and tab and pane
            else None
        )
        self.display.select_sidebar()

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
        if self.attach_target:
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
                self.display.select_sidebar()
                self.message = (
                    "Pane changed; choose Attach again"
                    if name
                    else "Pane changed; return the pane again"
                )
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
        self.display.select_sidebar()

    def action(self, name: str) -> None:
        mouse = mouse_action(name)
        if mouse:
            x, y = mouse
            height, width = self.screen.getmaxyx()
            if x < width and y < height:
                self.mouse(x, y, curses.BUTTON1_PRESSED)
            return
        if name == "refresh-viewer":
            # Replacing this viewer is deliberate, so an unfinished in-place
            # rename is neither discarded nor silently saved on the way there.
            self.refresh_viewer()
            return
        self.clear_inline()
        if name == "quit":
            self.quit()
            return
        if not self.leave_theme():
            return
        self.leave_shortcuts()
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
            "tab-options": lambda: self.open_menu("tab"),
            "workspace-options": lambda: self.open_menu("workspace"),
            "new-workspace": lambda: self.rename("new-workspace"),
            "next-workspace": lambda: self.next_workspace(1),
            "previous-workspace": lambda: self.next_workspace(-1),
            "rename-workspace": lambda: self.rename("rename-workspace"),
            "sidebar": self.focus_sidebar,
            "close-pane": self.close_pane,
            "close-tab": self.close_tab,
        }
        actions[name]()

    def refresh_viewer(self) -> None:
        if self.theme_editor:
            self.theme_editor.message = "Apply or cancel the color edit first"
            return
        if self.shortcut_editor:
            self.shortcut_editor.message = "Apply or cancel the shortcut edit first"
            return
        if self.inline_editor or self.menu == "name":
            # A pending name is unsaved work; never discard it on the way to
            # replacing the window it is being typed in.
            self.message = self.menu_message = "Finish or cancel the name edit first"
            return
        # A window that can only be reopened by hand says so immediately.
        # Everything else is checked once, on confirmation, so opening this
        # menu never runs a probe the shortcut would be waiting on.
        if self.relaunch is None:
            self.refresh_problem, self.refresh_command = "Refresh is unavailable here", None
        else:
            self.refresh_problem = self.relaunch.check() if self.relaunch.manual_reopen else None
            self.refresh_command = self.relaunch.manual_command()
        self.command_offset = 0
        self.open_menu("refresh")

    def refresh_navigation(self) -> dict:
        """This window's own selection, so a replacement never adopts a peer's."""
        tab = self.model.tab
        return {
            "workspace": self.model.space["id"],
            "tab": tab["id"] if tab else "",
            "leaf": tab["focus"] if tab else "",
            "focus": bool(self.model.state["focus"]),
        }

    def confirm_refresh(self) -> None:
        """Replace only this viewer, and only once its own edits are stored."""
        if self.relaunch is None:
            self.menu = None
            self.message = "Refresh is unavailable in this window"
            return
        if self.relaunch.requested:
            # A second confirmation of the same request is not a second viewer.
            self.message = self.menu_message = "Refresh already requested"
            return
        self.remember()
        try:
            self.save()
        except LayoutConflict as exc:
            # Nothing has been torn down; the viewer stays usable and current.
            self.menu = None
            self.message = visible(str(exc))[:100]
            return
        # Re-check: the launcher or the keymap file can change while the
        # confirmation is open, and only a valid one may close this window.
        problem = self.relaunch.check()
        if problem:
            self.refresh_problem = problem
            self.refresh_command = self.relaunch.manual_command()
            self.message = visible(problem)[:100]
            return
        if not self.relaunch.submit(self.refresh_navigation()):
            self.message = self.menu_message = (
                "Refresh already requested"
                if self.relaunch.requested
                else "Could not record the refresh request"
            )
            return
        self.message = "Refreshing viewer…"
        self.running = False

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
        self.put(y, x, text.ljust(width), self.style("active" if active else "normal"), width)
        self.hits.append((y, x, x + width, action))
        if context:
            self.context_hits.append((y, x, x + width, context))

    def shortcut_options(self, *, command: bool) -> list[tuple[str, Callable]]:
        mapping = self.keymap.direct if command else self.keymap.bindings
        options = []
        for action, keys in mapping.items():
            if not keys:
                continue
            text = f"{self.keymap.label(action, command=command)} {ACTION_LABELS[action]}"
            # Custom combinations and aliases may exceed navigation width. Wrap
            # them into scrollable rows instead of hiding the action or a key.
            for line in textwrap.wrap(text, width=max(1, self.screen.getmaxyx()[1] - 2)):
                options.append((line, lambda action=action: self.action(action)))
        return options

    def _options(self, agents: dict) -> list[tuple[str, Callable]]:
        """Rows of the open menu as labels and callbacks. The menu content lives here."""
        command_shortcuts = self.menu == "command-shortcuts" or (
            self.menu == "shortcuts" and self.shortcut_hints == "command"
        )
        if command_shortcuts:
            return [
                *self.shortcut_options(command=True),
                ("Edit shortcuts…", self.open_shortcut_editor),
                ("Refresh viewer…", self.refresh_viewer),
                ("Prefix shortcuts…", lambda: self.open_menu("prefix-shortcuts")),
            ]
        if self.menu in {"shortcuts", "prefix-shortcuts"}:
            return [
                *self.shortcut_options(command=False),
                ("Edit shortcuts…", self.open_shortcut_editor),
                ("Refresh viewer…", self.refresh_viewer),
                ("Terminal profile keys…", lambda: self.open_menu("command-shortcuts")),
            ]
        if self.menu == "refresh":
            if not self.refresh_problem:
                return [("Refresh viewer now", self.confirm_refresh)]
            return []
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
                for space in self.workspace_options()
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
                ("Colors…", self.open_theme),
            ]
        return []

    def workspace_options(self) -> list[dict]:
        """Workspaces the open list offers, in the order _options() draws them."""
        return [
            space
            for space in self.model.state["workspaces"]
            if self.menu != "move" or space != self.model.space
        ]

    def menu_entries(self, agents: dict) -> list[Entry]:
        """The rows of _options(), each given an identity the keyboard can follow.

        Keys outlive a rebuild: a session name, a workspace id, or the position of
        a fixed row. Labels alone are ambiguous, since names repeat. A menu whose
        rows no longer line up with its source falls back to positional keys, so
        added rows stay selectable rather than disappearing.
        """
        options = self._options(agents)
        spaces = self.workspace_options() if self.menu in {"spaces", "move"} else []
        if len(spaces) == len(options) and spaces:
            keys = ["workspace:" + space["id"] for space in spaces]
        elif self.menu == "agents":
            keys = ["session:" + label for label, _ in options]
        else:
            keys = [f"row:{index}:{label}" for index, (label, _) in enumerate(options)]
        rows = zip(keys, options, strict=True)
        return [Entry(key, label, action) for key, (label, action) in rows]

    def menu_rows(
        self, agents: dict | None = None, *, drawn: bool = True
    ) -> tuple[list[Entry], int]:
        """Reconcile the open menu's entries and return the visible rows and their top.

        Both drawing and keyboard activation go through this, so Enter always runs
        the entry shown on the active row of the frame the user is looking at.
        Only a drawn rebuild may adopt a row the user has not chosen yet.
        """
        if agents is None:
            agents = self.source.snapshot()[0]
        start = (
            5
            if self.menu == "agents"
            else 4 + max(0, len(self.notes(self.screen.getmaxyx()[1])) - 1)
        )
        available = max(1, self.screen.getmaxyx()[0] - start - 3)
        rows = self.selection.show(self.menu_entries(agents), available, drawn=drawn)
        return rows, start

    def roomy(self) -> bool:
        """Whether the panel is large enough to draw a menu instead of its warning."""
        height, width = self.screen.getmaxyx()
        return height >= 16 and width >= 18

    def move_selection(self, step: int) -> None:
        self.selection.move(step)

    def activate(self) -> None:
        """Run the active menu row. Nothing is activated by guesswork."""
        if (
            not self.menu
            or self.menu in {"name", "inline-name", "theme", "edit-shortcuts"}
            or not self.roomy()
        ):
            return
        self.menu_rows(drawn=False)
        entry = self.selection.entry()
        if entry:
            entry.action()
        elif self.selection.entries:
            # The chosen entry is gone, or arrived after the frame the user acted
            # on. Either way its neighbour is a different target: ask again.
            self.message = "Entries changed; choose again"

    def command_lines(self) -> list[str]:
        """Read-only reopening text, separate from selectable menu actions."""
        if self.menu != "refresh" or not self.refresh_problem:
            return []
        return textwrap.wrap(
            self.refresh_command or "",
            width=max(1, self.screen.getmaxyx()[1] - 2),
            break_on_hyphens=False,
        )

    def command_rows(self, start: int) -> list[str]:
        lines = self.command_lines()
        available = max(1, self.screen.getmaxyx()[0] - start - 3)
        self.command_offset = min(max(0, self.command_offset), max(0, len(lines) - available))
        return lines[self.command_offset : self.command_offset + available]

    def notes(self, width: int) -> tuple[str, ...]:
        """Menu limits shown above the options; description only, never clickable."""
        if self.menu == "command-shortcuts" or (
            self.menu == "shortcuts" and self.shortcut_hints == "command"
        ):
            return ("Requires terminal profile",)
        if self.menu != "refresh":
            return ()
        manual = bool(self.relaunch and getattr(self.relaunch, "manual_reopen", False))
        if self.relaunch is None:
            lines = ("Refresh is unavailable in this window.",)
        elif self.refresh_problem and not manual:
            lines = (self.refresh_problem, "Nothing was closed.")
        else:
            lines = self.relaunch.notes()
        lines = (*lines, "Esc close" if self.refresh_problem else "Enter refresh · Esc cancel")
        wrapped = []
        for line in lines:
            wrapped += textwrap.wrap(line, width=max(1, width - 2), break_on_hyphens=False)
        # Keep room for the option row and the scroll controls on short screens.
        return tuple(wrapped[: max(1, self.screen.getmaxyx()[0] - 9)])

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
        # Reconcile the open menu before the frame is compared: a skipped repaint
        # must still leave the active row and its options current for the keyboard.
        # A frame too small for the menu paints a warning instead, so it displays
        # no row and must not leave one armed for Enter.
        rows, start = ([], 0)
        if self.menu and self.menu not in {"inline-name", "theme", "edit-shortcuts"}:
            if self.roomy():
                rows, start = self.menu_rows(agents)
            else:
                self.selection.hide()
        command_rows = self.command_rows(start)
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
            # The active row must repaint even when nothing else in the frame moved.
            self.selected,
            [entry.key for entry in rows],
            self.tab_offset,
            self.message,
            self.menu_message,
            command_rows,
            self.display.small,
            repr(self.theme_editor),
            repr(self.shortcut_editor),
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
        if not self.roomy():
            self.put(0, 0, "Enlarge terminal", self.style("normal"))
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
                "theme": (
                    "Viewer colors · preview"
                    if self.theme_editor and self.theme_editor.changed
                    else "Viewer colors"
                ),
                "refresh": "Refresh viewer",
                "shortcuts": (
                    "Terminal profile keys"
                    if self.shortcut_hints == "command"
                    else f"{tmux_key_label(self.keymap.prefix)}, then…"
                ),
                "prefix-shortcuts": f"{tmux_key_label(self.keymap.prefix)}, then…",
                "command-shortcuts": "Terminal profile keys",
                "edit-shortcuts": (
                    "Edit shortcuts · unsaved"
                    if self.shortcut_editor and self.shortcut_editor.changed
                    else "Edit shortcuts"
                ),
            }
            self.put(2, 1, titles[self.menu], self.style("normal") | curses.A_BOLD)
            notes = self.notes(width)
            for index, line in enumerate(notes):
                self.put(3 + index, 1, line, self.style("accent"))
            if self.menu in {"name", "agents"}:
                self.put(3, 1, "> ", self.style("active"))
                style = self.style("active") | (curses.A_REVERSE if self.replace_name else 0)
                self.put(3, 3, self.query[-(width - 5) :], style)
            if self.menu == "name":
                self.button(5, "Save name", self.accept_name)
            elif self.menu == "theme":
                cursor = self.draw_theme(height, width)
            elif self.menu == "edit-shortcuts":
                cursor = self.draw_shortcuts(height, width)
            else:
                for row, line in enumerate(command_rows, start):
                    self.put(row, 1, line, self.style("normal"))
                for row, entry in enumerate(rows, start):
                    self.button(
                        row,
                        entry.label,
                        entry.action,
                        active=self.offset + row - start == self.selected,
                    )
                if not rows and self.menu != "refresh":
                    self.put(
                        start,
                        1,
                        "No matching sessions" if self.menu == "agents" else "No entries",
                        self.style("normal"),
                    )
                self.button(height - 2, "↑", lambda: self.scroll(-1), width=5)
                self.button(height - 2, "↓", lambda: self.scroll(1), x=8, width=5)
                if width >= 24:
                    hint = "read · Esc" if command_rows else "↵ open · Esc"
                    self.put(height - 2, 15, hint, self.style("accent"))
            if self.menu_message:
                self.put(height - 1, 1, self.menu_message, self.style("accent"))
        else:
            workspace_edit = self.inline_editor and self.inline_target[1] is None
            self.name_hits.append((0, 1, width - 7, "workspace:" + self.model.space["id"]))
            header = self.style("normal") | curses.A_BOLD
            self.put(0, 1, self.model.space["name"], header, width - 8)
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
                self.style("accent" if workspace_edit else "muted"),
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
                    self.style("active" if active else "muted"),
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
                self.put(row, 1 if tab_edit else 3, label, self.style("accent"))
                self.hits.append((row, 0, width - 1, action))
                self.context_hits.append((row, 0, width - 1, context))
                row += 1
            if not tabs:
                self.put(2, 1, "No tabs yet", self.style("muted"))
                self.button(3, "Open a terminal +", self.new_tab)
            half = (width - 2) // 2
            self.put(bottom - 1, 1, "─" * (width - 2), self.style("muted"))
            if len(tabs) > available:
                self.button(bottom - 1, "↑ Tabs", lambda: self.scroll(-1), width=half)
                self.button(bottom - 1, "↓ Tabs", lambda: self.scroll(1), x=1 + half)
            self.button(bottom, "Split →", lambda: self.split("right"), width=half)
            self.button(bottom, "Split ↓", lambda: self.split("below"), x=1 + half)
            label = "Layout" if self.model.state["focus"] else "Focus"
            self.button(bottom + 1, label, self.toggle_focus, width=half)
            self.button(bottom + 1, "Next →", self.next_pane, x=1 + half)
            self.button(bottom + 2, "Attach session…", lambda: self.open_menu("agents"))
            self.put(bottom + 2, 1, "Attach session…", self.style("accent"))
            self.button(bottom + 3, "Tab actions…", lambda: self.open_menu("tab"))
            self.button(
                bottom + 4, "Shortcuts", lambda: self.open_menu("shortcuts"), width=width - 8
            )
            self.button(bottom + 4, "Exit", self.quit, x=width - 6, width=5)
            self.put(bottom + 5, 1, "─" * (width - 2), self.style("muted"))
            self.button(bottom + 6, "Workspaces…", lambda: self.open_menu("workspace"), width=half)
            self.button(bottom + 6, "Colors…", self.open_theme, x=1 + half)
            self.workspace_buttons(bottom + 7, width)
            status = (
                error
                or self.message
                or ("Narrow: focus view" if self.display.small and tab else "Layouts saved")
            )
            self.put(bottom + 8, 1, status, self.message_style(status, bool(error or self.message)))
        if cursor:
            with contextlib.suppress(curses.error):
                curses.curs_set(1)
                self.screen.move(*cursor)
        self.screen.refresh()

    def scroll(self, amount: int) -> None:
        self.clear_inline()
        if self.theme_editor:
            self.theme_action(self.theme_editor.move, amount)
        elif self.shortcut_editor:
            self.shortcut_action(self.shortcut_editor.move, amount)
        elif self.command_lines():
            self.command_offset = max(0, self.command_offset + amount)
        elif self.menu:
            self.selection.scroll(amount)
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
        elif self.menu == "theme":
            self.theme_action(self.theme_editor.key, key)
        elif self.menu == "edit-shortcuts":
            # Every key belongs to the editor while it is open, including the
            # ones that would otherwise run an action: capture must be able to
            # bind them without triggering them.
            self.shortcut_action(self.shortcut_editor.key, key)
        elif key == "\x1b":
            self.show()
        elif self.menu:
            self.menu_input(key)
        elif key == "t":
            self.open_theme()
        elif key == curses.KEY_UP:
            self.scroll(-1)
        elif key == curses.KEY_DOWN:
            self.scroll(1)

    def navigation(self, key) -> int | None:
        """Selection movement for a key, or None when it is not a navigation key."""
        # Control aliases stay usable inside the chooser's filter field, where
        # plain letters are text. Home/End move to the first and last entry.
        steps = {
            curses.KEY_UP: -1,
            curses.KEY_DOWN: 1,
            "\x10": -1,
            "\x0e": 1,
            curses.KEY_PPAGE: -max(1, self.screen.getmaxyx()[0] - 9),
            curses.KEY_NPAGE: max(1, self.screen.getmaxyx()[0] - 9),
            curses.KEY_HOME: -len(self.options) or None,
            curses.KEY_END: len(self.options) or None,
        }
        return steps.get(key)

    def menu_input(self, key) -> None:
        """Keyboard handling for an open menu: navigate, activate, then filter."""
        if key == curses.KEY_RESIZE:
            return
        if self.command_lines():
            steps = {
                curses.KEY_UP: -1,
                curses.KEY_DOWN: 1,
                "\x10": -1,
                "\x0e": 1,
                curses.KEY_PPAGE: -max(1, self.screen.getmaxyx()[0] - 9),
                curses.KEY_NPAGE: max(1, self.screen.getmaxyx()[0] - 9),
                curses.KEY_HOME: -len(self.command_lines()),
                curses.KEY_END: len(self.command_lines()),
            }
            if key in steps and self.roomy():
                self.scroll(steps[key])
            return
        step = self.navigation(key)
        if step is not None:
            # Navigation is read before typing so the chooser's filter field can
            # never swallow the keys that select and activate its result. A menu
            # too small to draw shows no row to move between.
            if not self.roomy():
                return
            self.menu_rows(drawn=False)
            self.move_selection(step)
            return
        if key in ("\n", "\r", curses.KEY_ENTER):
            self.accept_name() if self.menu == "name" else self.activate()
            return
        if self.menu not in {"name", "agents"}:
            return
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
        else:
            return
        self.selection.first()

    def apply_pending(self) -> bool:
        """Apply queued gestures until one ends this window, then only release them.

        The first confirmed refresh (or exit) wins: gestures already waiting
        would otherwise change tabs or close shells after the window's final
        save. Their senders are still acknowledged, so no shortcut hangs.
        """
        for action in self.actions.pending():
            if not self.running:
                continue
            with self.display.snapshot_scope():
                self.action(action)
                self.draw()
        return self.running

    def run(self) -> None:
        curses.curs_set(0)
        self.setup_theme()
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
                if not self.apply_pending():
                    break
                now = time.monotonic()
                if now >= next_poll:
                    with self.display.snapshot_scope():
                        next_poll = now + 0.6
                        if (self.inline_editor or self.menu) and self.display.tmux.run(
                            "display-message", "-p", "-t", "viewer:", "#{pane_id}"
                        ) != self.display.sidebar:
                            # The user is typing in a pane they chose. End the
                            # overlay there rather than pulling focus back to a
                            # menu they can no longer see themselves using.
                            self.clear_inline()
                            self.close_menu()
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
                                    self.display.select_sidebar()
                                    self.message = "Tab changed; choose a pane"
                        focused = self.display.focused_leaf()
                        if focused and self.model.tab and focused != self.model.tab["focus"]:
                            self.model.tab["focus"] = focused
                            self.save()
                        size = self.display.size()
                        if size != self.display.last_size:
                            # tmux resized the panes; this is not a user ratio edit.
                            self.display.render(self.model.tab, self.model.state["focus"])
                            self.last_name_click = None
                            # Re-rendering selects a pane. An open editor or menu
                            # keeps the keyboard here; menu keys are not shell input.
                            if self.inline_editor or self.menu:
                                self.display.select_sidebar()
                self.draw()
                key = events.read_or_wait(next_poll)
                if key is not None:
                    with self.display.snapshot_scope():
                        self.input(key)
                    if key == curses.KEY_RESIZE:
                        next_poll = 0.0
            except (RuntimeError, OSError, ValueError) as exc:
                self.message = visible(str(exc))[:100]
                self.last_frame = None
                time.sleep(0.1)
        self.source.close()
