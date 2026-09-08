"""Inline tab editing via real SGR mouse input on disposable tmux/PTY sessions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from tests.integration.support import FixtureResources, click_button, saved, sidebar, tab_row, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.model import leaves
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux


def exercise(root: Path) -> None:
    with FixtureResources(parent=root) as resources:
        _exercise(resources)


def _exercise(resources: FixtureResources) -> None:
    root = resources.root
    library = resources.library()
    ghostty = (
        shutil.which("infocmp")
        and subprocess.run(["infocmp", "xterm-ghostty"], capture_output=True).returncode == 0
    )
    terminal_env = {"TERM": "xterm-ghostty" if ghostty else "xterm-256color"}
    client = resources.client(
        ["--data-dir", str(library), "--source-socket", str(root / "absent.sock")],
        terminal_env=terminal_env,
    )
    shells = Tmux(socket_path(library, "terminals"))
    wait(client, lambda: client.manifest(library), "inline viewer did not start")
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    wait(client, lambda: "Layouts saved" in sidebar(viewer), "sidebar did not draw")
    original = saved(library)
    first_id, first_name = original.tab["id"], original.tab["name"]
    terminal = "=" + Shells.name(original.pane) + ":"
    pid = shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    client.type("export INLINE_SENTINEL=preserved\r")

    def key(action):
        client.type(direct_sequence(action))

    def tap(row, column):
        top = int(viewer.run("display-message", "-p", "-t", "%0", "#{pane_top}"))
        os.write(client.master, f"\x1b[<0;{column + 1};{row + top + 1}M".encode())
        client.pump(0.035)
        os.write(client.master, f"\x1b[<0;{column + 1};{row + top + 1}m".encode())
        client.pump(0.035)

    def double(name):
        row = tab_row(viewer, name)
        column = sidebar(viewer).splitlines()[row].index(name) + 1
        tap(row, column)
        tap(row, column)

    def editing():
        return "Esc cancel" in sidebar(viewer)

    def begin(name):
        client.pump(0.5)
        double(name)
        wait(client, editing, "active name double-click did not open inline editor")
        assert "Type a name" not in sidebar(viewer), "opened old full-sidebar form"
        assert viewer.run("display-message", "-p", "-t", "viewer:", "#{pane_id}") == "%0"

    def begin_workspace():
        client.pump(0.5)
        tap(0, 6)
        tap(0, 6)
        wait(client, editing, "workspace header double-click did not open inline editor")
        assert "Type a name" not in sidebar(viewer)
        assert viewer.run("display-message", "-p", "-t", "viewer:", "#{pane_id}") == "%0"

    begin_workspace()
    client.type("Development")
    assert saved(library).space["name"] == original.space["name"]
    client.type("\r")
    wait(
        client,
        lambda: saved(library).space["name"] == "Development",
        "workspace name did not save",
    )
    wait(
        client,
        lambda: "Development" in sidebar(viewer).splitlines()[0],
        "workspace header did not redraw",
    )
    assert saved(library).tab["name"] == first_name
    assert saved(library).tab["id"] == first_id
    begin_workspace()
    client.type("discard-workspace")
    client.type("\x1b")
    wait(client, lambda: not editing(), "workspace Escape did not cancel")
    assert saved(library).space["name"] == "Development"

    # The first click on an inactive tab selects it, with no accidental editor.
    key("new-tab")
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "new tab failed")
    wait(client, lambda: saved(library).tab["name"] in sidebar(viewer), "new tab did not draw")
    double(first_name)
    wait(client, lambda: saved(library).tab["id"] == first_id, "click failed to select tab")
    assert not editing(), "double-clicking an inactive tab started editing"
    begin(first_name)
    client.type("inline-name")
    assert saved(library).tab["name"] == first_name, "draft saved before Enter"
    client.type("\x1bOD\x1b[3~X\r")  # Left, Delete, X, Enter in application keypad mode.
    wait(
        client,
        lambda: saved(library).tab["name"] == "inline-namX",
        "inline cursor editing failed",
    )
    assert pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    assert "inline-name" not in shells.run("capture-pane", "-p", "-t", terminal), (
        "rename input leaked into shell"
    )

    begin("inline-namX")
    client.type("discard-me")
    client.type("\x1b")
    wait(client, lambda: not editing(), "Escape did not cancel")
    assert saved(library).tab["name"] == "inline-namX"
    begin("inline-namX")
    client.type("\x15\r")
    assert editing(), "empty name closed editor"
    assert saved(library).tab["name"] == "inline-namX"
    client.type("\x1b")
    wait(client, lambda: not editing(), "empty edit did not cancel")

    # The editor keeps keyboard focus while resize temporarily focuses the layout.
    for action in ("split-right", "split-below", "split-right"):
        key(action)
    wait(client, lambda: len(leaves(saved(library).tab["tree"])) == 4, "four splits missing")
    begin("inline-namX")
    client.type("after-resize")
    client.resize(70, 22)
    wait(client, editing, "resize lost editor")
    assert viewer.run("display-message", "-p", "-t", "viewer:", "#{pane_id}") == "%0"
    client.type("-ok\r")
    wait(
        client,
        lambda: saved(library).tab["name"] == "after-resize-ok",
        "resize redirected name input",
    )
    client.resize(160, 38)
    assert len(leaves(saved(library).tab["tree"])) == 4
    begin("after-resize-ok")
    client.type("cancel-on-click")
    # Clicking a content terminal discards the draft after focus moves there.
    content = (
        viewer.run("list-panes", "-F", "#{pane_id} #{pane_left} #{pane_top}")
        .splitlines()[1]
        .split()
    )
    tap(int(content[2]), int(content[1]) + 2)
    wait(client, lambda: not editing(), "content click left stale editor open")
    assert saved(library).tab["name"] == "after-resize-ok"
    assert pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    key("new-workspace")
    wait(client, lambda: "Type a name" in sidebar(viewer), "new workspace form missing")
    client.type("Empty\r")
    wait(client, lambda: "No tabs yet" in sidebar(viewer), "empty workspace did not draw")
    begin_workspace()
    client.type("Empty renamed\r")
    wait(
        client,
        lambda: saved(library).space["name"] == "Empty renamed",
        "empty workspace rename failed",
    )
    assert saved(library).space["tabs"] == []
    key("select-workspace-1")
    wait(client, lambda: "after-resize-ok" in sidebar(viewer), "return to workspace failed")
    assert saved(library).space["name"] == "Development"
    error_path = client.manifest(library).parent / "error.txt"
    click_button(client, viewer, "Exit")
    client.process.wait(timeout=10)
    client.pump(0.1)
    assert client.process.returncode == 0, {
        "returncode": client.process.returncode,
        "terminal": client.output[-3000:].decode(errors="replace"),
        "runtime_error": error_path.read_text() if error_path.exists() else None,
    }
    client.close()
    client = resources.client(
        ["--data-dir", str(library), "--source-socket", str(root / "absent.sock")],
        terminal_env=terminal_env,
    )
    wait(client, lambda: client.manifest(library), "reopen failed")
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    wait(client, lambda: "after-resize-ok" in sidebar(viewer), "inline name was not persisted")
    assert pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    assert saved(library).space["name"] == "Development"
    # Physical double-click plus typing in one terminal write must reach
    # the inline field before the delayed tmux DoubleClick notification.
    for workspace in (True, False):
        client.pump(0.5)
        name = "Immediate workspace" if workspace else "Immediate tab"
        row = 0 if workspace else tab_row(viewer, "after-resize-ok")
        column = 7 if workspace else sidebar(viewer).splitlines()[row].index("after-resize-ok") + 2
        top = int(viewer.run("display-message", "-p", "-t", "%0", "#{pane_top}"))
        click = f"\x1b[<0;{column};{row + top + 1}M\x1b[<0;{column};{row + top + 1}m"
        os.write(client.master, (click + click + name + "\r").encode())
        wait(
            client,
            lambda workspace=workspace, name=name: (
                (saved(library).space if workspace else saved(library).tab)["name"] == name
            ),
            "same-write double-click typing did not reach inline name",
        )
        client.pump(0.5)
        assert not editing(), "delayed double-click reopened the completed editor"
        assert name not in shells.run("capture-pane", "-p", "-t", terminal)
    assert pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    print(
        "PASS: tab/workspace/empty-workspace inline rename, cursor editing, Enter/Escape, "
        "resize focus, outside-click cancellation, no input leak, "
        "four-pane layout and shell/name persistence "
        f"({terminal_env['TERM']})",
        flush=True,
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-inline-", dir="/tmp") as directory:
        exercise(Path(directory))
