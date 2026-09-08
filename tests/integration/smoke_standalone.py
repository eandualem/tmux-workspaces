"""Standalone launcher, generic attachments and reconnection on private tmux servers."""

from __future__ import annotations

import fcntl
import json
import os
import pty
import struct
import subprocess
import tempfile
import termios
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.integration.support import Client, click_button, saved, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux, clean_env


class StandaloneClient(Client):
    """Use the public launcher while retaining the existing PTY/mouse test helpers."""

    def __init__(self, arguments: list[str], terminal_env: dict[str, str]):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 38, 160, 0, 0))
        env = clean_env() | {
            "TERM": "xterm-256color",
            "LANG": "en_US.UTF-8",
            "SHELL": "/bin/sh",
        }
        self.process = subprocess.Popen(
            [str(Path(__file__).resolve().parents[2] / "run"), *arguments],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env | terminal_env,
            start_new_session=True,
        )
        os.close(slave)
        self.output = b""


def run(directory: Path) -> None:
    library = directory / "library"
    source = Tmux(str(Path(socket_path(directory, "standalone-source")).resolve()))
    alternate = Tmux(str(Path(socket_path(directory, "standalone-alternate")).resolve()))
    shells = Tmux(str(Path(socket_path(library, "terminals")).resolve()))
    clients: list[StandaloneClient] = []
    requests: list[str] = []
    broad_name = "Ops [α] 'x'"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = b'{"items": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    poison = directory / "poison-backbone"
    poison.mkdir()
    # Reading either file as Backbone configuration should fail. The HTTP trap
    # also detects an accidental fallback to the environment's Backbone port.
    (poison / ".env").write_text('BACKBONE_API_KEY="unterminated\n')
    (poison / "backbone.db").write_bytes(b"This is deliberately not a SQLite database.")
    env = {
        "BACKBONE_DATA_DIR": str(poison),
        "BACKBONE_PORT": str(server.server_port),
    }

    def create(tmux: Tmux, name: str) -> None:
        tmux.run("-f", "/dev/null", "new-session", "-d", "-s", name, "-n", "shell", "/bin/sh -i")
        tmux.run("set-option", "-t", "=" + name + ":", "status", "off")

    def identities(tmux: Tmux) -> str:
        return tmux.run("list-sessions", "-F", "#{session_id}:#{session_created}:#{session_name}")

    def attached(tmux: Tmux, target: str) -> bool:
        return int(tmux.run("display-message", "-p", "-t", target, "#{session_attached}") or 0) > 0

    def capture(tmux: Tmux, target: str) -> str:
        return tmux.run("capture-pane", "-p", "-t", target)

    def open_window(tmux: Tmux):
        client = StandaloneClient(["--data-dir", str(library), "--source-socket", tmux.socket], env)
        clients.append(client)
        wait(client, lambda: client.manifest(library), "standalone launcher did not start")
        manifest = client.manifest(library)
        runtime = json.loads(manifest.read_text())
        viewer = Tmux(runtime["viewer_socket"])
        assert runtime["source_socket"] == tmux.socket
        assert str(Path(runtime["shell_socket"]).resolve()) == shells.socket
        wait(
            client,
            lambda: "Layouts saved" in capture(viewer, "%0"),
            "standalone sidebar did not initialize with poisoned Backbone configuration",
        )
        return client, viewer, manifest

    try:
        for name in ("dev", "dev extra", "elsewhere", broad_name):
            create(source, name)
        # A same-named window elsewhere must never win over the intended session.
        source.run("new-window", "-t", "=elsewhere:", "-n", "dev", "/bin/sh -i")
        original_identities = identities(source)
        client, viewer, manifest = open_window(source)

        def button(text: str) -> None:
            click_button(client, viewer, text)

        def attach(name: str) -> None:
            button("Attach session…")
            wait(
                client,
                lambda: name in [line.strip() for line in capture(viewer, "%0").splitlines()],
                "generic chooser omitted exact session name: " + name,
            )
            lines = capture(viewer, "%0").splitlines()
            row = next(i for i, line in enumerate(lines) if line.strip() == name)
            top = int(viewer.run("display-message", "-p", "-t", "%0", "#{pane_top}"))
            client.click(3, row + top + 1)
            wait(
                client,
                lambda: saved(library).pane["agent"] == name and attached(source, "=" + name + ":"),
                "generic attachment failed: " + name,
            )
            assert saved(library).pane["source_socket"] == source.socket
            assert saved(library).tab["name"] == "My tools"

        initial = saved(library)
        assert len(initial.space["tabs"]) == 1
        assert initial.pane["agent"] is None
        assert "Backbone" not in capture(viewer, "%0")
        terminal = "=" + Shells.name(initial.pane) + ":"
        wait(client, lambda: attached(shells, terminal), "fresh ordinary terminal missing")
        shell_pid = shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
        client.type("export STANDALONE_STATE=retained; cd /tmp\r")
        client.type("\x07r")
        client.type("\x15My tools")
        button("Save name")
        assert saved(library).tab["name"] == "My tools"

        attach("dev")
        assert not attached(shells, terminal), "attachment left the parked shell client attached"
        client.type("printf 'EXACT_SESSION_INPUT\\n'\r")
        wait(
            client,
            lambda: "EXACT_SESSION_INPUT" in capture(source, "=dev:"),
            "input did not reach exact session",
        )
        assert "EXACT_SESSION_INPUT" not in capture(source, "=dev extra:")
        assert "EXACT_SESSION_INPUT" not in capture(source, "=elsewhere:dev")
        button("Tab actions…")
        button("Return pane to shell")
        wait(client, lambda: attached(shells, terminal), "parked shell did not return")
        assert saved(library).pane.get("source_socket") is None
        assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
        client.type('printf \'PARKED:%s:%s\\n\' "$STANDALONE_STATE" "$PWD"\r')
        wait(
            client,
            lambda: "PARKED:retained:/tmp" in capture(shells, terminal),
            "attachment replaced shell state or cwd",
        )
        attach(broad_name)
        client.type("printf 'UNICODE_PUNCTUATION_INPUT\\n'\r")
        wait(
            client,
            lambda: "UNICODE_PUNCTUATION_INPUT" in capture(source, "=" + broad_name + ":"),
            "space, Unicode or punctuation in a session name was not preserved",
        )
        assert original_identities == identities(source)
        assert not requests, "standalone launch contacted the Backbone API"
        print(
            "PASS: public launcher with poisoned Backbone configuration and zero API requests; "
            "generic chooser, exact session targets, broad names and parked shell state",
            flush=True,
        )

        # These are test-owned disposable shells. Offline references retain their
        # raw identity and reconnect without reconstructing the external session.
        source.run("kill-session", "-t", "=" + broad_name + ":")
        wait(
            client,
            lambda: "session offline" in capture(viewer, "=viewer:.1"),
            "missing offline attachment view",
        )
        assert saved(library).pane["agent"] == broad_name
        assert saved(library).pane["source_socket"] == source.socket
        assert saved(library).tab["name"] == "My tools"
        create(source, broad_name)
        wait(
            client,
            lambda: attached(source, "=" + broad_name + ":"),
            "offline attachment did not reconnect",
        )
        client.type("printf 'RECONNECTED_INPUT\\n'\r")
        wait(
            client,
            lambda: "RECONNECTED_INPUT" in capture(source, "=" + broad_name + ":"),
            "reconnected attachment did not accept input",
        )
        final_identities = identities(source)
        button("Exit")
        wait(client, lambda: client.process.poll() is not None, "standalone viewer did not exit")
        assert not manifest.exists()
        assert final_identities == identities(source)
        assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")

        # Reopen with another chooser socket containing a same-named decoy. Saved
        # attachments must continue connecting to their original source socket.
        create(alternate, broad_name)
        alternate_identities = identities(alternate)
        client, viewer, manifest = open_window(alternate)
        wait(
            client,
            lambda: attached(source, "=" + broad_name + ":"),
            "reopen did not retain the saved attachment socket",
        )
        assert not attached(alternate, "=" + broad_name + ":")
        assert saved(library).pane["source_socket"] == source.socket
        client.type("printf 'ORIGINAL_SOCKET_INPUT\\n'\r")
        wait(
            client,
            lambda: "ORIGINAL_SOCKET_INPUT" in capture(source, "=" + broad_name + ":"),
            "reopened input missed the saved source socket",
        )
        assert "ORIGINAL_SOCKET_INPUT" not in capture(alternate, "=" + broad_name + ":")
        button("Tab actions…")
        button("Close tab")
        wait(client, lambda: "No tabs yet" in capture(viewer, "%0"), "close tab failed")
        assert not shells.run("list-sessions", "-F", "#{session_name}", check=False)
        assert final_identities == identities(source)
        assert alternate_identities == identities(alternate)
        button("Exit")
        wait(client, lambda: client.process.poll() is not None, "final viewer did not exit")
        assert not manifest.exists()
        assert final_identities == identities(source)
        assert alternate_identities == identities(alternate)
        assert not requests, "generic source polling contacted the Backbone API"
        print(
            "PASS: offline association/reconnection, source socket retained across reopen, "
            "explicit shell cleanup and external sessions surviving tab/viewer close",
            flush=True,
        )
    finally:
        for client in clients:
            client.close()
        server.shutdown()
        server.server_close()
        for tmux in (source, alternate, shells):
            tmux.run("kill-server", check=False)
        Path(shells.socket + ".viewer-lock").unlink(missing_ok=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="workspace-standalone-smoke-") as directory:
        run(Path(directory))
