"""Synthetic SSH/XDG context through real viewer launch and persistent shells."""

import json
import shlex
import socket
import sys
import tempfile
from pathlib import Path

from tests.integration.support import Client, click_button, saved, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import SHELL_CONTEXT_NAMES, Tmux


def run(directory: Path) -> None:
    library = directory / "library"
    home = directory / "home"
    home.mkdir()
    source = Tmux(str(directory / "external.sock"))
    shells = Tmux(str(Path(socket_path(library, "terminals")).resolve()))
    clients = []
    agents = []
    forbidden = ("TMUX", "TMUX_PANE", "BACKBONE_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY")

    def context(label):
        root = directory / label
        root.mkdir()
        config = root / "config with spaces"
        config.mkdir()
        (config / "probe").write_text(label)
        agent = socket.socket(socket.AF_UNIX)
        agent.bind(str(root / "agent.sock"))
        agent.listen(16)
        agents.append(agent)
        values = {name: str(root / name) for name in SHELL_CONTEXT_NAMES}
        values.update(SSH_AUTH_SOCK=str(root / "agent.sock"), XDG_CONFIG_HOME=str(config))
        return values

    def open_window(values):
        client = Client(
            ["--data-dir", str(library), "--source-socket", source.socket],
            terminal_env={
                "HOME": str(home),
                **dict.fromkeys(forbidden, "synthetic-secret"),
                **values,
            },
        )
        clients.append(client)
        wait(client, lambda: client.manifest(library), "environment viewer did not start")
        runtime = json.loads(client.manifest(library).read_text())
        viewer = Tmux(runtime["viewer_socket"])
        wait(
            client,
            lambda: "Layouts saved" in viewer.run("capture-pane", "-p", "-t", "%0"),
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
        assert {
            k: observed["env"][k] for k in SHELL_CONTEXT_NAMES if k in observed["env"]
        } == expected
        assert observed["connected"] is connected
        assert observed["config"] == (
            Path(expected["XDG_CONFIG_HOME"], "probe").read_text() if expected else None
        )
        return shells.run("display-message", "-p", "-t", target, "#{pane_pid}|#{pane_current_path}")

    def close_window(client, viewer):
        click_button(client, viewer, "Exit")
        wait(client, lambda: client.process.poll() is not None, "environment viewer did not exit")

    try:
        source.run("-f", "/dev/null", "new-session", "-d", "-s", "external", "/bin/sh -i")
        source.run(
            "set-environment", "-t", "=external:", "SSH_AUTH_SOCK", "/synthetic/external-agent"
        )
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
        fresh = saved(library).pane
        probe(client, fresh, "refreshed", second, True)
        # Another window with no SSH/XDG settings must not inherit an old socket
        # from the persistent library server or change the first window's shells.
        other, other_viewer = open_window({})
        old_tab = saved(library).tab["id"]
        other.type(direct_sequence("new-tab"))
        wait(other, lambda: saved(library).tab["id"] != old_tab, "context-free new tab failed")
        probe(other, saved(library).pane, "unset", {}, False)
        probe(client, fresh, "still-second", second, True)
        click_button(other, other_viewer, "Attach session…")
        wait(
            other,
            lambda: "external" in other_viewer.run("capture-pane", "-p", "-t", "%0"),
            "external session missing",
        )
        lines = other_viewer.run("capture-pane", "-p", "-t", "%0").splitlines()
        row = next(i for i, line in enumerate(lines) if line.strip() == "external")
        other.click(3, row + 1)
        wait(
            other,
            lambda: (
                source.run("display-message", "-p", "-t", "=external:", "#{session_attached}")
                == "1"
            ),
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
    finally:
        for client in clients:
            client.close()
        for agent in agents:
            agent.close()
        for tmux in (source, shells):
            tmux.run("kill-server", check=False)
        Path(shells.socket + ".viewer-lock").unlink(missing_ok=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-env-", dir="/tmp") as directory:
        run(Path(directory))
