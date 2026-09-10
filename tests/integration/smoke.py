"""Real mouse/PTY exercise against disposable demo shells, never real agents."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tmux_workspaces.application import socket_path
from tmux_workspaces.model import leaves
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux

from .support import FixtureResources, click_attach, click_button, open_terminal, saved, wait


def exercise(directory: Path):
    with FixtureResources(parent=directory) as resources:
        _exercise(resources)


def _exercise(resources: FixtureResources):
    directory = resources.root
    library = resources.library("demo", demo=True)
    client = resources.client(["--demo", "--data-dir", str(directory)])
    shells = Tmux(socket_path(library, "terminals"))
    wait(client, lambda: client.manifest(library), "viewer did not start")
    runtime = json.loads(client.manifest(library).read_text())
    viewer, source = Tmux(runtime["viewer_socket"]), Tmux(runtime["source_socket"])

    def sidebar():
        return viewer.run("capture-pane", "-p", "-t", "%0")

    def panes():
        return viewer.run("list-panes", "-F", "#{pane_id} #{@viewer_agent}").splitlines()

    def button(text):
        click_button(client, viewer, text)

    def key(text):
        client.type("\x07" + text)

    def attached(target):
        return (
            int(shells.run("display-message", "-p", "-t", target, "#{session_attached}") or "0") > 0
        )

    wait(client, lambda: "Layouts saved" in sidebar(), "sidebar failed to initialize")
    initial = saved(library)
    assert len(initial.space["tabs"]) == 1
    assert initial.pane["agent"] is None
    assert "manager" not in sidebar() and "builder" not in sidebar()
    terminal = "=" + Shells.name(initial.pane) + ":"
    wait(client, lambda: attached(terminal), "ordinary shell not attached")
    client.type(
        "export WSV_CHECK=still_here; cd /tmp; "
        'printf \'STATE:%s:%s:%s\\n\' "$WSV_CHECK" "${TMUX-unset}" "${TMUX_PANE-unset}"\r'
    )
    assert "STATE:still_here:unset:unset" in shells.run("capture-pane", "-p", "-t", terminal)
    shell_pid = shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    key("r")
    client.type("\x15Organizers")
    button("Save name")
    assert saved(library).tab["name"] == "Organizers"
    button("+ Tab")
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "new tab button failed")
    assert saved(library).tab["name"].startswith("Tab ")
    key("p")
    wait(
        client,
        lambda: saved(library).tab["name"] == "Organizers" and attached(terminal),
        "tab shortcut failed",
    )
    client.type('printf \'PERSISTED:%s:%s\\n\' "$WSV_CHECK" "$PWD"\r')
    assert "PERSISTED:still_here:/tmp" in shells.run("capture-pane", "-p", "-t", terminal)
    assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    identities = source.run(
        "list-sessions", "-F", "#{session_name}:#{session_id}:#{session_created}"
    )
    # Attachment replaces only this pane, leaving its shell parked and tab name intact.
    button("Attach session…")
    button("manager")
    wait(client, lambda: " manager" in "\n".join(panes()), "agent attachment failed")
    assert saved(library).tab["name"] == "Organizers"
    client.type("printf 'VIEWER_INPUT_MANAGER\\n'\r")
    assert "VIEWER_INPUT_MANAGER" in source.run("capture-pane", "-p", "-t", "=manager:")
    assert "VIEWER_INPUT_MANAGER" not in source.run("capture-pane", "-p", "-t", "=builder:")
    client.type("seq 1 160\r")
    client.type("\x1b[<64;50;10M")
    wait(
        client,
        lambda: source.run("display-message", "-p", "-t", "=manager:", "#{pane_in_mode}") == "1",
        "inner scrollback failed",
    )
    client.type("q")
    button("Tab actions…")
    button("Return pane to shell")
    wait(client, lambda: attached(terminal), "return to original shell failed")
    assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    # Click and keyboard splits open choosers inside the same tab; each is
    # given a terminal here, which starts in the neighbour's directory.
    button("Split →")
    wait(client, lambda: len(panes()) == 3, "split-right failed")
    assert saved(library).pane["agent"] is None
    assert saved(library).pane.get("empty") is True, "a split opened a shell unasked"
    assert Path(saved(library).pane["cwd"]).resolve() == Path("/tmp").resolve()
    open_terminal(client, viewer, library)
    key('"')
    wait(client, lambda: len(panes()) == 4, "split-below shortcut failed")
    open_terminal(client, viewer, library)
    key("o")
    key("h")
    wait(client, lambda: len(panes()) == 5, "four-pane layout failed")
    open_terminal(client, viewer, library)
    assert len(saved(library).space["tabs"]) == 2
    assert [p["agent"] for p in leaves(saved(library).tab["tree"])] == [None] * 4
    target_leaf = leaves(saved(library).tab["tree"])[0]["id"]
    assert target_leaf != saved(library).pane["id"]
    click_attach(client, viewer, target_leaf)
    button("builder")
    wait(
        client,
        lambda: (
            saved(library).pane["id"] == target_leaf and saved(library).pane["agent"] == "builder"
        ),
        "sidebar attachment targeted the wrong pane",
    )
    key("o")
    key("a")
    button("reviewer")
    assert sorted(p["agent"] for p in leaves(saved(library).tab["tree"]) if p["agent"]) == [
        "builder",
        "reviewer",
    ]
    button("Focus")
    wait(client, lambda: len(panes()) == 2, "focus failed")
    button("Next →")
    button("Layout")
    wait(client, lambda: len(panes()) == 5, "layout restore failed")
    client.resize(80, 24)
    wait(
        client,
        lambda: "Narrow: focus" in sidebar() and len(panes()) == 2,
        "laptop focus failed",
    )
    client.resize(160, 38)
    wait(client, lambda: len(panes()) == 5, "wide layout did not return")
    assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    print(
        "PASS: neutral tabs, naming, shell state, optional attachments, four panes, "
        "mouse/shortcuts, resize and scrollback",
        flush=True,
    )
    button("Workspaces…")
    button("New workspace")
    client.type("Research")
    button("Save name")
    wait(client, lambda: "No tabs yet" in sidebar(), "new workspace failed")
    key("t")
    wait(client, lambda: saved(library).tab is not None, "new-tab shortcut failed")
    open_terminal(client, viewer, library)
    key("r")
    client.type("\x15Notebook")
    button("Save name")
    note_terminal = "=" + Shells.name(saved(library).pane) + ":"
    wait(client, lambda: attached(note_terminal), "notebook shell missing")
    # Foreground program survives workspace switching, terminal close and reopen.
    client.type("echo FOREGROUND_READY; read answer; printf 'RESUMED:%s\\n' \"$answer\"\r")
    wait(
        client,
        lambda: "FOREGROUND_READY" in shells.run("capture-pane", "-p", "-t", note_terminal),
        "foreground program missing",
    )
    key("w")
    button("Workspace 1")
    wait(client, lambda: "Organizers" in sidebar(), "workspace switching failed")
    key("w")
    button("Research")
    button("Exit")
    wait(client, lambda: client.process.poll() is not None, "viewer did not exit")
    assert shells.run("list-sessions", check=False)
    client.close()
    client = resources.client(["--demo", "--data-dir", str(directory)])
    wait(client, lambda: client.manifest(library), "reopen failed")
    runtime = json.loads(client.manifest(library).read_text())
    viewer, source = Tmux(runtime["viewer_socket"]), Tmux(runtime["source_socket"])
    wait(
        client,
        lambda: "Notebook" in sidebar() and attached(note_terminal),
        "saved terminal not restored",
    )
    client.type("continued_after_reopen\r")
    wait(
        client,
        lambda: (
            "RESUMED:continued_after_reopen"
            in shells.run("capture-pane", "-p", "-t", note_terminal)
        ),
        "foreground program did not survive reopen",
    )
    button("Attach session…")
    button("notes")
    wait(
        client,
        lambda: "session offline" in viewer.run("capture-pane", "-p", "-t", "viewer:.1"),
        "offline view missing",
    )
    assert saved(library).tab["name"] == "Notebook"
    button("Tab actions…")
    button("Close tab")
    wait(client, lambda: "No tabs yet" in sidebar(), "close-tab failed")
    assert Shells.name(
        {"id": note_terminal.removeprefix("=terminal-").removesuffix(":")}
    ) not in shells.run("list-sessions", "-F", "#{session_name}")
    # The original demo identities were kept until the first demo launcher exited.
    assert len(identities.splitlines()) == 5
    button("Exit")
    wait(client, lambda: client.process.poll() is not None, "final exit failed")
    print(
        "PASS: workspace persistence, live foreground command across exit/reopen, "
        "offline association and explicit shell close",
        flush=True,
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="workspace-viewer-smoke-") as directory:
        exercise(Path(directory))
