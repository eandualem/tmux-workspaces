"""Launch the real app through its tmux plugin on a disposable outer server."""

from __future__ import annotations

import json
import shlex
import tempfile
from pathlib import Path

from tests.integration.support import (
    OUTLINE,
    FixtureResources,
    OuterClient,
    click_button,
    content_panes,
    saved,
    wait,
)
from tmux_workspaces.application import socket_path
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux


def run(directory: Path) -> None:
    with FixtureResources(parent=directory) as resources:
        exercise(resources)


def exercise(resources: FixtureResources) -> None:
    directory = resources.root
    source = resources.server("source")
    library = resources.library()
    shell_socket = socket_path(library, "terminals")
    shells = Tmux(shell_socket)

    def manifest():
        return next((library / "windows").glob("*/runtime.json"), None)

    def identities():
        return source.run("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}:#{session_name}")

    def options():
        return (
            source.run("show-options", "-g"),
            source.run("show-options", "-gw"),
            source.run("show-options", "-p", "-t", "=host:0"),
        )

    source.run(
        "-f",
        "/dev/null",
        "new-session",
        "-d",
        "-s",
        "host",
        "-e",
        f"HOME={directory}",
        "-c",
        str(directory),
        "/bin/sh",
    )
    source.run("set-environment", "-g", "SHELL", "/bin/sh")
    source.run("set-environment", "-g", "HOME", str(directory))
    source.run("new-session", "-d", "-s", "ordinary-source", "/bin/sh")
    source.run("set-option", "-g", "@tmux-workspaces-data-dir", str(library))
    original_identities = identities()
    original_options = options()
    plugin = Path(__file__).resolve().parents[2] / "tmux-workspaces.tmux"
    source.run("run-shell", shlex.quote(str(plugin)).replace("#", "##"))
    assert options() == original_options
    assert identities() == original_identities
    client = resources.own_client(OuterClient(source.socket))
    wait(client, lambda: source.run("list-clients"), "outer tmux client did not attach")

    def open_viewer():
        client.prefix_key(source, "W")
        wait(client, manifest, "Prefix W failed to launch the viewer")
        runtime = json.loads(manifest().read_text())
        assert Path(runtime["source_socket"]).resolve() == Path(source.socket).resolve()
        assert len({runtime["source_socket"], runtime["viewer_socket"], shell_socket}) == 3
        viewer = Tmux(runtime["viewer_socket"])
        wait(
            client,
            lambda: "Detach" in viewer.run("capture-pane", "-p", "-t", "%0").translate(OUTLINE),
            "plugin viewer sidebar failed to initialize",
        )
        assert "workspaces" in source.run("list-windows", "-t", "=host:", "-F", "#{window_name}")
        return viewer

    def exit_viewer(viewer):
        click_button(client, viewer, "Exit")
        wait(client, lambda: manifest() is None, "viewer did not clean up on exit")
        wait(
            client,
            lambda: identities() == original_identities,
            "plugin window did not close or changed an outer pane",
        )
        assert options() == original_options
        assert client.process.poll() is None, "exit detached the original outer client"

    viewer = open_viewer()
    initial = saved(library)
    assert len(initial.space["tabs"]) == 1 and initial.pane["agent"] is None
    terminal = "=" + Shells.name(initial.pane) + ":"
    wait(client, lambda: shells.run("has-session", "-t", terminal) == "", "missing shell")
    shell_pid = shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
    client.type("export WSV_PLUGIN=still_here; printf 'PLUGIN_SHELL_OK\\n'\r")
    wait(
        client,
        lambda: "PLUGIN_SHELL_OK" in shells.run("capture-pane", "-p", "-t", terminal),
        "nested viewer did not receive ordinary shell input",
    )
    client.type("\x07t")
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "Ctrl-g t failed")
    click_button(client, viewer, initial.tab["name"])
    wait(client, lambda: saved(library).tab["id"] == initial.tab["id"], "tab mouse click failed")
    click_button(client, viewer, "Attach session…")
    click_button(client, viewer, "ordinary-source")
    client.type("printf 'PLUGIN_SOURCE_OK\\n'\r")
    wait(
        client,
        lambda: "PLUGIN_SOURCE_OK" in source.run("capture-pane", "-p", "-t", "=ordinary-source:"),
        "plugin source chooser did not attach the separate source session",
    )
    assert saved(library).tab["name"] == initial.tab["name"]
    click_button(client, viewer, "Tab actions…")
    click_button(client, viewer, "Return pane to shell")
    click_button(client, viewer, "Attach session…")
    click_button(client, viewer, "host")
    wait(
        client,
        lambda: (
            "This session hosts the viewer."
            in viewer.run("capture-pane", "-p", "-t", content_panes(viewer)[1])
        ),
        "plugin allowed a recursive attachment to its hosting session",
    )
    assert len(source.run("list-clients").splitlines()) == 1
    click_button(client, viewer, "Tab actions…")
    click_button(client, viewer, "Return pane to shell")
    exit_viewer(viewer)
    assert shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}") == shell_pid
    viewer = open_viewer()
    client.type("printf 'PLUGIN_REOPEN:%s\\n' \"$WSV_PLUGIN\"\r")
    wait(
        client,
        lambda: "PLUGIN_REOPEN:still_here" in shells.run("capture-pane", "-p", "-t", terminal),
        "plugin reopen failed to preserve the ordinary shell",
    )
    assert shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}") == shell_pid
    exit_viewer(viewer)
    print(
        "PASS: real plugin run-shell + Prefix W launch, nested shell/shortcuts/mouse, "
        "generic source attachment and host recursion refusal, exit/reopen persistence, "
        "unchanged outer panes/options."
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-plugin-smoke-", dir="/tmp") as directory:
        run(Path(directory))
