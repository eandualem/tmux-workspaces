"""Two independent windows, one saved library and source server; optional Ghostty TERM."""

from __future__ import annotations

import argparse
import fcntl
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests.integration.support import OUTLINE, FixtureResources, click_button, saved, wait
from tmux_workspaces.application import start_demo
from tmux_workspaces.tmux import Tmux


def run(directory: Path, terminfo: str | None) -> None:
    with FixtureResources(parent=directory) as resources:
        exercise(resources, terminfo)


def exercise(resources: FixtureResources, terminfo: str | None) -> None:
    directory = resources.root
    source = resources.server("window-smoke-source")
    source_socket = source.socket
    with patch.dict("os.environ", {"HOME": str(directory), "SHELL": "/bin/sh"}):
        start_demo(source_socket, directory)
    items = json.loads((directory / "demo.json").read_text())

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"items": items}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    resources.own_cleanup(server.server_close)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    resources.own_cleanup(server.shutdown)
    library = resources.library()
    # Even a still-open v1 viewer.lock must not block a new window.
    legacy_lock = (library / "viewer.lock").open("a")
    resources.own_cleanup(legacy_lock.close)
    fcntl.flock(legacy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    args = [
        "--backbone",
        "--data-dir",
        str(library),
        "--backbone-data-dir",
        str(directory / "empty-backbone"),
        "--source-socket",
        source_socket,
        "--url",
        f"http://127.0.0.1:{server.server_port}",
    ]
    env = {"TERM": "xterm-ghostty", "TERMINFO": terminfo} if terminfo else None

    def open_window(terminal_env=None):
        client = resources.client(args, terminal_env=terminal_env)
        wait(client, lambda: client.manifest(library), "new window refused to start")
        manifest = client.manifest(library)
        runtime = json.loads(manifest.read_text())
        viewer = Tmux(runtime["viewer_socket"])
        wait(
            client,
            lambda: "Detach" in viewer.run("capture-pane", "-p", "-t", "%0").translate(OUTLINE),
            "sidebar failed to start",
        )
        return client, viewer, manifest

    def sidebar(viewer):
        return viewer.run("capture-pane", "-p", "-t", "%0").translate(OUTLINE)

    click = click_button

    def selected(viewer):
        return viewer.run("list-panes", "-F", "#{pane_active} #{@viewer_agent}")

    first, first_view, first_manifest = open_window(env)
    second, second_view, second_manifest = open_window()
    assert first_view.socket != second_view.socket
    if terminfo:
        assert first_view.run("list-clients", "-F", "#{client_termname}") == "xterm-ghostty"
    identities = source.run(
        "list-sessions", "-F", "#{session_id}:#{session_created}:#{session_name}"
    )
    shell_socket = json.loads(first_manifest.read_text())["shell_socket"]
    assert json.loads(second_manifest.read_text())["shell_socket"] == shell_socket
    shells = Tmux(shell_socket)
    wait(
        first,
        lambda: len(shells.run("list-sessions").splitlines()) == 1,
        "windows duplicated a terminal",
    )
    assert len(saved(library).space["tabs"]) == 1
    click(first, first_view, "Attach session…")
    click(first, first_view, "manager")
    wait(second, lambda: "manager" in sidebar(second_view), "attachment not synchronized")
    assert "1 Workspaces" in selected(second_view), "peer attachment redirected keyboard input"
    click(second, second_view, "+ Tab")
    click(second, second_view, "Attach session…")
    click(second, second_view, "researcher")
    wait(first, lambda: "1 manager" in selected(first_view), "first window selection changed")
    wait(second, lambda: "1 researcher" in selected(second_view), "second window selection wrong")
    first.type("printf 'FIRST_WINDOW_MANAGER\\n'\r")
    second.type("printf 'SECOND_WINDOW_RESEARCHER\\n'\r")
    assert "FIRST_WINDOW_MANAGER" in source.run("capture-pane", "-p", "-t", "=manager:")
    assert "SECOND_WINDOW_RESEARCHER" not in source.run("capture-pane", "-p", "-t", "=manager:")
    assert "SECOND_WINDOW_RESEARCHER" in source.run("capture-pane", "-p", "-t", "=researcher:")
    click(first, first_view, "▾")
    click(first, first_view, "New workspace")
    first.type("Shared research")
    click(first, first_view, "Save name")
    wait(
        second,
        lambda: len(saved(library).state["workspaces"]) == 2,
        "new workspace did not synchronize",
    )
    assert "1 researcher" in selected(second_view)
    click(second, second_view, "▾")
    click(second, second_view, "Switch workspace")
    click(second, second_view, "Shared research")
    wait(second, lambda: "No tabs yet" in sidebar(second_view), "shared workspace missing")
    click(second, second_view, "+ Tab")
    click(second, second_view, "Attach session…")
    click(second, second_view, "reviewer")
    wait(
        first,
        lambda: "reviewer" in sidebar(first_view),
        "peer tab addition did not synchronize",
    )
    # Closing a terminal window must only clean up that window, without a stale lock.
    first.close_terminal()
    assert not first_manifest.exists()
    assert "reviewer" in sidebar(second_view)
    assert identities == source.run(
        "list-sessions", "-F", "#{session_id}:#{session_created}:#{session_name}"
    )
    third, third_view, third_manifest = open_window(env)
    click(second, second_view, "Exit")
    wait(second, lambda: second.process.poll() is not None, "second window did not exit")
    assert not second_manifest.exists()
    assert "reviewer" in sidebar(third_view)
    click(third, third_view, "Exit")
    wait(third, lambda: third.process.poll() is not None, "third window did not exit")
    assert not third_manifest.exists()
    assert identities == source.run(
        "list-sessions", "-F", "#{session_id}:#{session_created}:#{session_name}"
    )
    assert not list((library / "windows").glob("*/runtime.json"))
    print("PASS: concurrent windows, independent selection and input, shared workspace/tab edits,")
    print(
        "      terminal close/reopen, old lock ignored, other windows and source sessions survive."
    )
    if terminfo:
        print("PASS: Ghostty xterm-ghostty + installed TERMINFO startup and mouse interaction.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ghostty-terminfo", help="Ghostty's installed terminfo directory")
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="workspace-windows-smoke-") as directory:
        run(Path(directory), options.ghostty_terminfo)
