"""Built-in JSON editor through private PTYs and an owned viewer popup."""

import json
import re
import tempfile
from pathlib import Path

from tests.integration.support import (
    FixtureResources,
    click_button,
    open_terminal,
    saved,
    shell_attached,
    sidebar,
    wait,
)
from tmux_workspaces.application import socket_path
from tmux_workspaces.keymap import DEFAULT_KEYMAP, parse_keymap
from tmux_workspaces.shells import Shells
from tmux_workspaces.theme import parse_theme
from tmux_workspaces.tmux import Tmux


def replace(client, text):
    client.type("\x01\x1b[200~" + text + "\x1b[201~")


def standalone(resources):
    path, result = resources.root / "colors.toml", resources.root / "result.json"
    client = resources.client(
        [
            "_config-editor",
            "--config-kind",
            "colors",
            "--config-path",
            str(path),
            "--config-result",
            str(result),
        ],
        cols=100,
        rows=24,
    )
    wait(client, lambda: result.with_suffix(".ready").exists(), "built-in editor did not start")
    replace(client, "{")
    client.type("\x13")
    wait(client, lambda: b"JSON line" in client.output, "syntax error did not preserve the editor")
    assert not path.exists()
    replace(client, '{"preset":"paper"}')
    client.type("\x1a")
    client.type("\x19")
    client.resize(32, 10)
    client.type("\x13")
    assert not path.exists(), "hidden Save activated on a tiny terminal"
    client.resize(100, 24)
    # Save is a real mouse hit in the footer (one-based terminal coordinates).
    if re.search(rb"\x1b\[\?(?:[0-9]+;)*1006(?:;[0-9]+)*h", client.output):
        client.click(3, 23)
    else:
        # macOS system curses requests X10 mouse reports, not SGR. tmux normally
        # translates those for a popup; this standalone PTY has no translator.
        client.type("\x1b[M" + chr(32) + chr(35) + chr(55) + "\x1b[M" + chr(35) * 2 + chr(55))
    wait(client, lambda: result.exists(), "mouse Save did not finish")
    assert json.loads(result.read_text())["saved"]
    assert parse_theme(path.read_bytes()).preset_name() == "paper"
    print("PASS: JSON syntax errors, undo/redo, resize and mouse Save on a real PTY", flush=True)


def popup(resources):
    library = resources.library("library")
    source = resources.server()
    source.run("-f", "/dev/null", "new-session", "-d", "-s", "ordinary", "/bin/sh")
    keys, colors = resources.root / "keys.toml", resources.root / "popup-colors.toml"
    keys.write_text(DEFAULT_KEYMAP.to_toml())
    client = resources.client(
        [
            "--data-dir",
            str(library),
            "--source-socket",
            source.socket,
            "--keymap",
            str(keys),
            "--theme",
            str(colors),
        ],
    )
    wait(client, lambda: client.manifest(library), "viewer did not start")
    runtime = json.loads(client.manifest(library).read_text())
    viewer = Tmux(runtime["viewer_socket"])
    wait(client, lambda: "Configure…" in sidebar(viewer), "sidebar missing")
    if saved(library).pane.get("empty"):
        open_terminal(client, viewer, library)
    shells = Tmux(socket_path(library, "terminals"))
    target = "=" + Shells.name(saved(library).pane) + ":"
    wait(
        client, lambda: shell_attached(shells, Shells.name(saved(library).pane)), "shell not ready"
    )
    shell_pid = shells.run("display-message", "-p", "-t", target, "#{pane_pid}")

    def open_editor(label):
        client.output = b""
        click_button(client, viewer, label)
        wait(client, lambda: b"JSON editor" in client.output, "popup did not show editor")

    open_editor("Edit colors JSON…")
    replace(client, '{"preset":"paper"}')
    client.type("\x13")
    wait(client, lambda: "Colors saved and applied" in sidebar(viewer), "colors did not apply")
    assert parse_theme(colors.read_bytes()).preset_name() == "paper"

    open_editor("Edit colors JSON…")
    replace(client, '{"panel":"CANCEL_SENTINEL"}')
    client.type("\x1b")
    wait(client, lambda: b"Unsaved edits" in client.output, "Cancel did not ask about the draft")
    client.type("\x1b")
    wait(client, lambda: "Configure…" in sidebar(viewer), "Cancel did not return to viewer")
    assert parse_theme(colors.read_bytes()).preset_name() == "paper"
    history = shells.run("capture-pane", "-p", "-J", "-S", "-", "-t", target)
    assert "CANCEL_SENTINEL" not in history, "editor text leaked to a workspace shell"

    open_editor("Edit shortcuts JSON…")
    replace(client, '{"prefix":"C-a"}')
    client.type("\x13")
    wait(
        client, lambda: "Shortcuts saved" in sidebar(viewer), "shortcut save did not offer refresh"
    )
    assert parse_keymap(keys.read_bytes()).prefix == "C-a"
    click_button(client, viewer, "Refresh viewer now")
    wait(
        client,
        lambda: (
            client.manifest(library)
            and json.loads(client.manifest(library).read_text())["viewer_socket"] != viewer.socket
        ),
        "saved shortcuts did not refresh the viewer",
    )
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    wait(client, lambda: "Configure…" in sidebar(viewer), "refreshed sidebar missing")
    assert viewer.run("show-options", "-gv", "prefix") == "C-a"
    assert shells.run("display-message", "-p", "-t", target, "#{pane_pid}") == shell_pid
    assert source.run("has-session", "-t", "=ordinary:", check=False) == ""
    print(
        "PASS: built-in popup, live colors, Cancel without shell input, saved keys and refresh",
        flush=True,
    )


if __name__ == "__main__":
    with (
        tempfile.TemporaryDirectory(prefix="tw-json-test-", dir="/tmp") as root,
        FixtureResources(parent=Path(root)) as resources,
    ):
        standalone(resources)
        popup(resources)
