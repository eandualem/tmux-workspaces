"""Real encrypted loopback SSH transport with disposable keys and tmux sessions."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import subprocess
import sys

from tests.integration.ssh_support import SshHost
from tests.integration.support import FixtureResources, click_button, saved, sidebar, tab_row, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.model import leaves
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux


def exercise() -> None:
    with FixtureResources(prefix="tw-ssh-") as resources:
        library = resources.library("library")
        source = resources.server("source")
        for name in ("sample", "untouched"):
            source.run(
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                name,
                "-e",
                "HOME=" + str(resources.root),
                "-c",
                str(resources.root),
                "/bin/sh -i",
            )
        source_pids = source.run("list-panes", "-a", "-F", "#{session_name} #{pane_pid}")
        shells = Tmux(socket_path(library, "terminals"))
        args = ["--data-dir", str(library), "--source-socket", source.socket]
        with SshHost(resources.root / "ssh", args) as host:

            def connect():
                client = resources.own_client(host.client())

                def manifest():
                    return next((library / "windows").glob("*/runtime.json"), None)

                try:
                    wait(client, manifest, "SSH-authenticated viewer did not start")
                except AssertionError as error:
                    raise AssertionError(str(error) + "\n" + host.diagnostics()) from error
                path = manifest()
                viewer = Tmux(json.loads(path.read_text())["viewer_socket"])
                wait(
                    client, lambda: "Layouts saved" in sidebar(viewer), "SSH UI did not initialize"
                )
                connection = host.connection()
                assert connection["connection"].split()[::2] == ["127.0.0.1", "127.0.0.1"]
                assert connection["tty"].startswith("/dev/"), "SSH did not allocate a remote PTY"
                assert connection["term"] == "xterm-256color"
                return client, viewer, path

            client, viewer, manifest = connect()

            def key(action):
                client.type(direct_sequence(action))

            def command(target, marker):
                left, right = marker.rsplit("_", 1)
                client.type(f"printf '%s_%s\\n' {shlex.quote(left)} {shlex.quote(right)}\r")
                wait(
                    client,
                    lambda: marker in shells.run("capture-pane", "-p", "-t", target),
                    "SSH input did not reach the intended ordinary shell",
                )

            initial = saved(library)
            initial_leaf = initial.pane.copy()
            first_target = "=" + Shells.name(initial_leaf) + ":"
            wait(
                client,
                lambda: (
                    shells.run("display-message", "-p", "-t", first_target, "#{session_attached}")
                    == "1"
                ),
                "first ordinary shell did not attach over SSH",
            )
            first_pid = shells.run("display-message", "-p", "-t", first_target, "#{pane_pid}")
            client.type("\x07rRemote first\r")
            wait(
                client,
                lambda: saved(library).tab["name"] == "Remote first",
                "SSH prefix rename failed",
            )
            client.type("cd " + shlex.quote(str(resources.root)) + "\r")
            command(first_target, "SSH_FIRST_READY")

            click_button(client, viewer, "+ Tab")
            wait(client, lambda: len(saved(library).space["tabs"]) == 2, "SSH mouse new-tab failed")
            client.type(direct_sequence("rename-tab") + "Remote four\r")
            wait(
                client, lambda: saved(library).tab["name"] == "Remote four", "SSH CSI rename failed"
            )
            for count, action in ((2, "split-right"), (3, "split-below"), (4, "split-right")):
                key(action)
                wait(
                    client,
                    lambda count=count: len(leaves(saved(library).tab["tree"])) == count,
                    "SSH split did not create the expected ordinary pane",
                )
            four_target = "=" + Shells.name(saved(library).pane) + ":"
            command(four_target, "SSH_FOUR_READY")

            # A mouse gesture traverses ssh's remote PTY. The following direct
            # switch and text share a write: input must wait for destination focus.
            client.click(3, tab_row(viewer, "Remote first") + 1)
            wait(
                client, lambda: saved(library).tab["name"] == "Remote first", "SSH tab click failed"
            )
            client.type(direct_sequence("select-tab-2") + "printf 'SSH_ROUTE_%s\\n' FOUR\r")
            wait(
                client,
                lambda: "SSH_ROUTE_FOUR" in shells.run("capture-pane", "-p", "-t", four_target),
                "immediate SSH action+text did not reach the target pane",
            )
            wait(
                client,
                lambda: b"SSH_ROUTE_FOUR" in client.output,
                "rendered program output did not return through the SSH client",
            )
            assert "SSH_ROUTE_FOUR" not in shells.run("capture-pane", "-p", "-t", first_target)

            for cols, rows, pane_count in ((60, 20, 2), (160, 38, 5)):
                client.resize(cols, rows)
                wait(
                    client,
                    lambda cols=cols, rows=rows: (
                        viewer.run(
                            "display-message",
                            "-p",
                            "-t",
                            "=viewer:",
                            "#{window_width} #{window_height}",
                        )
                        == f"{cols} {rows}"
                    ),
                    "SSH window-change did not resize the remote viewer",
                )
                wait(
                    client,
                    lambda pane_count=pane_count: (
                        len(viewer.run("list-panes").splitlines()) == pane_count
                    ),
                    "SSH resize lost focus fallback or the saved four-pane arrangement",
                )

            key("new-workspace")
            client.type("Remote purpose\r")
            wait(
                client,
                lambda: len(saved(library).state["workspaces"]) == 2,
                "SSH workspace creation failed",
            )
            click_button(client, viewer, "+ Tab")
            wait(
                client, lambda: saved(library).tab is not None, "SSH workspace tab creation failed"
            )
            key("select-workspace-1")
            wait(
                client,
                lambda: saved(library).space["name"] == "Workspace 1",
                "SSH workspace selection failed",
            )
            key("select-tab-1")
            wait(
                client,
                lambda: saved(library).tab["name"] == "Remote first",
                "SSH first tab missing",
            )
            command(first_target, "SSH_BEFORE_FOREGROUND")
            foreground = resources.root / "foreground.py"
            started = resources.root / "foreground-started.json"
            resumed = resources.root / "foreground-resumed.json"
            foreground.write_text(
                "import json, os, sys\n"
                "from pathlib import Path\n"
                f"Path({str(started)!r}).write_text(json.dumps({{'pid': os.getpid()}}))\n"
                "answer = sys.stdin.readline().strip()\n"
                f"Path({str(resumed)!r}).write_text(\n"
                "    json.dumps({'pid': os.getpid(), 'answer': answer}))\n"
            )
            client.type(shlex.join([sys.executable, str(foreground)]) + "\r")
            wait(client, started.exists, "foreground test program did not start")
            foreground_pid = json.loads(started.read_text())["pid"]
            key("select-tab-2")
            wait(
                client,
                lambda: saved(library).tab["name"] == "Remote four",
                "SSH four-pane tab missing",
            )
            parked = "=" + Shells.name(saved(library).pane) + ":"
            parked_pid = shells.run("display-message", "-p", "-t", parked, "#{pane_pid}")
            click_button(client, viewer, "Attach session…")
            click_button(client, viewer, "sample")
            wait(
                client,
                lambda: (
                    saved(library).pane.get("agent") == "sample"
                    and source.run("display-message", "-p", "-t", "=sample:", "#{session_attached}")
                    == "1"
                ),
                "SSH mouse attachment failed",
            )
            client.type("printf 'SSH_EXTERNAL_%s\\n' INPUT\r")
            wait(
                client,
                lambda: "SSH_EXTERNAL_INPUT" in source.run("capture-pane", "-p", "-t", "=sample:"),
                "SSH input failed through external attachment",
            )
            key("sidebar")  # Save observed split ratios before the disconnect.
            before = saved(library).state["workspaces"]
            ordinary_pids = shells.run("list-panes", "-a", "-F", "#{session_name} #{pane_pid}")

            # Drop the SSH transport itself, without asking the viewer to exit.
            client.process.kill()
            client.process.wait(timeout=5)
            wait(None, lambda: not manifest.exists(), "SSH disconnect did not clean its viewer")
            assert (
                source.run("list-panes", "-a", "-F", "#{session_name} #{pane_pid}") == source_pids
            )
            assert (
                shells.run("list-panes", "-a", "-F", "#{session_name} #{pane_pid}") == ordinary_pids
            )
            assert (
                shells.run("display-message", "-p", "-t", first_target, "#{pane_pid}") == first_pid
            )
            os.kill(foreground_pid, 0)  # Read-only liveness check of our recorded child.

            client, viewer, manifest = connect()
            wait(
                client,
                lambda: saved(library).state["workspaces"] == before,
                "SSH reopen changed saved arrangements",
            )
            wait(
                client,
                lambda: (
                    source.run("display-message", "-p", "-t", "=sample:", "#{session_attached}")
                    == "1"
                ),
                "SSH reconnect did not restore the saved external association",
            )
            assert shells.run(
                "display-message", "-p", "-t", first_target, "#{pane_current_path}"
            ) == str(resources.root)
            assert shells.run("display-message", "-p", "-t", parked, "#{pane_pid}") == parked_pid
            key("select-tab-1")
            wait(
                client,
                lambda: saved(library).tab["name"] == "Remote first",
                "foreground tab missing",
            )
            client.type("continued-over-new-ssh\r")
            wait(client, resumed.exists, "foreground program did not resume after SSH reconnect")
            assert json.loads(resumed.read_text()) == {
                "pid": foreground_pid,
                "answer": "continued-over-new-ssh",
            }
            key("select-tab-2")
            wait(
                client,
                lambda: saved(library).tab["name"] == "Remote four",
                "attachment tab missing",
            )
            untouched_pid = source.run("display-message", "-p", "-t", "=untouched:", "#{pane_pid}")
            attached_leaf = saved(library).pane["id"]
            attached_pane = next(
                line.split()[0]
                for line in viewer.run(
                    "list-panes", "-F", "#{pane_id} #{@viewer_leaf_id}"
                ).splitlines()
                if line.split()[-1] == attached_leaf
            )
            source.run("kill-session", "-t", "=sample:")
            wait(
                client,
                lambda: "session offline" in viewer.run("capture-pane", "-p", "-t", attached_pane),
                "SSH attachment did not show offline state",
            )
            assert saved(library).pane["agent"] == "sample"
            source.run(
                "new-session",
                "-d",
                "-s",
                "sample",
                "-e",
                "HOME=" + str(resources.root),
                "/bin/sh -i",
            )
            wait(
                client,
                lambda: (
                    source.run("display-message", "-p", "-t", "=sample:", "#{session_attached}")
                    == "1"
                ),
                "SSH attachment did not reconnect after the disposable source returned",
            )
            assert (
                source.run("display-message", "-p", "-t", "=untouched:", "#{pane_pid}")
                == untouched_pid
            )
            click_button(client, viewer, "Exit")
            wait(None, lambda: client.process.poll() is not None, "SSH normal exit did not finish")
            assert client.process.returncode == 0
            assert source.run("has-session", "-t", "=sample:") == ""
            assert (
                shells.run("list-panes", "-a", "-F", "#{session_name} #{pane_pid}") == ordinary_pids
            )
            print(
                "PASS: authenticated loopback SSH PTY, mouse/prefix/CSI input, immediate typing, "
                "remote resize, abrupt disconnect/reconnect, saved workspaces/four-pane layout, "
                "foreground process/cwd/PIDs, attachment offline/reconnect and external ownership",
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--required", action="store_true", help="require SSH coverage on this host")
    args = parser.parse_args()
    if sys.platform != "linux" and not args.required:
        print("SKIP: loopback SSH is required on Linux; use --required to opt in on this platform")
        return
    print(
        "SSH environment:",
        platform.system(),
        platform.release(),
        "Python",
        platform.python_version(),
        subprocess.check_output(["tmux", "-V"], text=True).strip(),
        subprocess.run(["ssh", "-V"], capture_output=True, text=True).stderr.strip(),
        flush=True,
    )
    exercise()


if __name__ == "__main__":
    main()
