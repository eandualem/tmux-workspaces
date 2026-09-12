"""Persistent pane selection with captured clipboard output, never the OS clipboard."""

import base64
import json
import re
import shlex
import sys
import tempfile
from pathlib import Path

from tests.integration.support import FixtureResources, sidebar, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.model import leaves
from tmux_workspaces.persistence import Store
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux


def exercise(resources, alternate):
    library = resources.library()
    store = Store(library)
    try:
        model = store.load()
        model.split("right", str(resources.root))
        for leaf in leaves(model.tab["tree"]):
            leaf["cwd"] = str(resources.root)
        store.save(model)
        leaf_ids = [leaf["id"] for leaf in leaves(model.tab["tree"])]
        targets = ["=" + Shells.name(leaf) + ":" for leaf in leaves(model.tab["tree"])]
    finally:
        store.close()
    if not alternate:
        (resources.root / "theme.toml").write_text('surface = "default"\n')
    client = resources.client(
        [
            "--data-dir",
            str(library),
            "--source-socket",
            str(resources.root / "absent.sock"),
            "--no-keymap",
            "--theme",
            str(resources.root / "theme.toml"),
        ],
        terminal_env={"HOME": str(resources.root)},
    )
    wait(client, lambda: client.manifest(library), "selection viewer missing")
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    shells = Tmux(socket_path(library, "terminals"))
    wait(client, lambda: "Configure…" in sidebar(viewer), "selection sidebar missing")
    wait(
        client,
        lambda: len(shells.run("list-panes", "-a").splitlines()) == 2,
        "selection shells missing",
    )
    # Advertise clipboard support only to the owned fake terminal. OSC52 bytes
    # stay in Client.output; no terminal emulator or OS clipboard is involved.
    viewer.run("set-option", "-as", "terminal-features", ",xterm*:clipboard")
    original_keys = shells.run("list-keys")
    original_pids = shells.run("list-panes", "-a", "-F", "#{pane_pid}")
    program = resources.root / "selection_app.py"
    recording = resources.root / "input.bytes"
    program.write_text(
        "import os, select, tty\n"
        "tty.setraw(0)\n"
        + ("os.write(1, b'\\x1b[?1049h\\x1b[?1002h\\x1b[?1006h')\n" if alternate else "")
        + "os.write(1, b'\\x1b[2J\\x1b[HSELECT_TOKEN sample line')\n"
        "n = 0\n"
        "while True:\n"
        " if select.select([0], [], [], 0.1)[0]:\n"
        "  data = os.read(0, 4096)\n"
        f"  with open({str(recording)!r}, 'ab') as f: f.write(data)\n"
        " n += 1\n"
        " os.write(1, ('\\x1b[5;1Hbackground %s' % n).encode())\n"
    )
    shells.run("send-keys", "-t", targets[0], shlex.join([sys.executable, str(program)]), "Enter")
    shells.run("send-keys", "-t", targets[1], "printf 'SIBLING_PRIVATE\\n'", "Enter")

    def pane_for(leaf_id):
        return next(
            (
                line.split("|")[0]
                for line in viewer.run(
                    "list-panes", "-F", "#{pane_id}|#{@viewer_leaf_id}"
                ).splitlines()
                if line.endswith("|" + leaf_id)
            ),
            None,
        )

    wait(client, lambda: all(pane_for(leaf_id) for leaf_id in leaf_ids), "content panes missing")
    pane, sibling = [pane_for(leaf_id) for leaf_id in leaf_ids]
    wait(
        client,
        lambda: "SELECT_TOKEN" in viewer.run("capture-pane", "-p", "-t", pane),
        "selection program missing",
    )
    wait(
        client,
        lambda: "SIBLING_PRIVATE" in viewer.run("capture-pane", "-p", "-t", sibling),
        "sibling sentinel missing",
    )

    def geometry():
        return [
            int(v)
            for v in viewer.run(
                "display-message",
                "-p",
                "-t",
                pane,
                "#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}",
            ).split("|")
        ]

    def selected():
        return viewer.run("display-message", "-p", "-t", pane, "#{selection_present}") == "1"

    def clipboard():
        return [
            base64.b64decode(data)
            for data in re.findall(
                rb"\x1b\]52;[^;]*;([A-Za-z0-9+/=]+)(?:\x07|\x1b\\)", client.output
            )
        ]

    def drag(end_x=None):
        client.pump(0.6)
        left, top, width, _ = geometry()
        x, y = left + 1, top + 1
        end_x = end_x or x + len("SELECT_TOKEN")
        client.type(
            f"\x1b[<0;{x};{y}M\x1b[<32;{x + 1};{y}M"
            f"\x1b[<32;{min(end_x, left + width)};{y}M"
            f"\x1b[<32;{end_x};{y}M\x1b[<0;{end_x};{y}m"
        )
        wait(client, selected, "drag release did not retain selection")

    client.output = b""
    drag()
    assert not clipboard(), "release copied to clipboard"
    assert viewer.run("show-buffer", check=False) == "", "release created a tmux buffer"
    client.pump(0.8)
    assert selected(), "background output cleared selection"
    client.type(direct_sequence("copy-selection"))
    wait(client, lambda: clipboard(), "explicit copy did not emit OSC52")
    assert clipboard()[-1] == b"SELECT_TOKEN", clipboard()
    assert selected(), "copy unexpectedly cleared highlight"
    left, top, width, _ = geometry()
    client.click(left + 4, top + 3)
    wait(client, lambda: not selected(), "outside click did not clear selection")
    before = len(clipboard())
    client.type(direct_sequence("copy-selection"))
    assert len(clipboard()) == before, "copy without selection reused stale content"
    drag(left + width + 15)
    client.type("\x07y")
    wait(client, lambda: len(clipboard()) > before, "portable prefix copy did not work")
    assert b"SIBLING_PRIVATE" not in clipboard()[-1], "selection crossed into sibling pane"
    assert clipboard()[-1].startswith(b"SELECT_TOKEN sample line"), clipboard()[-1]
    client.type("\x1b")
    wait(client, lambda: not selected(), "Escape did not cancel selection")
    for count, text in ((2, b"SELECT_TOKEN"), (3, b"SELECT_TOKEN sample line")):
        client.pump(0.6)
        before = len(clipboard())
        client.click(left + 3, top + 1, count=count)
        wait(client, selected, "multi-click did not retain selection")
        # Nested tmux's default double-click waits 0.3s for a third click,
        # then another 0.3s before copying. Observe that whole interval so
        # a forwarded second click cannot silently schedule a later OSC52.
        client.pump(0.8)
        assert len(clipboard()) == before, "multi-click automatically copied"
        assert shells.run("display-message", "-p", "-t", targets[0], "#{pane_in_mode}") == "0"
        client.type(direct_sequence("copy-selection"))
        wait(client, lambda before=before: len(clipboard()) > before, "multi-click copy failed")
        client.pump(0.8)
        copied = [value.strip() for value in clipboard()[before:]]
        assert copied == [text], (count, copied)
        client.type("\x1b")
    # Returning to the live terminal restores normal application input.
    client.type("POST_SELECTION_INPUT")
    wait(
        client,
        lambda: b"POST_SELECTION_INPUT" in recording.read_bytes(),
        "typing did not resume after selection",
    )
    if alternate:
        start = len(recording.read_bytes())
        client.click(left + 4, top + 3)
        client.type(
            f"\x1b[<2;{left + 4};{top + 3}M\x1b[<2;{left + 4};{top + 3}m"
            f"\x1b[<64;{left + 4};{top + 3}M"
        )
        wait(
            client,
            lambda: all(
                token in recording.read_bytes()[start:] for token in (b"[<0;", b"[<2;", b"[<64;")
            ),
            "mouse click/right-click/wheel did not resume after selection",
        )
    drag()
    sibling_left, sibling_top = map(
        int,
        viewer.run("display-message", "-p", "-t", sibling, "#{pane_left}|#{pane_top}").split("|"),
    )
    client.click(sibling_left + 3, sibling_top + 2)
    wait(client, lambda: not selected(), "adjacent pane click did not clear selection")
    drag()
    client.resize(140, 36)
    # tmux may clear a selection when reflowing the pane. Selection must work
    # again at the new geometry, with the original application still running.
    client.type("\x1b")
    drag()
    client.click(4, 2)
    wait(client, lambda: not selected(), "sidebar click did not clear selection")
    assert shells.run("list-keys") == original_keys, "source bindings changed"
    assert shells.run("list-panes", "-a", "-F", "#{pane_pid}") == original_pids
    data = recording.read_bytes()
    assert direct_sequence("copy-selection").encode() not in data, "copy reached application"
    print(
        f"PASS: {'mouse-aware alternate screen/padded' if alternate else 'inline/plain'}: "
        "persistent pane-local drag, explicit OSC52/prefix copy, background output, "
        "multi-click, resize and cancellation",
        flush=True,
    )


if __name__ == "__main__":
    for alternate in (False, True):
        with (
            tempfile.TemporaryDirectory(prefix="tw-selection-", dir="/tmp") as directory,
            FixtureResources(parent=Path(directory)) as resources,
        ):
            exercise(resources, alternate)
