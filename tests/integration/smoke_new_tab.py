"""A new tab opens as a chooser on a real PTY: a shell or a session, picked in the pane.

Nothing here touches the sidebar's own attachment chooser. The pane offers the
same roster, the choice is made by keyboard and by mouse, and every outcome is
checked in the saved layout and on the terminal server, not only on screen.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.integration.support import FixtureResources, saved, sidebar, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.chooser import EMPTY_ROSTER, ROSTER_HEADING, TERMINAL, TITLE
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux

DOWN, ENTER = "\x1b[B", "\r"


def exercise(root: Path) -> None:
    with FixtureResources(parent=root) as resources:
        _exercise(resources)


def _exercise(resources: FixtureResources) -> None:
    directory = resources.root
    library = resources.library("demo", demo=True)
    client = resources.client(["--demo", "--data-dir", str(directory)])
    shells = Tmux(socket_path(library, "terminals"))
    wait(client, lambda: client.manifest(library), "viewer did not start")
    runtime = json.loads(client.manifest(library).read_text())
    viewer, source = Tmux(runtime["viewer_socket"]), Tmux(runtime["source_socket"])
    wait(client, lambda: "Layouts saved" in sidebar(viewer), "sidebar did not initialize")

    def key(name: str) -> None:
        client.type("\x07" + name)

    def content_pane() -> str:
        """The pane the chooser or its replacement runs in: the focused leaf."""
        return next(
            line.split("|")[0]
            for line in viewer.run(
                "list-panes", "-F", "#{pane_id}|#{@viewer_leaf_id}", check=False
            ).splitlines()
            if line.split("|")[1] == saved(library).tab["focus"]
        )

    def content(start: str = "") -> str:
        return viewer.run(
            "capture-pane", "-p", *(["-S", start] if start else []), "-t", content_pane()
        )

    def pane_geometry(pane: str) -> tuple[int, int]:
        left, top = viewer.run(
            "display-message", "-p", "-t", pane, "#{pane_left} #{pane_top}"
        ).split()
        return int(left), int(top)

    def chooser_shown() -> bool:
        text = content()
        return (
            TITLE in text and TERMINAL in text and (ROSTER_HEADING in text or EMPTY_ROSTER in text)
        )

    def shell_name() -> str:
        return Shells.name(saved(library).pane)

    def shell_exists() -> bool:
        return shell_name() in shells.run("list-sessions", "-F", "#{session_name}", check=False)

    first_shell = shell_name()
    first_pid = shells.run("display-message", "-p", "-t", "=" + first_shell + ":", "#{pane_pid}")
    shells_before = set(shells.run("list-sessions", "-F", "#{session_name}").splitlines())
    online = sorted(source.run("list-sessions", "-F", "#{session_name}").splitlines())
    assert online, "demo fixture sessions missing"

    # 1. A new tab is empty: no shell is created for it, the pane offers the choice,
    #    the sidebar says so, and the pane has keyboard focus.
    key("t")
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "new tab was not created")
    assert saved(library).pane.get("empty") is True, "the new tab did not open empty"
    wait(client, chooser_shown, "the new pane did not show the chooser")
    wait(client, lambda: "Empty" in sidebar(viewer), "the sidebar did not label the empty pane")
    text = content()
    for name in online:
        assert name in text, f"the chooser did not list {name}\n{text}"
    assert "notes" in text and "offline" in text, "the offline demo session is missing\n" + text
    assert (
        set(shells.run("list-sessions", "-F", "#{session_name}").splitlines()) == shells_before
    ), "an empty pane created a shell"
    assert viewer.run("display-message", "-p", "-t", "viewer:", "#{pane_id}") == content_pane(), (
        "the chooser pane did not receive focus"
    )

    # 2. Enter on the first row opens an ordinary shell in that very pane.
    client.type(ENTER)
    wait(client, lambda: not saved(library).pane.get("empty"), "Enter did not fill the pane")
    assert saved(library).pane["agent"] is None
    wait(client, shell_exists, "the chosen terminal was not created")
    wait(client, lambda: "Shell" in sidebar(viewer), "the sidebar still shows an empty pane")
    client.type("printf 'CHOSEN_%s\\n' TERMINAL\r")
    wait(
        client,
        lambda: (
            "CHOSEN_TERMINAL" in shells.run("capture-pane", "-p", "-t", "=" + shell_name() + ":")
        ),
        "typing after the choice did not reach the new shell",
    )
    assert first_pid == shells.run(
        "display-message", "-p", "-t", "=" + first_shell + ":", "#{pane_pid}"
    ), "the first tab's shell was restarted"
    print("PASS: a new tab opens as a chooser and Enter gives it a shell", flush=True)

    # 3. Down + Enter attaches the first session of the sorted roster; no shell
    #    is ever created for a pane that attaches straight away.
    key("t")
    wait(client, lambda: len(saved(library).space["tabs"]) == 3, "second new tab failed")
    wait(client, chooser_shown, "second chooser missing")
    expected = sorted([*online, "notes"])[0]
    client.type(DOWN)
    wait(client, lambda: "▸ " + expected in content(), "Down did not move the selection")
    attached_leaf = saved(library).pane["id"]
    client.type(ENTER)
    wait(
        client,
        lambda: saved(library).pane["agent"] == expected,
        "Enter did not attach the selected session",
    )
    assert "empty" not in saved(library).pane
    assert saved(library).pane["id"] == attached_leaf
    wait(client, lambda: expected in sidebar(viewer), "the sidebar did not show the attachment")
    assert "terminal-" + attached_leaf not in shells.run(
        "list-sessions", "-F", "#{session_name}", check=False
    ), "attaching from the chooser created a shell nobody asked for"
    if expected in online:
        wait(
            client,
            lambda: f"Demo shell: {expected}" in content("-"),
            "the attached session did not appear in the pane",
        )
    print("PASS: Down and Enter attach a session without an intermediate shell", flush=True)

    # 4. A mouse click on a session row chooses it too.
    key("t")
    wait(client, lambda: len(saved(library).space["tabs"]) == 4, "third new tab failed")
    wait(client, chooser_shown, "third chooser missing")
    pane = content_pane()
    left, top = pane_geometry(pane)
    # The capture helper strips leading blank lines, so rows are counted from
    # the title, which the chooser paints on its second line.
    lines = viewer.run("capture-pane", "-p", "-t", pane).splitlines()
    target = online[-1]
    title = next(index for index, line in enumerate(lines) if line.strip() == TITLE)
    found = next(index for index, line in enumerate(lines) if line.strip(" ▸").startswith(target))
    row = found - title + 1
    client.click(left + 4, top + row + 1)
    wait(
        client,
        lambda: saved(library).pane["agent"] == target,
        "a click on a session row did not attach it",
    )
    print("PASS: a click on a chooser row attaches that session", flush=True)

    # 5. The choice survives exit and reopen: an empty pane reopens as a chooser,
    #    a chosen shell reopens as that shell.
    key("t")
    wait(client, lambda: len(saved(library).space["tabs"]) == 5, "fourth new tab failed")
    wait(client, chooser_shown, "fourth chooser missing")
    kept_shell = first_shell
    client.close()
    client = resources.client(["--demo", "--data-dir", str(directory)])
    wait(client, lambda: client.manifest(library), "viewer did not reopen")
    runtime = json.loads(client.manifest(library).read_text())
    viewer = Tmux(runtime["viewer_socket"])
    wait(client, lambda: "Layouts saved" in sidebar(viewer), "reopened sidebar did not initialize")
    assert saved(library).pane.get("empty") is True, "the empty pane was filled by a reopen"
    wait(client, chooser_shown, "the empty pane did not reopen as a chooser")
    assert kept_shell in shells.run("list-sessions", "-F", "#{session_name}"), (
        "reopening lost the first tab's shell"
    )
    print("PASS: an empty pane reopens as a chooser after the viewer exits", flush=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-new-tab-", dir="/tmp") as directory:
        exercise(Path(directory))
