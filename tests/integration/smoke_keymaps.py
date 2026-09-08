"""User keymaps across real PTYs, isolated shells and a normal hosting tmux."""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

from tests.integration.support import Client, FixtureResources, click_button, saved, sidebar, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux

CONFIG = """prefix = 'C-a'
[bindings]
new-tab = ['u']
rename-tab = ['e']
next-tab = ['j']
previous-tab = ['k']
quit = ['q']
close-pane = []
[direct]
new-tab = ['super+alt+t']
rename-tab = []
"""


def terminal(library: Path) -> str:
    return "=" + Shells.name(saved(library).pane) + ":"


def ready(library: Path, viewer: Tmux, shells: Tmux) -> bool:
    name = Shells.name(saved(library).pane)
    return (
        any(
            line.startswith("1 ") and name in line
            for line in viewer.run(
                "list-panes", "-F", "#{pane_active} #{pane_start_command}"
            ).splitlines()
        )
        and int(
            shells.run("display-message", "-p", "-t", "=" + name + ":", "#{session_attached}") or 0
        )
        > 0
    )


def launch(resources: FixtureResources, library: Path, arguments: list[str]):
    directory = resources.root
    client = resources.client(
        [
            "--data-dir",
            str(library),
            "--source-socket",
            str(directory / "absent.sock"),
            *arguments,
        ],
        terminal_env={"HOME": str(directory), "XDG_CONFIG_HOME": str(directory / "config")},
    )
    wait(client, lambda: client.manifest(library), "keymap viewer did not launch")
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    shells = Tmux(socket_path(library, "terminals"))
    wait(client, lambda: ready(library, viewer, shells), "keymap shell did not attach")
    return client, viewer, shells


def marker() -> tuple[str, str]:
    token = os.urandom(8).hex()
    return "printf 'KEYMAP_%s\\n' " + token + "\r", "KEYMAP_" + token


def assert_routed(client: Client, shells: Tmux, destinations: list[tuple[str, str]]) -> None:
    def correct():
        captures = {
            "=" + name + ":": shells.run("capture-pane", "-S", "-", "-p", "-t", "=" + name + ":")
            for name in shells.run("list-sessions", "-F", "#{session_name}").splitlines()
        }
        return all(
            [target for target, text in captures.items() if token in text] == [expected]
            and captures[expected].count(token) == 1
            for token, expected in destinations
        )

    wait(client, correct, "custom shortcut input was lost, duplicated or reached the wrong shell")


def raw_prefix(directory: Path, library: Path, client: Client, viewer: Tmux, shells: Tmux) -> None:
    recording = directory / "prefix.bytes"
    started = directory / "prefix.ready"
    program = directory / "prefix_reader.py"
    program.write_text(
        "import os, pathlib, termios, tty\n"
        "old = termios.tcgetattr(0)\n"
        "tty.setraw(0)\n"
        "try:\n"
        f" pathlib.Path({str(started)!r}).touch()\n"
        " while True:\n"
        "  data = os.read(0, 4096)\n"
        "  if not data or b'\\x03' in data: break\n"
        f"  with open({str(recording)!r}, 'ab') as output: output.write(data)\n"
        "finally:\n"
        " termios.tcsetattr(0, termios.TCSANOW, old)\n"
    )
    wait(client, lambda: ready(library, viewer, shells), "prefix probe shell not focused")
    client.type(shlex.join([sys.executable, str(program)]) + "\r")
    wait(client, started.exists, "raw prefix reader did not start")
    client.type("\x01\x1b")
    client.pump(0.3)
    assert not recording.exists(), "prefix Escape leaked bytes into the ordinary terminal"
    client.type("\x01\x01")
    wait(client, recording.exists, "double prefix did not deliver the literal prefix")
    client.pump(0.3)
    assert recording.read_bytes() == b"\x01", "double prefix delivered extra bytes"
    # A removed direct binding must not open rename. A raw application avoids
    # making assumptions about how different shell editors handle unknown CSI.
    disabled_sequence = direct_sequence("rename-tab")
    client.type(disabled_sequence)
    wait(
        client,
        lambda: recording.read_bytes() == b"\x01" + disabled_sequence.encode(),
        "disabled direct rename was not delivered intact to the raw application",
    )
    assert "Type a name" not in sidebar(viewer), "disabled direct rename still runs"
    assert viewer.run("display-message", "-p", "#{pane_id}") != "%0"
    client.type("\x03")


def standalone(directory: Path) -> None:
    with FixtureResources(parent=directory) as resources:
        _standalone(resources)


def _standalone(resources: FixtureResources) -> None:
    directory = resources.root
    library = resources.library("custom")
    config = directory / "keys.toml"
    config.write_text(CONFIG)
    client, viewer, shells = launch(resources, library, ["--keymap", str(config)])
    first = terminal(library)
    first_pid = shells.run("display-message", "-p", "-t", first, "#{pane_pid}")
    assert viewer.run("show-option", "-gv", "prefix") == "C-a"
    destinations = []
    command, token = marker()
    client.type(command)
    destinations.append((token, first))
    assert_routed(client, shells, destinations)

    # One write includes the custom action followed immediately by text.
    command, token = marker()
    client.type("\x01u" + command)
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "custom new-tab failed")
    second_target = terminal(library)
    destinations.append((token, second_target))
    assert first != second_target
    assert_routed(client, shells, destinations)
    client.type("\x01eCustom name\r")
    wait(client, lambda: saved(library).tab["name"] == "Custom name", "custom rename failed")
    wait(client, lambda: ready(library, viewer, shells), "rename did not return shell focus")
    client.type("\x01eCancelled draft\x1b")
    wait(client, lambda: "Type a name" not in sidebar(viewer), "rename Escape did not cancel")
    assert saved(library).tab["name"] == "Custom name"
    assert "Cancelled draft" not in shells.run("capture-pane", "-p", "-t", second_target)

    packets = []
    for key, target in (("k", first), ("j", second_target), ("k", first), ("j", second_target)):
        command, token = marker()
        packets.append("\x01" + key + command)
        destinations.append((token, target))
    client.type("".join(packets))
    assert_routed(client, shells, destinations)
    assert first_pid == shells.run("display-message", "-p", "-t", first, "#{pane_pid}")

    # Replacing one action's aliases must remove both defaults, including
    # tmux's inherited c binding, which otherwise creates a native window.
    for key in ("t", "c", "x"):
        assert not viewer.run("list-keys", "-T", "prefix", key, check=False), (
            "removed prefix binding remains installed: " + key
        )
    client.type("\x01t\x01c\x01x")
    client.pump(0.3)
    assert len(saved(library).space["tabs"]) == 2
    assert len(viewer.run("list-windows").splitlines()) == 1
    assert len(viewer.run("list-panes").splitlines()) == 2
    raw_prefix(directory, library, client, viewer, shells)

    click_button(client, viewer, "Shortcuts")
    original_help = sidebar(viewer)
    new_tab_help = next(line.strip() for line in original_help.splitlines() if "New tab" in line)
    assert new_tab_help == "u New tab", new_tab_help
    assert "Close pane" not in original_help, "disabled prefix action remains in shortcut help"
    client.type("\x1b")
    config.write_text(CONFIG.replace("['u']", "['y']"))
    click_button(client, viewer, "Shortcuts")
    assert sidebar(viewer) == original_help, "editing config drifted the running viewer's help"
    client.type("\x1b")
    client.type("\x01u")
    wait(client, lambda: len(saved(library).space["tabs"]) == 3, "running keymap was not frozen")

    # Another viewer takes the new snapshot without changing the first one.
    other_library = resources.library("second")
    second, other_viewer, other_shells = launch(resources, other_library, ["--keymap", str(config)])
    second.type("\x01y")
    wait(second, lambda: len(saved(other_library).space["tabs"]) == 2, "new snapshot not loaded")
    assert not other_viewer.run("list-keys", "-T", "prefix", "u", check=False)
    unchanged_target = terminal(other_library)
    command, token = marker()
    second.type("\x01u" + command)
    assert_routed(second, other_shells, [(token, unchanged_target)])
    assert len(saved(other_library).space["tabs"]) == 2
    second.type("\x01q")
    wait(second, lambda: second.process.poll() is not None, "custom quit did not exit")
    assert second.process.returncode == 0
    client.type("\x01q")
    wait(client, lambda: client.process.poll() is not None, "first custom viewer did not exit")
    assert client.process.returncode == 0
    assert first_pid == shells.run("display-message", "-p", "-t", first, "#{pane_pid}")

    # Defaults reopen the same arrangement and live shells independently of
    # the earlier window's customized snapshot.
    default_config = directory / "config" / "tmux-workspaces" / "keymap.toml"
    default_config.parent.mkdir(parents=True)
    default_config.write_text("this is intentionally invalid TOML [")
    defaults, default_viewer, _ = launch(resources, library, ["--no-keymap"])
    assert default_viewer.run("show-option", "-gv", "prefix") == "C-g"
    count = len(saved(library).space["tabs"])
    defaults.type("\x07t")
    wait(
        defaults,
        lambda: len(saved(library).space["tabs"]) == count + 1,
        "default t not restored",
    )
    defaults.type("\x07c")
    wait(
        defaults,
        lambda: len(saved(library).space["tabs"]) == count + 2,
        "default c not restored",
    )
    assert first_pid == shells.run("display-message", "-p", "-t", first, "#{pane_pid}")
    defaults.type("\x07d")
    wait(defaults, lambda: defaults.process.poll() is not None, "default exit did not restore")
    assert defaults.process.returncode == 0
    assert not (directory / "absent.sock").exists()
    print(
        "PASS: custom prefix/action same-write and burst routing, rename cancellation, "
        "removed aliases/direct bindings, Escape and literal prefix, frozen window maps "
        "and help, fresh defaults and ordinary shell PID preservation",
        flush=True,
    )


def nested(directory: Path) -> None:
    with FixtureResources(parent=directory) as resources:
        _nested(resources)


def _nested(resources: FixtureResources) -> None:
    directory = resources.root
    library = resources.library("nested")
    config = directory / "nested.toml"
    config.write_text(CONFIG)
    outer = resources.server("outer")
    outer.run(
        "-f",
        "/dev/null",
        "new-session",
        "-d",
        "-s",
        "host",
        "-e",
        f"HOME={directory}",
        "/bin/sh",
    )
    outer.run("bind-key", "u", "display-message", "custom host binding")
    identities = outer.run("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}:#{session_name}")
    bindings = outer.run("list-keys")
    options = outer.run("show-options", "-g")
    # The ordinary host retains its default Ctrl-b prefix and bindings.
    # Launch this fixture in a new outer window, with a disposable HOME.
    command = shlex.join(
        [
            "env",
            "HOME=" + str(directory),
            "XDG_CONFIG_HOME=" + str(directory / "config"),
            "SHELL=/bin/sh",
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "run"),
            "--data-dir",
            str(library),
            "--keymap",
            str(config),
        ]
    )
    outer.run("new-window", "-t", "=host:", "-n", "viewer", command)
    client = resources.client(
        [],
        terminal_env={"HOME": str(directory), "XDG_CONFIG_HOME": str(directory / "config")},
        launcher=["tmux", "-S", outer.socket, "attach-session", "-t", "=host:"],
    )

    def manifest():
        return next((library / "windows").glob("*/runtime.json"), None)

    wait(client, manifest, "nested customized viewer did not launch")
    viewer = Tmux(json.loads(manifest().read_text())["viewer_socket"])
    shells = Tmux(socket_path(library, "terminals"))
    wait(client, lambda: ready(library, viewer, shells), "nested custom shell not ready")
    first = terminal(library)
    first_pid = shells.run("display-message", "-p", "-t", first, "#{pane_pid}")
    command, token = marker()
    client.type("\x01u" + command)
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "outer tmux ate custom prefix")
    assert_routed(client, shells, [(token, terminal(library))])
    client.type("\x01eNested custom\r")
    wait(client, lambda: saved(library).tab["name"] == "Nested custom", "nested rename failed")
    client.type("\x01q")
    wait(client, lambda: manifest() is None, "nested custom quit did not close viewer")
    wait(
        client,
        lambda: (
            outer.run("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}:#{session_name}")
            == identities
        ),
        "custom viewer changed ordinary host panes",
    )
    assert outer.run("list-keys") == bindings
    assert outer.run("show-options", "-g") == options
    assert first_pid == shells.run("display-message", "-p", "-t", first, "#{pane_pid}")
    assert client.process.poll() is None
    print(
        "PASS: custom viewer keys cross an ordinary hosting tmux with host bindings, "
        "options, processes and persistent ordinary shells unchanged",
        flush=True,
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-keymaps-smoke-", dir="/tmp") as directory:
        standalone(Path(directory))
        nested(Path(directory))
