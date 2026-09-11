"""Synthetic SSH/XDG context through real viewer launch and persistent shells."""

import json
import shlex
import socket
import sys
import tempfile
from pathlib import Path

from tests.integration.support import (
    OUTLINE,
    FixtureResources,
    click_button,
    open_terminal,
    saved,
    session_in_use,
    wait,
)
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import SHELL_CONTEXT_NAMES, Tmux


def run(directory: Path) -> None:
    with FixtureResources(parent=directory) as resources:
        exercise(resources)


def exercise(resources: FixtureResources) -> None:
    directory = resources.root
    library = resources.library()
    home = directory / "home"
    home.mkdir()
    # An empty HOME still permits /etc/profile in a login shell. Isolate the
    # handoff from distro profile overrides while checking the requested flags.
    shell = directory / "controlled-shell"
    shell.write_text(
        '#!/bin/sh\nif [ "$1" = -c ]; then exec /bin/sh "$@"; fi\n'
        '[ "$#" = 2 ] && [ "$1" = -l ] && [ "$2" = -i ] || exit 64\n'
        "exec /bin/bash --noprofile --norc -i\n"
    )
    shell.chmod(0o700)
    source = resources.server("external")
    shells = Tmux(str(Path(socket_path(library, "terminals")).resolve()))
    agents = []
    forbidden = ("TMUX", "TMUX_PANE", "BACKBONE_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY")

    def context(label):
        root = directory / label
        root.mkdir()
        config = root / "config with spaces"
        config.mkdir()
        (config / "probe").write_text(label)
        agent = socket.socket(socket.AF_UNIX)
        resources.own_cleanup(agent.close)
        agents.append(agent)
        agent.bind(str(root / "agent.sock"))
        agent.listen(16)
        values = {name: str(root / name) for name in SHELL_CONTEXT_NAMES}
        values.update(SSH_AUTH_SOCK=str(root / "agent.sock"), XDG_CONFIG_HOME=str(config))
        return values

    def open_window(values):
        client = resources.client(
            ["--data-dir", str(library), "--source-socket", source.socket],
            terminal_env={
                "HOME": str(home),
                "SHELL": str(shell),
                **dict.fromkeys(forbidden, "synthetic-secret"),
                **values,
            },
        )
        wait(client, lambda: client.manifest(library), "environment viewer did not start")
        runtime = json.loads(client.manifest(library).read_text())
        viewer = Tmux(runtime["viewer_socket"])
        wait(
            client,
            lambda: "Configure…" in viewer.run("capture-pane", "-p", "-t", "%0").translate(OUTLINE),
            "environment viewer did not initialize",
        )
        return client, viewer

    def probe(client, pane, label, expected, connected):
        result = directory / (label + ".json")
        code = (
            "import json,os,socket; from pathlib import Path; "
            "env=dict(os.environ); config=env.get('XDG_CONFIG_HOME'); "
            "s=socket.socket(socket.AF_UNIX); s.settimeout(1); "
            "connected=False\n"
            "try: s.connect(env.get('SSH_AUTH_SOCK','')); connected=True\n"
            "except OSError: pass\n"
            "s.close()\n"
            f"Path({str(result)!r}).write_text(json.dumps(dict(env=env, connected=connected, "
            "config=Path(config,'probe').read_text() if config else None)))"
        )
        target = "=" + Shells.name(pane) + ":"
        wait(
            client,
            lambda: (
                Shells.name(pane)
                in shells.run("list-sessions", "-F", "#{session_name}", check=False).splitlines()
            ),
            "shell missing",
        )
        shells.run("send-keys", "-t", target, "-l", shlex.join([sys.executable, "-c", code]))
        shells.run("send-keys", "-t", target, "Enter")
        wait(client, result.exists, "shell environment probe did not complete")
        observed = json.loads(result.read_text())
        for key in forbidden:
            assert key not in observed["env"], key + " leaked into an ordinary shell"
        observed_context = {
            k: observed["env"][k] for k in SHELL_CONTEXT_NAMES if k in observed["env"]
        }
        assert observed_context == expected, {"expected": expected, "observed": observed_context}
        assert observed["connected"] is connected
        assert observed["config"] == (
            Path(expected["XDG_CONFIG_HOME"], "probe").read_text() if expected else None
        )
        return shells.run("display-message", "-p", "-t", target, "#{pane_pid}|#{pane_current_path}")

    def close_window(client, viewer):
        click_button(client, viewer, "Exit")
        wait(client, lambda: client.process.poll() is not None, "environment viewer did not exit")

    source.run(
        "-f",
        "/dev/null",
        "new-session",
        "-d",
        "-s",
        "external",
        "-e",
        f"HOME={directory}",
        "/bin/sh -i",
    )
    source.run("set-environment", "-t", "=external:", "SSH_AUTH_SOCK", "/synthetic/external-agent")
    source.run("set-environment", "-t", "=external:", "DISPLAY", "synthetic-display")
    external_before = source.run("show-environment", "-t", "=external:")
    first = context("first")
    client, viewer = open_window(first)
    original = saved(library).pane
    identity = probe(client, original, "initial", first, True)
    close_window(client, viewer)
    agents[0].close()
    Path(first["SSH_AUTH_SOCK"]).unlink()
    second = context("second")
    client, viewer = open_window(second)
    assert probe(client, original, "retained", first, False) == identity
    old_tab = saved(library).tab["id"]
    client.type(direct_sequence("new-tab"))
    wait(client, lambda: saved(library).tab["id"] != old_tab, "reopened new tab failed")
    open_terminal(client, viewer, library)
    fresh = saved(library).pane
    probe(client, fresh, "refreshed", second, True)
    # Another window with no SSH/XDG settings must not inherit an old socket
    # from the persistent library server or change the first window's shells.
    other, other_viewer = open_window({})
    old_tab = saved(library).tab["id"]
    other.type(direct_sequence("new-tab"))
    wait(other, lambda: saved(library).tab["id"] != old_tab, "context-free new tab failed")
    open_terminal(other, other_viewer, library)
    probe(other, saved(library).pane, "unset", {}, False)
    probe(client, fresh, "still-second", second, True)
    click_button(other, other_viewer, "Attach session…")
    wait(
        other,
        lambda: "external" in other_viewer.run("capture-pane", "-p", "-t", "%0").translate(OUTLINE),
        "external session missing",
    )
    lines = other_viewer.run("capture-pane", "-p", "-t", "%0").translate(OUTLINE).splitlines()
    row = next(i for i, line in enumerate(lines) if line.strip() == "external")
    other.click(3, row + 1)
    wait(
        other,
        lambda: session_in_use(source, "=external:"),
        "external attachment did not start",
    )
    assert source.run("show-environment", "-t", "=external:") == external_before
    close_window(other, other_viewer)
    close_window(client, viewer)
    assert source.run("show-environment", "-t", "=external:") == external_before
    print(
        "PASS: synthetic SSH socket and XDG config reach ordinary commands; "
        "reopen refreshes new shells, old PID/cwd/context persist, absent context "
        "stays absent, launcher secrets/private identities excluded, "
        "external attachment environment unchanged",
        flush=True,
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-env-", dir="/tmp") as directory:
        run(Path(directory))
