"""Immediate navigation and typing through a real PTY, using only owned shells."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from tests.integration.support import Client, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.model import leaves
from tmux_workspaces.persistence import Store
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux


def bracketed_paste(client: Client, shells: Tmux, target: str) -> None:
    # macOS's system bash predates bracketed paste; zsh and Linux's bash support
    # it without sourcing user configuration. The exec preserves the shell PID.
    shell = "/bin/zsh -f" if sys.platform == "darwin" else "/bin/bash --noprofile --norc"
    token = os.urandom(8).hex()
    client.type("exec " + shell + "\r")
    client.type("printf 'PASTE_READY_%s\\n' " + token + "\r")

    def capture():
        return shells.run("capture-pane", "-S", "-", "-p", "-t", target)

    wait(client, lambda: "PASTE_READY_" + token in capture(), "paste shell did not start")
    payload = "printf 'PASTED_%s\\n' " + token + "\nprintf 'PASTED_SECOND_%s\\n' " + token
    os.write(client.master, ("\x1b[200~" + payload + "\x1b[201~").encode())
    client.pump(0.3)
    before_enter = capture()
    assert "PASTED_" + token not in before_enter, "bracketed paste executed before Enter"
    assert "PASTED_SECOND_" + token not in before_enter, "second pasted command ran before Enter"
    client.type("\r")
    wait(
        client,
        lambda: "PASTED_" + token in capture() and "PASTED_SECOND_" + token in capture(),
        "bracketed paste did not execute intact after Enter",
    )


def native_mouse(directory: Path, client: Client, viewer: Tmux) -> None:
    """Check nested application forwarding and tmux's plain-pane selection."""
    recording, ready = directory / "mouse.bytes", directory / "mouse.ready"
    program = directory / "mouse_app.py"
    program.write_text(
        "import os, pathlib, termios, tty\n"
        "old = termios.tcgetattr(0)\n"
        "tty.setraw(0)\n"
        "try:\n"
        " os.write(1, b'\\x1b[?1000h\\x1b[?1006h')\n"
        f" pathlib.Path({str(ready)!r}).touch()\n"
        " while True:\n"
        "  data = os.read(0, 4096)\n"
        "  if not data or b'\\x03' in data: break\n"
        f"  with open({str(recording)!r}, 'ab') as output: output.write(data)\n"
        "finally:\n"
        " os.write(1, b'\\x1b[?1000l\\x1b[?1006l')\n"
        " termios.tcsetattr(0, termios.TCSANOW, old)\n"
    )
    client.type(shlex.join([sys.executable, str(program)]) + "\r")
    wait(client, ready.exists, "nested mouse application did not start")
    pane, left, top = viewer.run(
        "display-message", "-p", "#{pane_id}|#{pane_left}|#{pane_top}"
    ).split("|")
    x, y = int(left) + 3, int(top) + 1

    def click(button=0):
        return f"\x1b[<{button};{x};{y}M\x1b[<{button};{x};{y}m"

    def downs():
        data = recording.read_bytes() if recording.exists() else b""
        return re.findall(rb"\x1b\[<([0-9]+);[0-9]+;[0-9]+M", data)

    for count in (2, 3):
        client.pump(0.5)
        before = len(downs())
        os.write(client.master, (click() * count).encode())
        wait(
            client,
            lambda before=before, count=count: len(downs()) >= before + count,
            "content multi-click was not forwarded",
        )
    os.write(client.master, (click(2) + click(64)).encode())
    wait(client, lambda: b"2" in downs() and b"64" in downs(), "content right-click or wheel lost")
    client.type("\x03")

    # Replace only this fixture's disposable attachment client with a plain
    # pane, where tmux (rather than a mouse-aware nested app) owns selection.
    viewer.run(
        "respawn-pane",
        "-k",
        "-t",
        pane,
        "printf 'NATIVE_TOKEN sample line\\n'; sleep 30",
    )
    wait(
        client,
        lambda: "NATIVE_TOKEN" in viewer.run("capture-pane", "-p", "-t", pane),
        "plain selection pane did not start",
    )
    for count, text in ((2, "NATIVE_TOKEN"), (3, "NATIVE_TOKEN sample line")):
        client.pump(0.5)
        viewer.run("delete-buffer", check=False)
        os.write(client.master, (click() * count).encode())
        wait(
            client,
            lambda text=text: viewer.run("show-buffer", check=False).strip() == text,
            "native double/triple selection changed",
        )


def exercise(directory: Path, pane_count: int) -> None:
    library = directory / f"routing-{pane_count}"
    with_store = Store(library)
    try:
        model = with_store.load()
        for space_index in range(2):
            if space_index:
                model.add_workspace("Second purpose")
                model.add_tab()
            for tab_index in range(2):
                if tab_index:
                    model.add_tab()
                for direction in ("right", "below", "right")[: pane_count - 1]:
                    model.split(direction, str(directory))
                for pane in leaves(model.tab["tree"]):
                    pane["cwd"] = str(directory)
            model.space["selected"] = model.space["tabs"][0]["id"]
        spaces = model.state["workspaces"]
        model.state["selected"] = spaces[0]["id"]
        with_store.save(model)
    finally:
        with_store.close()
    shells = Tmux(socket_path(library, "terminals"))
    client = Client(
        ["--data-dir", str(library), "--source-socket", str(directory / "absent.sock")],
        terminal_env={"HOME": str(directory)},
    )
    viewer = None
    try:
        wait(client, lambda: client.manifest(library), "routing viewer manifest missing")
        viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
        wait(
            client,
            lambda: "Layouts saved" in viewer.run("capture-pane", "-p", "-t", "%0"),
            "routing viewer did not initialize",
        )
        targets = [
            "=" + Shells.name(pane) + ":"
            for space in spaces
            for tab in space["tabs"]
            for pane in leaves(tab["tree"])
        ]

        def terminal(tab):
            return "=" + Shells.name({"id": tab["focus"]}) + ":"

        def contents():
            return {
                target: shells.run("capture-pane", "-S", "-", "-p", "-t", target)
                for target in targets
            }

        def packet():
            token = os.urandom(8).hex()
            # The full marker is absent from command echo, so only execution
            # can satisfy the assertion, including when input arrives late.
            return "printf 'ROUTED_%s\\n' " + token + "\r", "ROUTED_" + token

        # Warm every terminal arrangement and prove the selected shell executes.
        for space_index, space in enumerate(spaces):
            for tab_index, tab in enumerate(space["tabs"]):
                command, marker = packet()
                client.type(
                    direct_sequence(f"select-workspace-{space_index + 1}")
                    + direct_sequence(f"select-tab-{tab_index + 1}")
                    + command
                )
                wait(
                    client,
                    lambda marker=marker, tab=tab: (
                        marker in shells.run("capture-pane", "-p", "-t", terminal(tab))
                    ),
                    "routing fixture shell did not become ready",
                )
            client.type(direct_sequence("select-tab-1"))

        def identities():
            return set(
                shells.run(
                    "list-panes", "-a", "-F", "#{session_name}|#{pane_id}|#{pane_pid}"
                ).splitlines()
            )

        original = identities()
        assert len(original) == 4 * pane_count
        expected = []

        def routed():
            observed = contents()
            return all(
                [target for target, text in observed.items() if marker in text] == [destination]
                for marker, destination in expected
            )

        for method in ("click", "shortcut"):
            for kind in ("tab", "workspace"):
                for burst in (False, True):
                    client.type(
                        direct_sequence("select-workspace-1") + direct_sequence("select-tab-1")
                    )
                    lines = viewer.run("capture-pane", "-p", "-t", "%0").splitlines()
                    packets = []
                    for index in (1, 0, 1, 0):
                        tab = spaces[index if kind == "workspace" else 0]["tabs"][
                            index if kind == "tab" else 0
                        ]
                        if method == "shortcut":
                            navigation = direct_sequence(f"select-{kind}-{index + 1}")
                        else:
                            if kind == "tab":
                                row, column = (4 if index else 2), 3
                            else:
                                label = f"[ {index + 1} ]"
                                row = next(i for i, line in enumerate(lines) if label in line)
                                column = lines[row].index(label) + 2
                            navigation = f"\x1b[<0;{column};{row + 1}M\x1b[<0;{column};{row + 1}m"
                        command, marker = packet()
                        expected.append((marker, terminal(tab)))
                        packets.append(navigation + command)
                        if not burst:
                            os.write(client.master, packets[-1].encode())
                            wait(client, routed, f"immediate {method}/{kind} input misrouted")
                    if burst:
                        # Multiple navigation events and commands in one write:
                        # no readiness wait between physical inputs.
                        os.write(client.master, "".join(packets).encode())
                        wait(client, routed, f"burst {method}/{kind} input misrouted")
        client.pump(0.3)
        assert routed(), "delayed or duplicate delivery reached an unintended shell"
        assert identities() == original, "navigation changed a shell process"
        if pane_count == 1:
            bracketed_paste(client, shells, terminal(spaces[0]["tabs"][0]))
            native_mouse(directory, client, viewer)
            assert identities() == original, "native mouse checks changed a shell process"
        print(
            f"PASS: {pane_count}-pane tab/workspace immediate and burst click/shortcut routing; "
            "all 32 commands executed only in their intended shell; original PIDs retained",
            flush=True,
        )
    finally:
        client.close()
        if viewer is not None:
            viewer.run("kill-server", check=False)
        shell_pids = [
            int(pid)
            for pid in shells.run("list-panes", "-a", "-F", "#{pane_pid}", check=False).splitlines()
        ]
        shells.run("kill-server", check=False)

        def shells_exited():
            for pid in shell_pids:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                return False
            return True

        # kill-server can return before login shells finish writing history.
        # Let all owned shells exit before TemporaryDirectory removes their HOME.
        wait(client, shells_exited, "fixture shells did not finish exiting")
        Path(shells.socket + ".viewer-lock").unlink(missing_ok=True)
