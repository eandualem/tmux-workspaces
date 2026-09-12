"""Drag visible padded rules without changing gutter widths or terminal clients."""

import json
import tempfile
from pathlib import Path

from tests.integration.smoke_gutters import panes
from tests.integration.support import FixtureResources, saved, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.model import leaves
from tmux_workspaces.persistence import Store
from tmux_workspaces.tmux import Tmux


def exercise(resources, direction):
    library = resources.library()
    store = Store(library)
    model = store.load()
    model.split(direction, str(resources.root))
    model.split("below" if direction == "right" else "right", str(resources.root))
    for leaf in leaves(model.tab["tree"]):
        leaf["cwd"] = str(resources.root)
    model.tab["focus"] = leaves(model.tab["tree"])[0]["id"]
    store.save(model)
    store.close()
    client = resources.client(
        [
            "--data-dir",
            str(library),
            "--source-socket",
            str(resources.root / "absent.sock"),
            "--no-keymap",
        ],
        terminal_env={"HOME": str(resources.root)},
    )
    wait(client, lambda: client.manifest(library), "drag viewer missing")
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    shells = Tmux(socket_path(library, "terminals"))
    wait(
        client,
        lambda: len([pane for pane in panes(viewer) if pane["band"]]) == 2,
        "drag split layout missing",
    )
    wait(
        client,
        lambda: len(shells.run("list-clients").splitlines()) == 3,
        "ordinary clients not attached",
    )
    original = viewer.run("list-panes", "-F", "#{pane_id}|#{pane_pid}")
    source_pids = shells.run("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}")
    source_keys = shells.run("list-keys")
    focus = viewer.run("display-message", "-p", "#{pane_id}")
    panel_width = next(pane["width"] for pane in panes(viewer) if pane["id"] == "%0")
    root_band = next(
        pane["id"]
        for pane in panes(viewer)
        if pane["band"] and (pane["width"] == 1 if direction == "right" else pane["height"] == 1)
    )
    axis = "left" if direction == "right" else "top"

    def band():
        return next(pane for pane in panes(viewer) if pane["id"] == root_band)

    def fixed():
        current = panes(viewer)
        assert next(p["width"] for p in current if p["id"] == "%0") == panel_width, current
        for pane in current:
            if pane["gutter"]:
                assert pane["width"] == 1 or pane["height"] == 1, current
        assert viewer.run("list-panes", "-F", "#{pane_id}|#{pane_pid}") == original
        assert viewer.run("display-message", "-p", "#{pane_id}") == focus
        assert shells.run("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}") == source_pids

    def drag(delta, *, border=0):
        client.pump(0.4)  # Start a new physical gesture outside tmux's click timer.
        rule = band()
        if direction == "right":
            x, y = rule["left"] + 1 + border, 4
            target_x, target_y = min(158, max(2, x + delta)), y
            step_x, step_y = x + (1 if delta > 0 else -1), y
        else:
            x, y = rule["left"] + 4, rule["top"] + 1 + border
            target_x, target_y = x, min(37, max(2, y + delta))
            step_x, step_y = x, y + (1 if delta > 0 else -1)
        client.type(
            f"\x1b[<0;{x};{y}M\x1b[<32;{step_x};{step_y}M"
            f"\x1b[<32;{target_x};{target_y}M\x1b[<0;{target_x};{target_y}m"
        )
        wait(
            client,
            lambda: viewer.run("show-options", "-gv", "@viewer_resizing") == "0",
            "drag did not release input",
        )

    initial = band()[axis]
    delta = 8 if direction == "right" else 4
    viewer.run("copy-mode", "-t", focus)
    drag(delta)
    wait(client, lambda: band()[axis] == initial + delta, "visible rule did not resize content")
    assert viewer.run("display-message", "-p", "-t", focus, "#{pane_in_mode}") == "0"
    fixed()
    wait(client, lambda: saved(library).tab["tree"]["ratio"] != 0.5, "drag ratio was not saved")
    drag(-delta)
    wait(client, lambda: band()[axis] == initial, "reverse drag did not restore split")
    fixed()
    # Hidden tmux borders are padding, not independent resize handles.
    for border in (-1, 1):
        before = [(p["id"], p["left"], p["top"], p["width"], p["height"]) for p in panes(viewer)]
        drag(delta, border=border)
        assert [
            (p["id"], p["left"], p["top"], p["width"], p["height"]) for p in panes(viewer)
        ] == before
        fixed()
    for delta in (-200, 200):
        drag(delta)
        fixed()
        assert 0.15 <= saved(library).tab["tree"]["ratio"] <= 0.85
    # Keyboard navigation interrupts a held drag, even before its release.
    client.pump(0.4)
    rule = band()
    x, y = (rule["left"] + 1, 4) if direction == "right" else (rule["left"] + 4, rule["top"] + 1)
    client.type(f"\x1b[<0;{x};{y}M")
    wait(
        client,
        lambda: viewer.run("show-options", "-gv", "@viewer_resizing") == "1",
        "held drag did not capture",
    )
    client.type(direct_sequence("next-pane"))
    wait(
        client,
        lambda: viewer.run("display-message", "-p", "#{pane_id}") != focus,
        "keyboard pane navigation stopped after drag",
    )
    assert viewer.run("show-options", "-gv", "@viewer_resizing") == "0"
    client.type(f"\x1b[<0;{x};{y}m")
    assert shells.run("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}") == source_pids
    assert shells.run("list-keys") == source_keys
    print(
        f"PASS: padded {direction} separator drag, reverse, bounds, fixed gutters, saved ratio, "
        "unchanged clients/shells and keyboard navigation",
        flush=True,
    )


if __name__ == "__main__":
    for direction in ("right", "below"):
        with (
            tempfile.TemporaryDirectory(prefix="tw-split-drag-", dir="/tmp") as directory,
            FixtureResources(parent=Path(directory)) as resources,
        ):
            exercise(resources, direction)
