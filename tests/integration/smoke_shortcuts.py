"""Direct terminal key sequences and right-click menus on isolated real tmux/PTYs."""

from __future__ import annotations

import json
import shlex
import tempfile
from pathlib import Path

from tests.integration.routing import exercise as routing
from tests.integration.support import (
    FixtureResources,
    OuterClient,
    click_button,
    open_terminal,
    right_click,
    saved,
    sidebar,
    tab_row,
    wait,
    workspace_row,
)
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.model import leaves
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux


def standalone(directory: Path) -> None:
    with FixtureResources(parent=directory) as resources:
        _standalone(resources)


def _standalone(resources: FixtureResources) -> None:
    directory = resources.root
    library = resources.library("direct")
    source_socket = socket_path(directory, "absent-source")
    client = resources.client(["--data-dir", str(library), "--source-socket", source_socket])
    shells = Tmux(socket_path(library, "terminals"))
    wait(client, lambda: client.manifest(library), "direct-key viewer did not launch")
    manifest = client.manifest(library)
    viewer = Tmux(json.loads(manifest.read_text())["viewer_socket"])
    wait(client, lambda: "Layouts saved" in sidebar(viewer), "sidebar did not initialize")
    initial = saved(library)
    first_space = initial.space["id"]
    first_tab = initial.tab["id"]
    first_terminal = "=" + Shells.name(initial.pane) + ":"

    def key(action: str) -> None:
        # Exactly one physical-key encoding, with no Ctrl-g/tmux prefix.
        client.type(direct_sequence(action))

    def check(predicate, description: str) -> None:
        wait(client, predicate, description)

    def button(text: str) -> None:
        click_button(client, viewer, text)

    def content_ready() -> bool:
        model = saved(library)
        if not model.pane:
            return "No tabs yet" in sidebar(viewer)
        terminal_name = Shells.name(model.pane)
        return any(
            line.startswith("1 ") and terminal_name in line
            for line in viewer.run(
                "list-panes", "-F", "#{pane_active} #{pane_start_command}"
            ).splitlines()
        )

    def rename(action: str, name: str) -> None:
        key(action)
        check(lambda: "Type a name" in sidebar(viewer), "rename action did not open")
        client.type(name + "\r")
        # No Ctrl-u/backspace: the first typed character must replace the
        # previously selected name rather than append to it.
        field = "tab" if action == "rename-tab" else "space"
        check(
            lambda: getattr(saved(library), field)["name"] == name,
            "rename did not replace the existing name on first input",
        )
        check(content_ready, "renamed terminal did not finish rendering")

    def choose_tab(index: int, expected: str) -> None:
        if saved(library).tab["name"] != expected:
            key(f"select-tab-{index}")
        check(
            lambda: saved(library).tab["name"] == expected and content_ready(),
            "direct tab selection failed",
        )
        client.pump(0.2)

    def choose_space(index: int, expected: str) -> None:
        if saved(library).space["name"] != expected:
            key(f"select-workspace-{index}")
        check(
            lambda: (
                saved(library).space["name"] == expected
                and expected in sidebar(viewer).splitlines()[0]
                and content_ready()
            ),
            "direct workspace selection failed",
        )

    def first_saved_tab():
        return next(
            tab
            for space in saved(library).state["workspaces"]
            for tab in space["tabs"]
            if tab["id"] == first_tab
        )

    check(
        lambda: shells.run("has-session", "-t", first_terminal) == "",
        "initial ordinary shell missing",
    )
    shell_pid = shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    client.type("export DIRECT_STATE=retained; cd /tmp\r")
    rename("rename-tab", "Alpha")
    for count, name in ((2, "Beta"), (3, "Gamma")):
        key("new-tab")
        check(
            lambda count=count: len(saved(library).space["tabs"]) == count,
            "one direct new-tab key did not create exactly one tab",
        )
        client.pump(0.35)
        assert len(saved(library).space["tabs"]) == count
        open_terminal(client, viewer, library)
        rename("rename-tab", name)
    choose_tab(1, "Alpha")
    key("previous-tab")
    check(lambda: saved(library).tab["name"] == "Gamma", "previous tab did not wrap")
    key("next-tab")
    check(lambda: saved(library).tab["name"] == "Alpha", "next tab did not wrap")
    choose_tab(2, "Beta")
    key("select-tab-9")
    assert saved(library).tab["name"] == "Beta"

    for count, action in ((2, "split-right"), (3, "split-below"), (4, "split-right")):
        neighbour = saved(library).pane.get("cwd")
        key(action)
        check(
            lambda count=count: len(leaves(saved(library).tab["tree"])) == count,
            "one direct split key did not create one pane",
        )
        # A terminal chosen in the split starts where its neighbour was.
        assert saved(library).pane.get("cwd") == neighbour, "split lost the neighbour's directory"
        open_terminal(client, viewer, library)
    check(
        lambda: len(viewer.run("list-panes").splitlines()) == 5,
        "direct split actions did not produce a four-pane layout",
    )
    tab = saved(library).tab
    pane_ids = [pane["id"] for pane in leaves(tab["tree"])]
    next_pane = pane_ids[(pane_ids.index(tab["focus"]) + 1) % len(pane_ids)]
    key("next-pane")
    check(lambda: saved(library).pane["id"] == next_pane, "direct next-pane failed")
    key("previous-pane")
    check(lambda: saved(library).pane["id"] == tab["focus"], "direct previous-pane failed")
    key("focus")
    check(lambda: len(viewer.run("list-panes").splitlines()) == 2, "direct focus failed")
    key("focus")
    check(lambda: len(viewer.run("list-panes").splitlines()) == 5, "direct layout failed")
    key("sidebar")
    check(
        lambda: "1 Workspaces" in viewer.run("list-panes", "-F", "#{pane_active} #{@viewer_agent}"),
        "direct sidebar key failed",
    )
    key("attach")
    check(lambda: "Attach to selected pane" in sidebar(viewer), "direct attach key failed")
    client.type("\x1b")

    for count, space_name, tab_name in ((2, "Research", "Delta"), (3, "Archive", "Epsilon")):
        key("new-workspace")
        check(lambda: "Type a name" in sidebar(viewer), "direct new workspace failed")
        client.type(space_name + "\r")
        check(
            lambda count=count: len(saved(library).state["workspaces"]) == count,
            "one direct workspace key did not create exactly one workspace",
        )
        key("new-tab")
        check(lambda: saved(library).tab is not None, "direct tab in new workspace failed")
        open_terminal(client, viewer, library)
        rename("rename-tab", tab_name)
        if count == 2:
            rename("rename-workspace", "Lab")
    choose_space(1, "Workspace 1")
    key("previous-workspace")
    check(lambda: saved(library).space["name"] == "Archive", "previous workspace did not wrap")
    key("next-workspace")
    check(lambda: saved(library).space["id"] == first_space, "next workspace did not wrap")
    choose_space(2, "Lab")
    key("previous-workspace")
    check(lambda: saved(library).space["id"] == first_space, "previous workspace failed")
    key("select-workspace-9")
    assert saved(library).space["id"] == first_space
    choose_tab(2, "Beta")
    print(
        "PASS: direct CSI keys without prefixes, one action per key, tab/workspace indexes "
        "and cycles, four panes, focus, pane navigation, attach/sidebar and rename replacement",
        flush=True,
    )

    # Inactive tab rows and the active detail row both open the correct menu.
    # Rename must leave the ordinary process intact.
    for previous_name, new_name, status_line in (
        ("Alpha", "Alpha context", False),
        ("Alpha context", "Alpha status", True),
    ):
        choose_tab(1, previous_name) if status_line else choose_tab(2, "Beta")
        check(
            lambda previous_name=previous_name: previous_name in sidebar(viewer),
            "inactive tab missing",
        )
        right_click(client, viewer, tab_row(viewer, previous_name) + int(status_line))
        check(lambda: "Tab options" in sidebar(viewer), "tab right-click menu missing")
        assert saved(library).tab["id"] == first_tab, (
            "right-click context selected " + saved(library).tab["name"]
        )
        button("Rename tab")
        client.type(new_name + "\r")
        check(
            lambda new_name=new_name: first_saved_tab()["name"] == new_name,
            "right-click rename acted on the active tab instead of the clicked tab",
        )
        assert any(tab["name"] == "Beta" for tab in saved(library).space["tabs"])
        assert shell_pid == shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")

    right_click(client, viewer, 0)
    check(lambda: "Workspace options" in sidebar(viewer), "workspace header menu missing")
    button("Rename workspace")
    client.type("Main\r")
    check(lambda: saved(library).space["name"] == "Main", "workspace context rename failed")
    choose_space(3, "Archive")
    switch_row, buttons = workspace_row(sidebar(viewer))
    right_click(client, viewer, switch_row, buttons.index("1") + 1)
    check(lambda: "Workspace options" in sidebar(viewer), "inactive workspace button menu missing")
    button("Rename workspace")
    client.type("Primary\r")
    check(
        lambda: saved(library).state["workspaces"][0]["name"] == "Primary",
        "right-click workspace button targeted the wrong workspace",
    )
    assert saved(library).state["workspaces"][2]["name"] == "Archive"

    choose_space(1, "Primary")
    choose_tab(2, "Beta")
    key("close-pane")
    check(lambda: len(leaves(saved(library).tab["tree"])) == 3, "direct close-pane failed")
    key("close-tab")
    check(lambda: len(saved(library).space["tabs"]) == 2, "direct close-tab failed")
    choose_tab(1, "Alpha status")
    client.type('printf \'DIRECT_RETAINED:%s:%s\\n\' "$DIRECT_STATE" "$PWD"\r')
    check(
        lambda: (
            "DIRECT_RETAINED:retained:/tmp"
            in shells.run("capture-pane", "-p", "-t", first_terminal)
        ),
        "direct navigation or context menus lost the original shell state",
    )
    button("Exit")
    check(lambda: client.process.poll() is not None, "direct-key viewer did not exit")
    assert not manifest.exists()
    assert shell_pid == shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    assert not Path(source_socket).exists(), "test unexpectedly created an external server"
    print(
        "PASS: right-click inactive tab title/status and workspace header/button targets; "
        "direct close actions and original shell PID/cwd/environment retained",
        flush=True,
    )


def nested(directory: Path) -> None:
    with FixtureResources(parent=directory) as resources:
        _nested(resources)


def _nested(resources: FixtureResources) -> None:
    library = resources.library("nested")
    outer = resources.server("shortcut-outer")
    shells = Tmux(socket_path(library, "terminals"))

    def manifest():
        return next((library / "windows").glob("*/runtime.json"), None)

    def identities():
        return outer.run("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}:#{session_name}")

    outer.run(
        "-f",
        "/dev/null",
        "new-session",
        "-d",
        "-s",
        "host",
        "-e",
        f"HOME={resources.root}",
        "/bin/sh",
    )
    # The server, not the outer attachment client's environment, supplies
    # plugin-created windows. Keep fixture shells away from user rc/history.
    outer.run("set-environment", "-g", "SHELL", "/bin/sh")
    outer.run("set-environment", "-g", "HOME", str(resources.root))
    outer.run("set-environment", "-t", "=host:", "SHELL", "/bin/sh")
    outer.run("set-option", "-g", "default-shell", "/bin/sh")
    original_identities = identities()
    outer.run("set-option", "-g", "@tmux-workspaces-data-dir", str(library))
    plugin = Path(__file__).resolve().parents[2] / "tmux-workspaces.tmux"
    outer.run("run-shell", shlex.quote(str(plugin)).replace("#", "##"))
    client = resources.own_client(OuterClient(outer.socket))
    wait(client, lambda: outer.run("list-clients"), "outer tmux client did not attach")
    client.prefix_key(outer, "W")
    wait(client, manifest, "plugin did not open nested viewer")
    viewer = Tmux(json.loads(manifest().read_text())["viewer_socket"])

    def content_ready() -> bool:
        pane = saved(library).pane
        if not pane:
            return False
        name = Shells.name(pane)
        selected = any(
            line.startswith("1 ") and name in line
            for line in viewer.run(
                "list-panes", "-F", "#{pane_active} #{pane_start_command}"
            ).splitlines()
        )
        return (
            selected
            and int(
                shells.run("display-message", "-p", "-t", "=" + name + ":", "#{session_attached}")
                or 0
            )
            > 0
        )

    wait(client, lambda: "Layouts saved" in sidebar(viewer), "nested sidebar did not initialize")
    original = saved(library)
    terminal = "=" + Shells.name(original.pane) + ":"
    wait(client, lambda: shells.run("has-session", "-t", terminal) == "", "nested shell missing")
    wait(client, content_ready, "nested shell attachment did not become ready")
    shell_pid = shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    client.type("export DIRECT_NESTED=retained\r")
    client.type(direct_sequence("new-tab"))
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "direct key lost in outer tmux")
    client.pump(0.35)
    assert len(saved(library).space["tabs"]) == 2
    open_terminal(client, viewer, library)
    client.type(direct_sequence("rename-tab"))
    wait(client, lambda: "Type a name" in sidebar(viewer), "nested direct rename failed")
    client.type("Nested\r")
    wait(client, lambda: saved(library).tab["name"] == "Nested", "nested rename appended name")
    wait(client, content_ready, "nested rename did not finish rendering")
    client.type(direct_sequence("split-right"))
    wait(
        client,
        lambda: len(leaves(saved(library).tab["tree"])) == 2,
        "nested direct split failed",
    )
    open_terminal(client, viewer, library)
    wait(client, content_ready, "nested split did not finish rendering")
    wait(
        client,
        lambda: original.tab["name"] in sidebar(viewer),
        "nested sidebar did not finish drawing the inactive tab",
    )
    right_click(client, viewer, tab_row(viewer, original.tab["name"]))
    wait(client, lambda: "Tab options" in sidebar(viewer), "right-click lost in outer tmux")
    assert saved(library).tab["id"] == original.tab["id"]
    click_button(client, viewer, "Rename tab")
    client.type("Nested first\r")
    wait(
        client,
        lambda: saved(library).space["tabs"][0]["name"] == "Nested first",
        "nested right-click renamed the wrong tab",
    )
    wait(client, content_ready, "nested context rename did not finish rendering")
    client.type(direct_sequence("select-tab-2"))
    wait(
        client,
        lambda: saved(library).tab["name"] == "Nested" and content_ready(),
        "nested second-tab key failed",
    )
    client.type(direct_sequence("select-tab-1"))
    wait(
        client,
        lambda: saved(library).tab["id"] == original.tab["id"] and content_ready(),
        "nested index key failed",
    )
    client.type("printf 'NESTED_RETAINED:%s\\n' \"$DIRECT_NESTED\"\r")
    wait(
        client,
        lambda: "NESTED_RETAINED:retained" in shells.run("capture-pane", "-p", "-t", terminal),
        "nested shortcuts/context menu lost the original shell",
    )
    assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    click_button(client, viewer, "Exit")
    wait(client, lambda: manifest() is None, "nested viewer did not exit")
    wait(client, lambda: identities() == original_identities, "outer host panes changed")
    assert client.process.poll() is None
    print(
        "PASS: direct CSI shortcuts and right-click cross an ordinary outer tmux; "
        "one action per key and outer/shell processes retained (PTY, not Ghostty GUI)",
        flush=True,
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-shortcuts-smoke-", dir="/tmp") as directory:
        standalone(Path(directory))
        nested(Path(directory))
        for pane_count in (1, 4):
            routing(Path(directory), pane_count)
