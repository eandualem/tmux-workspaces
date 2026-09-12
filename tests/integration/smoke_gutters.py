"""Padded panes on a real PTY: gutters around content, a band between splits,
typing that still reaches the shell, and a click on a gutter that bounces."""

from __future__ import annotations

import json
import tempfile
from itertools import pairwise
from pathlib import Path

from tmux_workspaces.application import socket_path
from tmux_workspaces.model import leaves
from tmux_workspaces.persistence import Store
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux

from .support import OUTLINE, FixtureResources, click_button, open_terminal, saved, wait

# The shipped preset's surface: the padded panes' ground and the hidden borders.
BACKGROUND = "#292c33"


def panes(viewer: Tmux) -> list[dict]:
    rows = []
    for line in viewer.run(
        "list-panes",
        "-F",
        "#{pane_id} #{?@viewer_gutter,1,0} #{pane_left} #{pane_top} #{pane_width} "
        "#{pane_height} #{pane_active} #{window-style} #{pane-border-style} #{pane_start_command}",
    ).splitlines():
        pane, gutter, left, top, width, height, active, _style, border, *command = line.split()
        rows.append(
            {
                "id": pane,
                "gutter": gutter == "1",
                "left": int(left),
                "top": int(top),
                "width": int(width),
                "height": int(height),
                "active": active == "1",
                "band": "--rule" in " ".join(command),
                "border": border,
            }
        )
    return rows


def exercise(directory: Path) -> None:
    with FixtureResources(parent=directory) as resources:
        library = resources.library("demo", demo=True)
        client = resources.client(["--demo", "--data-dir", str(resources.root)])
        shells = Tmux(socket_path(library, "terminals"))
        wait(client, lambda: client.manifest(library), "viewer did not start")
        runtime = json.loads(client.manifest(library).read_text())
        viewer = Tmux(runtime["viewer_socket"])

        def sidebar():
            return viewer.run("capture-pane", "-p", "-t", "%0").translate(OUTLINE)

        wait(client, lambda: "Configure…" in sidebar(), "sidebar failed to initialize")
        wait(
            client,
            lambda: sum(p["gutter"] for p in panes(viewer)) == 2,
            "a single pane did not get its two edge gutters",
        )
        layout = sorted(panes(viewer), key=lambda p: p["left"])
        sidebar_pane, left_gutter, content, right_gutter = layout
        hidden = f"fg={BACKGROUND},bg={BACKGROUND}"
        assert not sidebar_pane["gutter"] and sidebar_pane["left"] == 0
        assert left_gutter["gutter"] and left_gutter["width"] == 1 and not left_gutter["band"]
        # Sidebar, its band border, the blank column, a hidden border, content.
        assert left_gutter["left"] == sidebar_pane["width"] + 1, layout
        assert content["left"] == left_gutter["left"] + 2 and not content["gutter"], layout
        assert content["border"] == hidden, content
        assert right_gutter["gutter"] and right_gutter["left"] + 1 == 160, layout
        assert right_gutter["left"] == content["left"] + content["width"] + 1, layout
        assert content["active"], "the shell beside the sidebar was not selected"

        # The shell inside the padded pane is the same ordinary shell as ever.
        terminal = "=" + Shells.name(saved(library).pane) + ":"
        client.type("printf 'PADDED:%s\\n' ok\r")
        wait(
            client,
            lambda: "PADDED:ok" in shells.run("capture-pane", "-p", "-t", terminal),
            "typing did not reach the padded shell",
        )

        # A split adds a band gutter between the two panes, each still padded.
        click_button(client, viewer, "Split →")
        wait(
            client,
            lambda: len(leaves(saved(library).tab["tree"])) == 2,
            "split did not create a pane",
        )
        open_terminal(client, viewer, library)
        wait(
            client,
            lambda: sum(p["gutter"] for p in panes(viewer)) == 3,
            "the split did not get a band gutter",
        )
        row = sorted((p for p in panes(viewer) if p["top"] == 0), key=lambda p: p["left"])
        kinds = [
            "sidebar"
            if p["left"] == 0
            else "band"
            if p["band"]
            else "blank"
            if p["gutter"]
            else "content"
            for p in row
        ]
        assert kinds == ["sidebar", "blank", "content", "band", "content", "blank"], kinds
        for earlier, later in pairwise(row):
            assert later["left"] == earlier["left"] + earlier["width"] + 1, row
        band = next(p for p in row if p["band"] and p["gutter"])
        assert band["width"] == 1 and band["height"] == row[0]["height"], band

        # Clicking the band or a blank column never leaves focus on it.
        before = next(p["id"] for p in panes(viewer) if p["active"])
        client.click(band["left"] + 1, 5)
        wait(
            client,
            lambda: (
                next(p["id"] for p in panes(viewer) if p["active"]) == before
                and not any(p["active"] and p["gutter"] for p in panes(viewer))
            ),
            "a click on the band left focus on it",
        )
        client.type("printf 'STILL:%s\\n' here\r")
        wait(
            client,
            lambda: any(
                "STILL:here"
                in shells.run("capture-pane", "-p", "-t", "=" + Shells.name(pane) + ":")
                for pane in leaves(saved(library).tab["tree"])
            ),
            "typing after the gutter click reached no shell",
        )
        click_button(client, viewer, "Exit")
        client.close_terminal()
    print(
        "PASS: with the terminal background known, every content pane is padded by a blank "
        "column on each side with hidden borders, a split gets a band between its panes, "
        "typing reaches the padded shells, and a click on a gutter bounces back",
        flush=True,
    )


def exercise_saved_minimum(directory: Path) -> None:
    """Saved extreme proportions must still leave room for nested separators."""
    with FixtureResources(parent=directory) as resources:
        library = resources.library()
        store = Store(library)
        model = store.load()
        model.split("below", str(resources.root))
        model.split("below", str(resources.root))
        model.tab["tree"]["ratio"] = 0.85
        for leaf in leaves(model.tab["tree"]):
            leaf["cwd"] = str(resources.root)
        store.save(model)
        store.close()
        client = resources.client(
            ["--data-dir", str(library), "--source-socket", str(resources.root / "absent.sock")],
            cols=80,
            rows=24,
            terminal_env={"HOME": str(resources.root)},
        )
        wait(client, lambda: client.manifest(library), "minimum-size saved viewer did not start")
        viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
        shells = Tmux(socket_path(library, "terminals"))

        def ready(cols, rows, count):
            current = panes(viewer)
            content = [pane for pane in current if not pane["gutter"] and pane["left"] != 0]
            return (
                len(content) == count
                and sum(pane["band"] for pane in current) == count - 1
                and max(pane["left"] + pane["width"] for pane in current) == cols
                and max(pane["top"] + pane["height"] for pane in current) == rows
                and all(pane["width"] >= 34 and pane["height"] >= 6 for pane in content)
            )

        wait(client, lambda: ready(80, 24, 3), "saved nested layout failed at its minimum size")
        wait(
            client,
            lambda: len(shells.run("list-clients").splitlines()) == 3,
            "minimum-size layout did not attach all ordinary shells",
        )
        original = shells.run("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}")
        terminal = "=" + Shells.name(model.pane) + ":"
        for step, (cols, rows, count) in enumerate(
            ((80, 60, 3), (80, 23, 1), (80, 24, 3), (160, 120, 3), (80, 24, 3))
        ):
            client.resize(cols, rows)
            wait(
                client,
                lambda cols=cols, rows=rows, count=count: ready(cols, rows, count),
                "nested layout did not recover on resize",
            )
            assert shells.run("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}") == original
            assert saved(library).tab["tree"]["ratio"] == 0.85
            if rows == 120:
                first = next(
                    pane
                    for pane in panes(viewer)
                    if not pane["gutter"] and pane["left"] != 0 and pane["top"] == 0
                )
                assert first["height"] == round((rows - 3) * 0.85), first
            token = f"{step}-{cols}x{rows}"
            client.type(f"printf 'LIMIT:%s\\n' {token}\r")
            wait(
                client,
                lambda token=token: (
                    f"LIMIT:{token}" in shells.run("capture-pane", "-p", "-t", terminal)
                ),
                "typing after minimum-size resize did not reach the preserved shell",
            )
        assert client.process.poll() is None
    print(
        "PASS: saved nested proportions open at 80x24, resize and restore without losing shells",
        flush=True,
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-gutters-", dir="/tmp") as directory:
        exercise(Path(directory))
        exercise_saved_minimum(Path(directory))
