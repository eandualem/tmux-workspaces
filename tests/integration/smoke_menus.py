"""Keyboard-only menu navigation on real PTYs, against disposable demo sessions.

No mouse event is sent anywhere in this exercise: every chooser and options menu
is opened, filtered, navigated and activated with keys, and menu input must never
reach a shell. Existing mouse coverage stays in smoke.py and smoke_shortcuts.py.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from tests.integration.support import FixtureResources, saved, sidebar, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux

DOWN, UP, ENTER, ESCAPE = "\x1b[B", "\x1b[A", "\r", "\x1b"
ESCAPE_SEQUENCE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def exercise(root: Path) -> None:
    with FixtureResources(parent=root) as resources:
        _exercise(resources)


def _exercise(resources: FixtureResources) -> None:
    directory = resources.root
    library = resources.library("demo", demo=True)
    client = resources.client(["--demo", "--data-dir", str(directory)])
    shells = Tmux(socket_path(library, "terminals"))
    wait(client, lambda: client.manifest(library), "keyboard viewer did not start")
    runtime = json.loads(client.manifest(library).read_text())
    viewer, source = Tmux(runtime["viewer_socket"]), Tmux(runtime["source_socket"])

    def panel() -> str:
        return sidebar(viewer)

    def styled() -> list[str]:
        # Colours survive this capture, so the active row can be told apart.
        return viewer.run("capture-pane", "-p", "-e", "-t", "%0").splitlines()

    def marked() -> tuple[str, ...]:
        """Text of the rows carrying the active-row background, escapes removed.

        Only the selection is compared, never the rest of the frame: an unrelated
        repaint must not be mistaken for the highlight having moved. The chooser's
        filter prompt shares that background and is excluded.
        """
        rows = []
        for line in styled():
            # The selection background is the exact color in palette slot 18.
            if "48;5;18" not in line:
                continue
            text = ESCAPE_SEQUENCE.sub("", line).strip()
            if text and not text.startswith(">"):
                rows.append(text)
        return tuple(rows)

    def highlighted(label: str) -> bool:
        return any(label in row for row in marked())

    def focused() -> str:
        return viewer.run("display-message", "-p", "-t", "viewer:", "#{pane_id}")

    def key(name: str) -> None:
        client.type("\x07" + name)

    def press(text: str) -> None:
        client.type(text)

    def open_menu(name: str, title: str) -> None:
        key(name)
        wait(client, lambda: title in panel(), "keyboard did not open: " + title)
        assert focused() == "%0", "opening a menu left the keyboard in a shell"

    def select(label: str) -> None:
        """Walk the highlight onto a row with Down alone, scrolling it into view.

        Every Down is observed moving the highlight before the next one is sent,
        so a slow frame cannot let one keystroke stand in for several. The walk
        stays bounded: each step waits with the shared timeout, and a list that
        never reaches the label runs out of rows.
        """
        for _ in range(40):
            before = marked()
            if any(label in row for row in before):
                return
            press(DOWN)
            wait(
                client,
                lambda before=before: marked() != before,
                "Down did not move the menu selection towards: " + label,
            )
        raise AssertionError("could not select by keyboard: " + label + "\n" + panel())

    def activate(label: str) -> None:
        select(label)
        press(ENTER)

    wait(client, lambda: "Detach" in panel(), "sidebar did not initialize")
    start = saved(library)
    tab_id, tab_name, pane_id = start.tab["id"], start.tab["name"], start.pane["id"]
    terminal = "=" + Shells.name(start.pane) + ":"
    wait(
        client,
        lambda: shells.run("has-session", "-t", terminal, check=False) == "",
        "ordinary shell missing",
    )
    shell_pid = shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    client.type("export MENU_SENTINEL=kept\r")

    def shell_text() -> str:
        return shells.run("capture-pane", "-S", "-", "-p", "-t", terminal)

    # Filtering, empty results and activation, by keyboard only.
    open_menu("a", "Attach to selected pane")
    press("qqzz")
    wait(client, lambda: "No matching sessions" in panel(), "empty filter result missing")
    press(ENTER)
    client.pump(0.4)
    assert "Attach to selected pane" in panel(), "Enter on an empty result closed the chooser"
    assert saved(library).pane["agent"] is None, "empty result attached something"
    press("\x15")  # Ctrl-u clears the filter without leaving the menu.
    press("re")
    wait(client, lambda: "manager" not in panel(), "filter did not narrow the chooser")
    press(UP)  # Up on the first result stays there instead of wrapping.
    wait(client, lambda: highlighted("researcher"), "filtering did not select the first result")
    activate("reviewer")
    wait(
        client,
        lambda: saved(library).pane["agent"] == "reviewer",
        "keyboard attach did not reach the selected session",
    )
    attached = saved(library)
    assert attached.pane["id"] == pane_id, "keyboard attach moved to another pane"
    assert attached.tab["id"] == tab_id and attached.tab["name"] == tab_name
    assert "qqzz" not in shell_text(), "menu filter input leaked into the parked shell"
    assert "MENU_SENTINEL" in shell_text(), "parked shell lost its history"

    # Return pane to shell, also keyboard only, keeps the parked shell and session.
    open_menu("m", "Tab options")
    activate("Return pane to shell")
    wait(
        client,
        lambda: saved(library).pane["agent"] is None,
        "keyboard return to shell did not detach the pane",
    )
    assert saved(library).pane["id"] == pane_id, "return to shell changed the target pane"
    assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    assert source.run("has-session", "-t", "=reviewer:", check=False) == "", (
        "returning to a shell stopped the external session"
    )
    client.type("printf 'KEPT:%s\\n' \"$MENU_SENTINEL\"\r")
    wait(client, lambda: "KEPT:kept" in shell_text(), "parked shell did not resume")

    # Escape closes the whole overlay and hands the keyboard back to the pane.
    open_menu("m", "Tab options")
    press(ESCAPE)
    wait(client, lambda: "Tab options" not in panel(), "Escape did not close the menu")
    client.type("printf 'AFTER_ESCAPE\\n'\r")
    wait(client, lambda: "AFTER_ESCAPE" in shell_text(), "Escape did not restore pane focus")

    # A resize while a menu is open must not drop the keyboard into a shell.
    open_menu("a", "Attach to selected pane")
    client.resize(120, 30)
    press("qqzz")
    wait(client, lambda: "No matching sessions" in panel(), "resize lost the open chooser")
    assert focused() == "%0", "resize moved menu input to a shell"
    assert "qqzz" not in shell_text(), "menu input after a resize leaked into the shell"
    # Shrinking below the drawable minimum hides the rows, so Enter must activate
    # nothing until the chooser is visible again.
    press("\x15")
    wait(client, lambda: "manager" in panel(), "clearing the filter did not restore the sessions")
    select("manager")
    client.resize(120, 12)
    wait(client, lambda: "Enlarge terminal" in panel(), "small terminal warning missing")
    press(ENTER)
    client.pump(0.5)
    assert saved(library).pane["agent"] is None, "a hidden row was activated"
    client.resize(160, 38)
    wait(client, lambda: "Attach to selected pane" in panel(), "chooser did not return")
    activate("manager")
    wait(
        client,
        lambda: saved(library).pane["agent"] == "manager",
        "the restored chooser did not activate its visible row",
    )
    open_menu("m", "Tab options")
    activate("Return pane to shell")
    wait(client, lambda: saved(library).pane["agent"] is None, "return to shell failed")
    wait(client, lambda: "Detach" in panel(), "sidebar did not settle after resize")

    # Tab reordering and transfer are menu-only commands; both by keyboard here.
    key("t")
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "new tab shortcut failed")
    second = saved(library).tab["id"]
    open_menu("m", "Tab options")
    activate("Move tab up")
    wait(
        client,
        lambda: next(tab["id"] for tab in saved(library).space["tabs"]) == second,
        "keyboard tab reordering failed",
    )
    key("W")
    wait(client, lambda: "Type a name" in panel(), "new workspace prompt missing")
    press("Shelf" + ENTER)
    wait(client, lambda: "No tabs yet" in panel(), "keyboard workspace creation failed")
    key("[")
    wait(client, lambda: "No tabs yet" not in panel(), "did not return to the first workspace")
    open_menu("m", "Tab options")
    activate("Move to workspace")
    wait(client, lambda: "Move tab to" in panel(), "transfer list did not open")
    activate("Shelf")
    wait(
        client,
        lambda: (
            [tab["id"] for tab in saved(library).space["tabs"]] == [second]
            and saved(library).space["name"] == "Shelf"
        ),
        "keyboard tab transfer failed",
    )

    # Workspace options: an empty workspace can be deleted without the mouse.
    open_menu("m", "Tab options")
    activate("Close tab")
    wait(client, lambda: "No tabs yet" in panel(), "close tab failed")
    open_menu("M", "Workspace options")
    activate("Delete empty workspace")
    wait(
        client,
        lambda: all(space["name"] != "Shelf" for space in saved(library).state["workspaces"]),
        "keyboard deletion of an empty workspace failed",
    )
    print(
        "PASS: keyboard-only attach with filtering and empty results, return to shell with a "
        "preserved target, Escape focus handover, resize with an open menu, tab reorder and "
        "transfer, and empty-workspace deletion",
        flush=True,
    )

    # Overflow: the selection scrolls with the window and stays activatable.
    for index in range(9):
        key("W")
        wait(client, lambda: "Type a name" in panel(), "workspace prompt missing")
        press(f"Space {index + 2}" + ENTER)
        wait(
            client,
            lambda index=index: len(saved(library).state["workspaces"]) == index + 2,
            "workspace creation failed",
        )
    client.resize(160, 16)
    wait(client, lambda: "Space 10" in panel() or "Detach" in panel(), "resize failed")
    open_menu("w", "Workspaces")
    assert "Space 10" not in panel(), "the overflowing list already showed its last row"
    activate("Space 10")
    wait(
        client,
        lambda: saved(library).space["name"] == "Space 10",
        "keyboard selection past the visible window failed",
    )
    client.resize(160, 38)
    assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    print(
        "PASS: keyboard selection scrolls an overflowing chooser into view and activates it "
        "while the original shell keeps running",
        flush=True,
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-menus-", dir="/tmp") as directory:
        exercise(Path(directory))
