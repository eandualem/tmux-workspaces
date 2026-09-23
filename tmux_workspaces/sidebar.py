"""Workspace navigation and input. Terminal programs stay in tmux attachments."""

from __future__ import annotations

import contextlib
import curses
import importlib.metadata
import locale
import re
import textwrap
import time
from collections.abc import Callable

from .config_popup import ConfigPopup
from .controls import Actions, mouse_action, pane_choice
from .discovery import Snapshot
from .display import Display
from .events import InputEvents
from .keymap import short_key_label
from .menu import LABEL, RULE, Entry, Selection, passive, section
from .messages import failed, failure
from .model import LayoutConflict, Model, is_empty, leaves
from .name_editor import NameEditor, cells
from .persistence import Store
from .source import Source
from .theme import DEFAULT_THEME, ROLES, ThemeError, extra_color, load_theme, theme_path


def visible(text: str) -> str:
    return "".join(char for char in str(text) if char.isprintable())


def package_version() -> str:
    """The installed version, or ``dev`` for a checkout."""
    try:
        return importlib.metadata.version("tmux-workspaces")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def versions() -> str:
    """The footer of the Configure menu: this package and the tmux in use."""
    from .preflight import check_tmux

    try:
        tmux = check_tmux()
    except (RuntimeError, OSError):
        tmux = "tmux"
    version = package_version()
    return f"{'v' + version if version[:1].isdigit() else version} · {tmux}"


MENU_RULE = RULE

# Glyphs a workspace may carry: one cell wide in the monospace fonts terminals
# use, and common to their box-drawing and symbol ranges. A workspace without
# one shows its number.
WORKSPACE_ICONS: tuple[tuple[str, str], ...] = (
    ("◆", "Diamond"),
    ("●", "Circle"),
    ("▲", "Triangle"),
    ("■", "Square"),
    ("★", "Star"),
    ("✦", "Spark"),
    ("◈", "Gem"),
    ("⚑", "Flag"),
    ("⌂", "House"),
    ("✎", "Pencil"),
    ("⚙", "Gear"),
    ("♪", "Note"),
)

# Agent states as Backbone reports them, folded onto a few symbols that differ
# in shape before they differ in color. Anything unlisted is an unknown state.
STATE_SYMBOLS = {
    "busy": "▶",
    "starting": "▶",
    "waiting_for_human": "!",
    "blocked": "!",
    "idle": "○",
}
STATE_NAMES = {
    "busy": "working",
    "starting": "starting",
    "waiting_for_human": "waiting for you",
    "blocked": "blocked",
    "idle": "idle",
    "unknown": "unknown",
}
LEGEND = (
    ("▶", "working"),
    ("!", "needs you: waiting or blocked"),
    ("○", "idle"),
    ("?", "state unknown"),
)
# One-cell substitutes keep controls and outlines aligned in limited encodings.
ASCII_GLYPHS = {
    "▶": ">",
    "▮": "|",
    "●": "*",
    "›": ">",
    "○": "o",
    "‹": "<",
    "▾": "v",
    "↑": "^",
    "↓": "v",
    "←": "<",
    "→": ">",
    "↵": ">",
    "…": "~",
    "⋯": "~",
    "·": ".",
    "╭": "+",
    "╮": "+",
    "╰": "+",
    "╯": "+",
    "─": "-",
    "│": "|",
}


def terminal_text(text: str, encoding: str) -> str:
    """Keep representable text; replace other characters without shifting cells."""
    text = visible(text)
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        result = []
        for char in text:
            try:
                char.encode(encoding)
                result.append(char)
            except UnicodeEncodeError:
                result.append(ASCII_GLYPHS.get(char, "?" * cells(char)))
        return "".join(result)


# The roster is bounded so the tab list keeps its room: at most this many
# agent rows, and never fewer tab rows than this.
MAX_ROSTER_ROWS = 6
MIN_TAB_ROWS = 4
# Sentinel: no roster has been read for the frame in progress.
_UNREAD = object()


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
        emit: Callable[[str], None] | None = None,
    ):
        self.screen, self.model, self.store = screen, model, store
        # Sends raw text to this pane's terminal: the palette definitions an
        # RGB color needs. Absent in tests, so nothing recolors a test runner.
        self.emit = emit
        self.source, self.display = source, display
        self.actions = actions
        self.shortcut_hints = shortcut_hints
        # Colors are read once in run(); construction touches no user file.
        self.theme_path, self.terminal_colors = theme_path, terminal_colors
        self.colors, self.theme, self.palette = 8, None, None
        # The file a shortcut edit would write. Absent means the viewer
        # inherited a map rather than reading one, so Save has no implicit
        # destination and the editor says so instead of guessing a path.
        self.keymap_path = keymap_path
        self.config_popup = None

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
        self.roster_offset = 0
        # Consecutive polls that found the keyboard focus away from the panel
        # while a menu or editor was open.
        self.away_polls = 0
        # The interior rows the roster's agent rows occupy, for the wheel.
        self.roster_span: tuple[int, int] | None = None
        # The roster reading the frame being drawn works from; unread between frames.
        self._frame_roster: Snapshot | object | None = _UNREAD
        encoding = getattr(screen, "encoding", None)
        self.encoding = (
            encoding
            if isinstance(encoding, str) and encoding
            else locale.getpreferredencoding(False) or "ascii"
        )
        # What the open menu draws beside each row, by row position.
        self._meta: dict[int, dict[str, str]] = {}
        # The theme's colors as tmux spells them, for the status row.
        self.status_colors: dict[str, str] = {}
        self._versions: str | None = None
        self.message = ""
        self.running = True
        self.last_frame = None
        self.body = screen

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

    def style(self, role: str, on: str | None = None) -> int:
        """Attributes for a semantic role, optionally on another role's ground.
        Pairs are installed, never per frame."""
        return self.palette.style(role, on) if self.palette else 0

    def message_style(self, message: str, notice: bool) -> int:
        """Failures add bold, so severity survives a reduced or reversed palette."""
        return self.style("accent" if notice else "muted") | (
            curses.A_BOLD if failed(message) else 0
        )

    def install(self, theme) -> None:
        """Show a theme in place: four pair updates, no reopen and no redraw loop."""
        palette = theme.resolve(self.colors)
        palette.install(curses, self.emit, previous_rgb=self.palette.rgb if self.palette else ())
        self.theme, self.palette, self.last_frame = theme, palette, None
        self.display.chooser_theme_state = theme.to_toml()
        self.status_colors = {role: theme.tmux_role(role, self.colors)[0] for role in ROLES}
        self.status_colors["ok"] = extra_color("ok", self.colors)
        # The grounds are tmux's to paint: the sidebar's panel, the terminals'
        # surface, the separators between them and the status row. A display
        # that cannot be reached keeps its grounds; the sidebar's own colors
        # still apply.
        with contextlib.suppress(RuntimeError, OSError, ValueError):
            self.display.style_panel(
                theme.panel, theme.surface, theme.separator(), self.status_colors
            )
        # The role names the sidebar's own base, so its empty cells and the
        # cleared frame carry the configured background rather than the
        # terminal's, which is only visible once someone configures one.
        with contextlib.suppress(curses.error):
            self.screen.bkgdset(" ", palette.style("normal"))
            # Pair redefinition invalidates curses' physical color state.
            # A normal erase/redraw can leave the first run in default colors.
            self.screen.clearok(True)

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
            self.display.shells.remember_many(
                [pane for pane in leaves(tab["tree"]) if not is_empty(pane)]
            )

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
            # Kept so a late report of the same gesture does not place the caret.
            self.last_name_click = (key, now, x, y)
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

    def open_config_editor(self, kind: str) -> None:
        if self.config_popup:
            return
        path = self.keymap_path if kind == "shortcuts" else theme_path(self.theme_path)
        if path is None:
            self.menu_message = "No keymap file; reopen without --no-keymap to edit shortcuts."
            return
        self.open_menu("json-settings")
        look = {
            "theme_state": self.theme.to_toml() if self.theme else None,
            "colors": self.colors,
        }
        try:
            self.config_popup = (
                ConfigPopup(self.display, kind, None, keymap=self.keymap, **look)
                if kind == "reference"
                else ConfigPopup(self.display, kind, path, **look)
            )
        except (OSError, RuntimeError) as error:
            self.close_menu()
            self.display.render(self.model.tab, self.model.state["focus"])
            self.message = "Could not open settings editor: " + visible(str(error))

    def finish_config_editor(self) -> bool:
        result = self.config_popup.poll()
        if result is None:
            return False
        # Discard viewer actions queued while the editor owned input before
        # allowing any command to act on panes again.
        for _action in self.actions.pending():
            pass
        kind = self.config_popup.kind
        self.config_popup.close()
        self.config_popup = None
        self.close_menu()
        self.display.render(self.model.tab, self.model.state["focus"])
        if result.get("error"):
            self.message = visible(result["error"])
        elif result.get("saved"):
            if kind == "colors":
                loaded = load_theme(self.theme_path, working=self.theme)
                if loaded.diagnostic:
                    self.message = visible(loaded.diagnostic)
                else:
                    self.install(loaded.theme)
                    self.display.render(self.model.tab, self.model.state["focus"])
                    self.message = "Colors saved and applied"
            else:
                self.refresh_viewer()
                self.menu_message = "Shortcuts saved"
        return True

    def draw_field(self, editor, row: int, x: int, width: int) -> tuple[int, int]:
        text, column = editor.viewport(width)
        style = self.style("active") | (curses.A_REVERSE if editor.selected else curses.A_UNDERLINE)
        self.put(row, x, text + " " * (width - cells(text)), style, width)
        return row, x + column

    def close_menu(self) -> None:
        """Drop an open menu without moving the keyboard away from its pane."""
        self.menu, self.query = None, ""
        self.selection.reset()
        self.menu_message = ""
        self.replace_name = False
        self.attach_target = None

    def show(self) -> None:
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
        self.clear_inline()
        self.remember()
        # The workspace list and the workspace options are one menu.
        name = "workspace" if name == "spaces" else name
        self.menu, self.pending, self.query = name, pending, ""
        self.selection.reset()
        self.menu_message = ""
        self.replace_name = False
        tab, pane = self.model.tab, self.model.pane
        # Tab options own Return to shell, so they bind the same target as
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
        # The pane opens as a chooser: a shell, or a session, decided there.
        self.model.add_tab(empty=True)
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

    def fill_pane(self, tab_id: str, leaf_id: str, session: str | None) -> None:
        """A chooser pane picked what it runs: an ordinary shell, or a session.

        The pane named the destination it was started for. Anything that moved
        since -- another window replacing the pane, the tab changing, the pane
        already filled -- is refused, and the display is redrawn so a stale
        chooser is replaced by whatever the pane holds now.
        """
        self.remember()
        try:
            self.store.refresh(self.model)
        except LayoutConflict:
            self.message = "Pane changed; choose again"
            self.show()
            return
        tab = self.model.tab
        pane = (
            next((p for p in leaves(tab["tree"]) if p["id"] == leaf_id), None)
            if tab and tab["id"] == tab_id
            else None
        )
        if not pane or not is_empty(pane) or leaf_id not in self.display.panes:
            self.message = "Pane changed; choose again"
            self.show()
            return
        tab["focus"] = leaf_id
        if session is None:
            self.model.open_terminal()
        else:
            self.model.attach(
                session, self.source.socket if self.source.persistent_socket else None
            )
        self.show()

    def split(self, direction: str) -> None:
        self.remember()
        # The new pane opens as a chooser, like a new tab. The directory is
        # kept so a terminal chosen there starts where its neighbour is.
        cwd = self.model.pane.get("cwd") if self.model.pane else None
        self.model.split(direction, cwd, empty=True)
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
        if self.config_popup:
            return
        if name == "copy-selection":
            self.display.copy_selection()
            return
        mouse = mouse_action(name)
        if mouse:
            x, y = mouse
            height, width = self.screen.getmaxyx()
            if x < width and y < height:
                self.mouse(x - self.INSET, y - self.INSET, curses.BUTTON1_PRESSED)
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
        if name.startswith("attach-pane:"):
            _, tab_id, leaf_id = name.split(":")
            self.attach_pane(tab_id, leaf_id)
            return
        choice = pane_choice(name)
        if choice:
            self.fill_pane(*choice[1:])
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
            "show-agents": self.toggle_agents,
        }
        actions[name]()

    def refresh_viewer(self) -> None:
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

    def menu_next_pane(self, offset: int = 1) -> None:
        """Cycle panes from the menu, closing it so typing follows the pane."""
        self.next_pane(offset)
        if self.menu:
            self.show()

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

    # The panel fills its pane edge to edge: no inset, no outline of its own.
    # tmux draws the one-column line between it and the content. Everything is
    # drawn straight into the pane in its own coordinates.
    INSET = 0
    # Rows the bottom of the panel keeps: a blank row, Configure…, the
    # workspace slots. Messages go to the status row under the panes.
    FOOTER_ROWS = 3

    def size(self) -> tuple[int, int]:
        """Use every cell of the panel; tmux owns the separator outside it."""
        height, width = self.screen.getmaxyx()
        return max(0, height), max(0, width)

    def interior(self):
        """The window the panel draws into: the pane itself."""
        return self.screen

    def put(self, y: int, x: int, text: str, style: int = 0, width: int | None = None) -> None:
        height, columns = self.size()
        if 0 <= y < height and 0 <= x < columns:
            with contextlib.suppress(curses.error):
                self.body.addnstr(
                    y,
                    x,
                    terminal_text(text, self.encoding),
                    min(width or columns, columns - x),
                    style,
                )

    def fill(self, y: int, style: int) -> None:
        """Paint one whole row in a style, as the ground of what goes on it."""
        self.put(y, 0, " " * self.size()[1], style)

    def put_right(self, y: int, text: str, style: int, *, margin: int = 1) -> int:
        """Text ending one cell before the right edge; returns its column."""
        x = max(0, self.size()[1] - margin - cells(text))
        self.put(y, x, text, style)
        return x

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
        style: int | None = None,
    ) -> None:
        columns = self.size()[1]
        width = width or columns - x
        if style is None:
            style = self.style("active" if active else "normal")
        self.put(y, x, text.ljust(width), style, width)
        self.hits.append((y, x, x + width, action))
        if context:
            self.context_hits.append((y, x, x + width, context))

    def row(
        self,
        label: str,
        action: Callable,
        *,
        key: str | None = None,
        kind: str | None = None,
        right: str | None = None,
        identity: str | None = None,
        icon: str | None = None,
        status: str | None = None,
    ) -> tuple[str, Callable]:
        """One menu row and what is drawn beside it.

        ``key`` names the viewer action whose effective prefix key ends the
        row; ``right`` is other text there, such as a toggle's value or why a
        row is disabled; ``kind`` is ``danger``, ``disabled``, ``toggle`` or
        ``workspace``; ``identity`` is a stable key for the selection.
        """
        meta: dict[str, str] = {}
        if key:
            meta["action"] = key
        if kind:
            meta["kind"] = kind
        if right:
            meta["right"] = right
        if identity:
            meta["key"] = identity
        if icon:
            meta["icon"] = icon
        if status:
            meta["status"] = status
        self._meta[len(self._meta)] = meta
        return label, action

    def _options(self, agents: dict) -> list[tuple[str, Callable]]:
        """Rows of the open menu as labels and callbacks. The menu content lives here."""
        self._meta = {}
        row = self.row
        if self.menu == "refresh":
            if not self.refresh_problem:
                return [row("Refresh viewer now", self.confirm_refresh)]
            return []
        if self.menu == "agents":
            return [
                row(name, lambda name=name: self.attach(name), identity="session:" + name)
                for name in sorted(agents)
                if self.query.casefold() in name.casefold()
            ]
        if self.menu == "move":
            return [
                row(
                    space["name"],
                    lambda space=space: self.transfer_tab(space),
                    kind="workspace",
                    right=self.tab_count(space),
                    identity="workspace:" + space["id"],
                    icon=self.workspace_icon(space, index),
                )
                for index, space in enumerate(self.model.state["workspaces"])
                if space != self.model.space
            ]
        if self.menu == "tab":
            # Pane actions live here rather than as sidebar buttons: the panel
            # keeps one plain list, and a split opens as a chooser anyway.
            focused = self.model.state["focus"]
            return [
                row("Rename tab", lambda: self.rename("rename-tab"), key="rename-tab"),
                row("Split right", lambda: self.split("right"), key="split-right"),
                row("Split below", lambda: self.split("below"), key="split-below"),
                row("Attach session", lambda: self.open_menu("agents"), key="attach"),
                row("Return to shell", lambda: self.attach(None)),
                row(
                    "Restore layout" if focused else "Focus one pane",
                    self.toggle_focus,
                    key="focus",
                ),
                row(MENU_RULE, lambda: None),
                row("Move up", lambda: self.move_tab(-1)),
                row("Move down", lambda: self.move_tab(1)),
                row("Move to workspace…", lambda: self.open_menu("move")),
                row(MENU_RULE, lambda: None),
                row("Close pane", self.close_pane, key="close-pane"),
                row("Close tab", self.close_tab, key="close-tab", kind="danger"),
            ]
        if self.menu == "workspace":
            # The chooser and the options in one menu: every workspace to
            # switch to, then what can be done with the current one.
            spaces = self.model.state["workspaces"]
            rows = [
                row(
                    space["name"],
                    lambda space=space: self.choose_workspace(space),
                    kind="workspace",
                    right=self.tab_count(space),
                    identity="workspace:" + space["id"],
                    icon=self.workspace_icon(space, index),
                    status=f"Switch to {visible(space['name'])}",
                )
                for index, space in enumerate(spaces)
            ]
            current = self.model.space
            if current["tabs"]:
                blocked = "has tabs"
            elif len(spaces) == 1:
                blocked = "last one"
            else:
                blocked = None
            return [
                *rows,
                row(section("This workspace"), lambda: None),
                row("Rename", lambda: self.rename("rename-workspace"), key="rename-workspace"),
                row("Set icon…", lambda: self.open_menu("icon")),
                row(
                    "Delete",
                    self.delete_workspace,
                    kind="disabled" if blocked else "danger",
                    right=blocked,
                ),
                row(MENU_RULE, lambda: None),
                row(
                    "New workspace",
                    lambda: self.rename("new-workspace"),
                    key="new-workspace",
                    icon="+",
                ),
            ]
        if self.menu == "icon":
            return [
                *(
                    row(f"{glyph}  {label}", lambda glyph=glyph: self.set_icon(glyph))
                    for glyph, label in WORKSPACE_ICONS
                ),
                # No glyph: its name lines up with the names above.
                row("   Number", lambda: self.set_icon(None)),
            ]
        if self.menu == "configure":
            # Everything infrequent in one place; leaving last, after a rule,
            # and named for what it does: shells and attached sessions keep running.
            rows = [
                row("Edit theme…", lambda: self.open_config_editor("colors")),
                row("Edit shortcuts…", lambda: self.open_config_editor("shortcuts")),
                row("View shortcuts…", lambda: self.open_config_editor("reference")),
                row("Refresh viewer…", self.refresh_viewer, key="refresh-viewer"),
            ]
            if self.roster(agents) is not None:
                rows += [
                    row(MENU_RULE, lambda: None),
                    row(
                        "Show agents",
                        self.toggle_agents,
                        key="show-agents",
                        kind="toggle",
                        right="on" if self.show_agents else "off",
                    ),
                    row("Agent status…", lambda: self.open_menu("status")),
                ]
            return [*rows, row(MENU_RULE, lambda: None), row("Detach", self.quit, key="quit")]
        if self.menu == "status":
            return self.status_options()
        return []

    def status_options(self) -> list[tuple[str, Callable]]:
        """The roster in full: every active agent with its state spelled out,
        how many are offline, and the legend for the symbols."""

        def nothing() -> None:
            return None

        roster = self.roster()
        if roster is None:
            rows = [self.row("No agent source connected", nothing)]
        elif roster.stale:
            rows = [self.row("Roster unavailable", nothing)]
            if roster.error:
                rows += [self.row(line, nothing) for line in self.wrap(roster.error)]
        else:
            entries = self.roster_entries(roster)
            rows = [
                self.row(f"{self.symbol(state)} {name} · {self.state_name(state)}", nothing)
                for name, state in entries
            ] or [self.row("No active agents", nothing)]
            offline = len(roster.sessions) - len(entries)
            if offline:
                rows.append(self.row(f"{offline} offline", nothing))
        legend = [self.row(f"{self.glyph(symbol)} {text}", nothing) for symbol, text in LEGEND]
        return [*rows, self.row(MENU_RULE, nothing), *legend]

    @staticmethod
    def tab_count(space: dict) -> str:
        count = len(space["tabs"])
        return "empty" if not count else "1 tab" if count == 1 else f"{count} tabs"

    def wrap(self, text: str) -> list[str]:
        return textwrap.wrap(text, width=max(1, self.size()[1] - 2), break_on_hyphens=False)

    def set_icon(self, glyph: str | None) -> None:
        """Give the current workspace an icon, or none, and show the row again."""
        if glyph is None:
            self.model.space.pop("icon", None)
        else:
            self.model.space["icon"] = glyph
        self.show()

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
        entries = []
        for index, (label, action) in enumerate(options):
            meta = self._meta.get(index, {})
            if "key" in meta:
                key = meta["key"]
            elif self.menu in {"configure", "status"}:
                key = f"row:{index}"
            else:
                key = f"row:{index}:{label}"
            entries.append(Entry(key, label, action, meta))
        return entries

    def menu_start(self) -> int:
        """The first row of the open menu's options."""
        if self.menu in {"agents", "name"}:
            return 5
        return 3 + len(self.notes(self.size()[1]))

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
        start = self.menu_start()
        available = max(1, self.size()[0] - start - self.FOOTER_ROWS)
        rows = self.selection.show(self.menu_entries(agents), available, drawn=drawn)
        return rows, start

    def roomy(self) -> bool:
        """Whether the panel is large enough to draw a menu instead of its warning.

        Fourteen rows and sixteen columns, the floor the panel has always had.
        """
        height, width = self.size()
        return height >= 14 and width >= 16

    def move_selection(self, step: int) -> None:
        self.selection.move(step)

    def activate(self) -> None:
        """Run the active menu row. Nothing is activated by guesswork."""
        if not self.menu or self.menu in {"name", "inline-name"} or not self.roomy():
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
            width=max(1, self.size()[1] - 2),
            break_on_hyphens=False,
        )

    def key_label(self, action: str) -> str:
        """The effective prefix key of an action, as a menu row's right end shows it."""
        keys = self.keymap.label(action)
        return keys.split(" / ")[0] if keys else ""

    def action_keys(self, action: str) -> str:
        """Every way to reach an action, for the status row: the prefix key
        and, when a terminal profile supplies one, the direct key."""
        parts = []
        prefix = self.keymap.label(action)
        if prefix:
            parts.append(f"{short_key_label(self.keymap.prefix)} {prefix}")
        direct = self.keymap.label(action, command=True)
        if direct:
            parts.append(f"{direct} in Ghostty")
        return " · ".join(parts)

    def command_rows(self, start: int) -> list[str]:
        lines = self.command_lines()
        available = max(1, self.size()[0] - start - self.FOOTER_ROWS)
        self.command_offset = min(max(0, self.command_offset), max(0, len(lines) - available))
        return lines[self.command_offset : self.command_offset + available]

    def notes(self, width: int) -> tuple[str, ...]:
        """Menu limits shown above the options; description only, never clickable."""
        if self.menu != "refresh":
            return ()
        manual = bool(self.relaunch and getattr(self.relaunch, "manual_reopen", False))
        if self.relaunch is None:
            lines = ("Refresh is unavailable in this window.",)
        elif self.refresh_problem and not manual:
            lines = (self.refresh_problem, "Nothing was closed.")
        else:
            lines = self.relaunch.notes()
        wrapped = []
        for line in lines:
            wrapped += textwrap.wrap(line, width=max(1, width - 2), break_on_hyphens=False)
        # Keep room for the option row and the footer on short screens.
        return tuple(wrapped[: max(1, self.size()[0] - 8)])

    def status_text(self) -> str:
        """What the status row's right slot says instead of the light, or nothing.

        A notice about the open menu outranks an older general message, since
        it is about what the user is looking at.
        """
        error = self.source.snapshot()[1] if self.source else ""
        if self.menu and self.menu_message:
            return self.menu_message
        if error or self.message:
            return error or self.message
        if self.menu_message:
            return self.menu_message
        if self.display.small and self.model.tab:
            return "Narrow: focus view"
        return ""

    def footer_rows(self) -> int:
        return self.FOOTER_ROWS

    # -- the status row ------------------------------------------------------

    MENU_TITLES = {
        "agents": "Attach session",
        "move": "Move to workspace",
        "tab": "Tab",
        "workspace": "Workspaces",
        "icon": "Workspace icon",
        "configure": "Configure",
        "json-settings": "Popup open",
        "status": "Agent status",
        "name": "Type a name",
        "refresh": "Refresh viewer",
    }

    def menu_title(self) -> str:
        if self.menu == "name":
            return {
                "new-workspace": "New workspace",
                "rename-tab": "Rename tab",
                "rename-workspace": "Rename workspace",
            }.get(self.pending, "Type a name")
        return self.MENU_TITLES.get(self.menu or "", "")

    @staticmethod
    def _escape(text: str) -> str:
        """Text for a tmux format: nothing in it may start a directive."""
        return visible(text).replace("#", "##")

    def _tmux_fg(self, name: str) -> str:
        return self.status_colors.get(name, "default")

    def status_slots(self, rows: list[Entry] | None = None) -> tuple[str, str, str]:
        """The three slots of the status row as tmux format text: the context
        on the left, the hints for the current mode in the centre, the
        agents toggle and the saved-state light on the right."""
        text, muted, accent = (self._tmux_fg(name) for name in ("normal", "muted", "accent"))
        prefix = short_key_label(self.keymap.prefix)
        esc = self._escape

        def named(label: str, detail: str = "") -> str:
            left = f"#[fg={text}]{esc(label)}#[fg={muted}]"
            return left + (f" · {esc(detail)}" if detail else "")

        def hint(action: str, word: str) -> str:
            keys = self.keymap.label(action)
            return f"{prefix} {keys.split(' / ')[0]} {word}" if keys else ""

        popup = self.config_popup
        if popup is not None:
            kind = getattr(popup, "kind", "")
            if kind == "reference":
                left = named("Shortcuts", "active in this viewer")
                centre = "esc close"
            else:
                left = named(
                    "Edit theme" if kind == "colors" else "Edit shortcuts",
                    "draft, not applied until Save",
                )
                centre = "F2 save · F10 cancel · F1 help"
        elif self.inline_editor:
            left = named("Rename", "type a new name")
            centre = "⏎ save · esc cancel"
        elif self.menu and self.menu != "inline-name":
            entry = self.selection.entry() if rows else None
            if entry is not None and not passive(entry.label):
                action = entry.meta.get("action")
                label = entry.meta.get("status") or entry.label
                keys = self.action_keys(action) if action else ""
                left = named(label, keys) if keys else named(label)
            else:
                left = named(self.menu_title())
            if self.menu == "agents":
                centre = "type to filter · ↑↓ move · ⏎ open · esc back"
            elif self.menu == "name":
                centre = "⏎ save · esc cancel"
            elif self.menu == "refresh":
                centre = "esc close" if self.refresh_problem else "⏎ refresh · esc cancel"
            else:
                centre = "↑↓ move · ⏎ open · esc back"
        else:
            tab = self.model.tab
            if tab:
                members = leaves(tab["tree"])
                index = next((i for i, pane in enumerate(members) if pane["id"] == tab["focus"]), 0)
                left = named(
                    self.model.space["name"],
                    f"{visible(tab['name'])} · pane {index + 1}/{len(members)}",
                )
                hints = [
                    hint(a, w)
                    for a, w in (
                        ("new-tab", "new"),
                        ("split-right", "split"),
                        ("attach", "attach"),
                        ("focus", "focus"),
                    )
                ]
            else:
                left = named(self.model.space["name"], "no tabs")
                hints = [
                    hint(a, w)
                    for a, w in (
                        ("new-tab", "new"),
                        ("workspaces", "workspaces"),
                        ("attach", "attach"),
                    )
                ]
            centre = " · ".join(h for h in hints if h)
        right = ""
        if self.roster() is not None:
            shown = self.show_agents
            keys = self.keymap.label("show-agents").split(" / ")[0]
            right = (
                "#[range=user|agents]"
                + (f"#[fg={text}]agents shown" if shown else f"#[fg={muted}]agents hidden")
                + (f" #[fg={accent}]{prefix} {keys}" if keys else "")
                + "#[norange]  "
            )
        status = self.status_text()
        if status:
            bold = ",bold" if failed(status) else ""
            right += f"#[fg={accent}{bold}]{esc(status)}"
        else:
            right += f"#[fg={self._tmux_fg('ok')}]●"
        return left, centre, right

    def status_line(self, rows: list[Entry] | None = None) -> str:
        left, centre, right = self.status_slots(rows)
        # The centre is laid out on the whole row: it needs the wider side's
        # room on both sides of it, plus the edge insets and a two-cell gap.
        # Where it would run into the sides, the hints give way.
        columns = self.display.last_size[0]
        left_cells, centre_cells, right_cells = (
            cells(re.sub(r"#\[[^\]]*\]", "", slot).replace("##", "#"))
            for slot in (left, centre, right)
        )
        if columns and 2 * max(left_cells, right_cells) + centre_cells + 6 > columns:
            centre = ""
        muted = self._tmux_fg("muted")
        return (
            f"#[fg={muted}]#[align=left] {left}"
            f"#[fg={muted}]#[align=centre]{centre}"
            f"#[fg={muted}]#[align=right]{right} "
        )

    # -- the agent roster --------------------------------------------------

    def roster(self, agents: dict | None = None) -> Snapshot | None:
        """The agents and states the source reports, or None without a
        provider that reports states (plain tmux discovery knows names only).

        A frame reads the source once: ``draw`` stores that reading, and every
        sizing or menu step of the same frame reuses it, so a poll landing
        mid-frame cannot clip a row. Callers outside a frame read afresh.
        """
        if self._frame_roster is not _UNREAD:
            return self._frame_roster
        reader = getattr(self.source, "roster", None)
        snapshot = reader() if callable(reader) else None
        return snapshot if isinstance(snapshot, Snapshot) else None

    @property
    def show_agents(self) -> bool:
        """The saved preference; the roster is shown unless it was turned off."""
        return self.model.state.get("show_agents", True) is not False

    def toggle_agents(self) -> None:
        self.model.state["show_agents"] = not self.show_agents
        self.roster_offset = 0
        self.save()

    @staticmethod
    def roster_entries(roster: Snapshot) -> list[tuple[str, str]]:
        """Active agents as (name, state): everything but offline. Those that
        need you come first, then those working, so a roster taller than its
        rows keeps them in view; names order each group. Every row opens the
        same status menu, so a row that moves never misdirects a click."""
        entries = []
        for name, item in roster.sessions.items():
            state = item.get("state") if isinstance(item, dict) else None
            state = state if isinstance(state, str) and state else "unknown"
            if state != "offline":
                entries.append((name, state))
        attention = {"!": 0, "▶": 1}
        return sorted(
            entries,
            key=lambda entry: (
                attention.get(STATE_SYMBOLS.get(entry[1], "?"), 2),
                entry[0].casefold(),
                entry[0],
            ),
        )

    def glyph(self, char: str) -> str:
        return terminal_text(char, self.encoding)

    def symbol(self, state: str) -> str:
        return self.glyph(STATE_SYMBOLS.get(state, "?"))

    def symbol_style(self, symbol: str, *, on: str | None = None) -> int:
        """Working is the accent, needs-you the danger color, the rest muted."""
        if symbol == self.glyph("▶"):
            return self.style("accent", on)
        if symbol == "!":
            return self.style("danger", on) | curses.A_BOLD
        return self.style("muted", on)

    @staticmethod
    def state_name(state: str) -> str:
        return STATE_NAMES.get(state, state.replace("_", " "))

    def roster_rows(self) -> int:
        """Rows the roster takes, its label included; zero when it is hidden,
        absent, or the window is too short to keep the tab list usable."""
        roster = self.roster()
        if roster is None or not self.show_agents:
            return 0
        room = self.size()[0] - 3 - self.FOOTER_ROWS - MIN_TAB_ROWS
        if room < 2:
            return 0
        wanted = 1 + max(1, min(MAX_ROSTER_ROWS, len(self.roster_entries(roster))))
        return min(wanted, room)

    def draw_roster(self, top: int, rows: int, width: int, roster: Snapshot) -> None:
        """The section: its label and count, then one row per active agent — a
        symbol in a fixed slot and the name — or one quiet row saying why not."""
        muted = self.style("muted")
        self.put(top, 1, "AGENTS", muted)
        visible_rows = rows - 1
        self.roster_span = (top + 1, top + rows)
        if roster.stale:
            self.put(top + 1, 1, "Roster unavailable", muted, width - 2)
            return
        entries = self.roster_entries(roster)
        if not entries:
            self.put(top + 1, 1, "No active agents", muted, width - 2)
            return
        self.roster_offset = min(self.roster_offset, max(0, len(entries) - visible_rows))
        count = str(len(entries))
        if len(entries) > visible_rows:
            self.put(top, width - 6 - len(count), count, muted)
            self.button(top, "↑", lambda: self.scroll_roster(-1), x=width - 5, width=2, style=muted)
            self.button(top, "↓", lambda: self.scroll_roster(1), x=width - 3, width=2, style=muted)
        else:
            self.put_right(top, count, muted)
        shown = entries[self.roster_offset : self.roster_offset + visible_rows]
        for index, (name, state) in enumerate(shown):
            row = top + 1 + index
            symbol = self.symbol(state)
            self.put(row, 1, symbol, self.symbol_style(symbol), 1)
            self.put(row, 3, visible(name), self.style("normal"), width - 4)
            self.hits.append((row, 0, width, lambda: self.open_menu("status")))

    def scroll_roster(self, amount: int) -> None:
        self.roster_offset = max(0, self.roster_offset + amount)

    def workspace_icon(self, space: dict, index: int) -> str:
        """The workspace's glyph, or its number when none is set."""
        icon = space.get("icon")
        return icon if isinstance(icon, str) and icon.isprintable() and icon else str(index + 1)

    def icon_row(self, row: int, width: int) -> None:
        """One-click workspace switching: fixed three-cell slots, the current
        one filled with its glyph in the accent; when the row is full, the
        last slot opens the full list."""
        spaces = self.model.state["workspaces"]
        slots = max(1, (width - 2) // 4)
        overflow = len(spaces) > slots
        shown = spaces[: slots - 1] if overflow else spaces
        for index, space in enumerate(shown):
            active = space["id"] == self.model.space["id"]
            label = self.workspace_icon(space, index)
            if len(label) > 2:
                label = label[:2]
            self.button(
                row,
                f"{label:^3}",
                lambda space=space: self.choose_workspace(space),
                x=1 + index * 4,
                width=3,
                active=active,
                context=lambda space=space: self.context_workspace(space),
                style=self.style("accent", "active") if active else self.style("muted"),
            )
        if overflow:
            self.button(
                row,
                " … ",
                lambda: self.open_menu("workspace"),
                x=1 + len(shown) * 4,
                width=3,
                style=self.style("muted"),
            )

    def tab_capacity(self) -> int:
        """One row per tab: the heading, a blank row and the section label sit
        above, the roster and the footer below."""
        return max(1, self.size()[0] - 3 - self.FOOTER_ROWS - self.roster_rows())

    def draw(self) -> None:
        self._frame_roster = _UNREAD
        try:
            self._draw()
        finally:
            self._frame_roster = _UNREAD

    def _draw(self) -> None:
        agents, error = self.source.snapshot()
        roster = self._frame_roster = self.roster()
        height, width = self.size()
        # Reconcile the open menu before the frame is compared: a skipped repaint
        # must still leave the active row and its options current for the keyboard.
        # A frame too small for the menu paints a warning instead, so it displays
        # no row and must not leave one armed for Enter.
        rows, start = ([], 0)
        if self.menu and self.menu not in {"inline-name"}:
            if self.roomy():
                rows, start = self.menu_rows(agents)
            else:
                self.selection.hide()
        command_rows = self.command_rows(start)
        status = self.status_line(rows)
        # Skip identical frames: idle sidebar does not repaint the terminal.
        frame = (
            repr(self.model.state),
            repr(agents),
            # The roster's observation time changes with every poll; only what
            # it says matters to the frame.
            (repr(roster.sessions), roster.error, roster.stale) if roster else None,
            self.roster_offset,
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
            status,
        )
        if frame == self.last_frame:
            return
        self.last_frame = frame
        self.display.set_status(status)
        self.screen.erase()
        self.body = self.interior()
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        self.name_hits.clear()
        cursor = None
        self.hits.clear()
        self.context_hits.clear()
        self.roster_span = None
        if not self.roomy():
            self.put(0, 0, "Enlarge terminal", self.style("normal"))
            self.button(2, "Detach", self.quit)
            self.screen.refresh()
            return
        if self.menu and self.menu != "inline-name":
            cursor = self.draw_menu(rows, start, command_rows, width, height)
        else:
            cursor = self.draw_home(roster, width, height)
        # The bottom, from the last row up: the workspace slots, Configure…,
        # and a blank row. The Configure menu names the versions there instead.
        bottom = height - self.FOOTER_ROWS
        if self.menu == "configure":
            if self._versions is None:
                self._versions = versions()
            self.put(bottom + 1, 1, self._versions, self.style("muted"))
        else:
            self.button(
                bottom + 1,
                "Configure…",
                lambda: self.open_menu("configure"),
                style=self.style("muted"),
            )
        self.icon_row(bottom + 2, width)
        if cursor:
            with contextlib.suppress(curses.error):
                curses.curs_set(1)
                self.body.move(*cursor)
                self.body.cursyncup()
        self.screen.refresh()

    def draw_menu(self, rows, start, command_rows, width, height):
        """A menu in place of the tab list: Back on the header bar, the
        context label, then the rows with their keys at the right end."""
        cursor = None
        muted = self.style("muted")
        self.fill(0, self.style("header"))
        self.button(0, "‹ Back", self.show, width=7, style=self.style("accent", "header"))
        self.put_right(0, "esc", self.style("muted", "header"))
        title = self.menu_title()
        if self.menu == "tab" and self.model.tab:
            tabs = self.model.space["tabs"]
            number = tabs.index(self.model.tab) + 1
            self.put(2, 1, "TAB · ", muted)
            self.put(
                2, 7, f"{number} {visible(self.model.tab['name'])}", self.style("normal"), width - 8
            )
        else:
            self.put(2, 1, title.upper(), muted)
        notes = self.notes(width)
        for index, line in enumerate(notes):
            self.put(3 + index, 1, line, self.style("accent"))
        if self.menu in {"name", "agents"}:
            self.put(3, 1, "›", self.style("accent"))
            style = self.style("normal") | (curses.A_REVERSE if self.replace_name else 0)
            shown = self.query[-(width - 5) :]
            self.put(3, 3, shown, style)
            if not self.replace_name:
                self.put(3, 3 + cells(shown), " ", self.style("normal") | curses.A_REVERSE)
        if self.menu == "name":
            self.button(5, "Save name", self.accept_name, style=self.style("accent"))
            return cursor
        for row, line in enumerate(command_rows, start):
            self.put(row, 1, line, self.style("normal"))
        for row, entry in enumerate(rows, start):
            if entry.label == MENU_RULE:
                self.put(row, 1, "─" * (width - 2), self.style("outline"))
            elif entry.label.startswith(LABEL):
                self.put(row, 1, entry.label[len(LABEL) :].upper(), muted)
            else:
                self.draw_menu_row(row, entry, self.offset + row - start == self.selected, width)
        if not rows and self.menu not in {"refresh", "json-settings"}:
            self.put(
                start,
                1,
                "No matching sessions" if self.menu == "agents" else "No entries",
                muted,
            )
        return cursor

    def draw_menu_row(self, row: int, entry: Entry, active: bool, width: int) -> None:
        """One choice: a two-cell gutter carrying the marker on the selected
        row, the label, and the key or value right-aligned in muted."""
        meta = entry.meta
        kind = meta.get("kind")
        on = "active" if active else None
        if kind == "danger":
            text = self.style("danger", on)
        elif kind == "disabled":
            text = self.style("muted", on)
        else:
            text = self.style("active") if active else self.style("normal")
        if active:
            self.fill(row, self.style("active"))
            text |= curses.A_BOLD
        icon = meta.get("icon")
        if icon:
            current = kind == "workspace" and entry.key == "workspace:" + self.model.space["id"]
            style = self.style("accent", on) if current or icon == "+" else self.style("muted", on)
            self.put(row, 1, icon, style, 1)
            if current:
                text |= curses.A_BOLD
        elif active:
            self.put(row, 1, "▶", self.style("accent", "active"), 1)
        right = meta.get("right") or (self.key_label(meta["action"]) if meta.get("action") else "")
        right_style = self.style("accent", on) if kind == "toggle" else self.style("muted", on)
        room = width - 4 - (cells(right) + 1 if right else 0)
        label = visible(entry.label)
        if right and cells(label) > room and kind == "workspace":
            # The name matters more than its tab count: keep the name whole.
            right, room = "", width - 4
        if cells(label) > room:
            label = label[: max(0, room - 1)] + "…"
        self.put(row, 3, label, text, max(1, room))
        if right:
            self.put_right(row, right, right_style)
        self.hits.append((row, 0, width, entry.action))

    def draw_home(self, roster, width, height):
        """The workspace heading, the tab list, the roster."""
        cursor = None
        muted = self.style("muted")
        # The heading is the workspace chooser: the icon and name, a
        # chevron, and one click to switch, create or rename workspaces.
        # A double click renames in place.
        workspace_edit = bool(self.inline_editor and self.inline_target[1] is None)
        tab_edit = bool(self.inline_editor and self.inline_target[1] is not None)
        self.fill(0, self.style("header"))
        name_width = width - 4
        self.name_hits.append((0, 1, 1 + name_width, "workspace:" + self.model.space["id"]))
        header = self.style("header") | curses.A_BOLD
        icon = self.model.space.get("icon")
        icon = icon if isinstance(icon, str) and icon.isprintable() and icon else ""
        title = visible(self.model.space["name"])
        room = name_width - (len(icon) + 1 if icon else 0)
        if len(title) > room:
            title = title[: max(0, room - 1)] + "…"
        if icon and not workspace_edit:
            self.put(0, 1, icon, self.style("accent", "header") | curses.A_BOLD, len(icon))
            self.put(0, 1 + len(icon) + 1, title, header, room)
        else:
            self.put(0, 1, title, header, name_width)
        if workspace_edit:
            cursor = self.draw_inline(0, 1, name_width)
        self.context_hits.append(
            (0, 1, 1 + name_width, lambda: self.context_workspace(self.model.space))
        )
        # The chevron opens the chooser: switch, create, rename or delete.
        self.button(
            0,
            " ▾",
            lambda: self.open_menu("workspace"),
            x=width - 3,
            width=2,
            style=self.style("muted", "header"),
            context=lambda: self.context_workspace(self.model.space),
        )
        hint = "Enter save · Esc cancel" if width >= 25 else "↵ save · Esc cancel"
        # The row under the heading is blank; while a name is edited in
        # place it carries the editing hint. The section label keeps the
        # add glyph and, when the list overflows, its scroll arrows, at the
        # right end.
        if workspace_edit or tab_edit:
            self.put(1, 1, hint, self.style("accent"))
        self.put(2, 1, "TABS", muted)
        self.button(
            2,
            "  +",
            self.new_tab,
            x=width - 4,
            width=3,
            style=self.style("accent") | curses.A_BOLD,
        )
        tab = self.model.tab
        tabs = self.model.space["tabs"]
        available = self.tab_capacity()
        self.tab_offset = min(self.tab_offset, max(0, len(tabs) - available))
        if len(tabs) > available:
            self.button(2, "↑", lambda: self.scroll(-1), x=width - 8, width=2, style=muted)
            self.button(2, "↓", lambda: self.scroll(1), x=width - 6, width=2, style=muted)
        row = 3
        for index, item in enumerate(
            tabs[self.tab_offset : self.tab_offset + available], self.tab_offset
        ):

            def action(item=item):
                self.choose_tab(item)

            def context(item=item):
                self.context_tab(item)

            members = leaves(item["tree"])
            active = item == tab
            on = "active" if active else None
            # The row is one run so the selection reads as a bar: the number,
            # the name, then one glyph per pane and, on the selected row, the
            # tab's menu behind an ellipsis at the right end.
            number = str(index + 1)
            glyphs = "▮" * min(len(members), 6)
            tail = cells(glyphs) + (3 if active else 1)
            room = width - 2 - len(number) - tail - 1
            name = visible(item["name"])
            if cells(name) > room:
                name = name[: max(0, room - 1)] + "…"
            if active:
                self.fill(row, self.style("active"))
            self.put(row, 1, number, self.style("accent", "active") if active else muted)
            name_x = 2 + len(number)
            self.put(
                row,
                name_x,
                name,
                (self.style("active") | curses.A_BOLD) if active else self.style("normal"),
                room,
            )
            self.hits.append((row, 0, width - 3 if active else width, action))
            self.context_hits.append((row, 0, width, context))
            self.put(row, width - tail, glyphs, self.style("muted", on))
            self.name_hits.append((row, name_x, name_x + room, item["id"]))
            editing_this = active and tab_edit and self.inline_target[1] == item["id"]
            if editing_this:
                cursor = self.draw_inline(row, name_x, room)
            if active and not editing_this:
                self.button(
                    row,
                    "⋯",
                    lambda: self.open_menu("tab"),
                    x=width - 2,
                    width=1,
                    style=self.style("muted", "active"),
                )
            row += 1
        if not tabs:
            self.put(3, 3, "no tabs yet", muted)
            self.hits.append((3, 0, width, self.new_tab))
        bottom = height - self.FOOTER_ROWS
        roster_rows = self.roster_rows()
        if roster_rows and roster is not None:
            self.draw_roster(bottom - roster_rows, roster_rows, width, roster)
        return cursor

    def scroll(self, amount: int) -> None:
        self.clear_inline()
        if self.command_lines():
            self.command_offset = max(0, self.command_offset + amount)
        elif self.menu:
            self.selection.scroll(amount)
        else:
            self.tab_offset = max(0, self.tab_offset + amount)

    def wheel(self, y: int, amount: int) -> None:
        """The wheel scrolls the list under the pointer: the roster over its
        rows, otherwise whatever the panel is showing."""
        span = self.roster_span
        if not self.menu and span and span[0] <= y < span[1]:
            self.scroll_roster(amount)
        else:
            self.scroll(amount)

    def mouse(self, x: int, y: int, buttons: int) -> None:
        left = buttons & (
            curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED
        )
        right = buttons & (curses.BUTTON3_PRESSED | curses.BUTTON3_CLICKED)
        if buttons & curses.BUTTON4_PRESSED:
            self.wheel(y, -1)
        elif buttons & getattr(curses, "BUTTON5_PRESSED", 0):
            self.wheel(y, 1)
        elif left or right:
            height, width = self.size()
            if not (0 <= x < width and 0 <= y < height):
                return
            field = next((h for h in self.name_hits if h[0] == y and h[1] <= x < h[2]), None)
            edit_key = (
                self.inline_target[1] or "workspace:" + self.inline_target[0]
                if self.inline_target
                else None
            )
            if left and self.inline_editor and field and field[3] == edit_key:
                opened = self.last_name_click
                if not (
                    opened
                    and time.monotonic() - opened[1] <= 0.45
                    and abs(x - opened[2]) <= 1
                    and y == opened[3]
                ):
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
        if self.config_popup:
            return
        if key == curses.KEY_MOUSE:
            with contextlib.suppress(curses.error):
                _, x, y, _, buttons = curses.getmouse()
                self.mouse(x - self.INSET, y - self.INSET, buttons)
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
        elif self.menu:
            self.menu_input(key)
        elif key == "t":
            self.open_config_editor("colors")
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
            curses.KEY_PPAGE: -max(1, self.size()[0] - 9),
            curses.KEY_NPAGE: max(1, self.size()[0] - 9),
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
                curses.KEY_PPAGE: -max(1, self.size()[0] - 9),
                curses.KEY_NPAGE: max(1, self.size()[0] - 9),
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
        with contextlib.closing(self.actions.pending()) as pending:
            for action in pending:
                if not self.running:
                    continue
                with self.display.snapshot_scope():
                    self.action(action)
                    if self.running and not self.config_popup:
                        self.display.wait_for_input(self.model.tab)
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
        try:
            self.run_loop(events)
        finally:
            if self.config_popup:
                self.config_popup.close()
            self.source.close()

    def run_loop(self, events) -> None:
        next_poll = 0.0
        while self.running:
            try:
                if self.config_popup and self.finish_config_editor():
                    events.keys.clear()
                    curses.flushinp()
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
                            # Focus can also sit elsewhere for one poll while
                            # tmux finishes selecting the panel after the click
                            # that opened the menu, so it takes two polls in a
                            # row to count as the user's choice.
                            self.away_polls += 1
                            if self.away_polls >= 2:
                                self.clear_inline()
                                self.close_menu()
                        else:
                            self.away_polls = 0
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
