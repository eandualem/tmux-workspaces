"""Padded panes on a real PTY: gutters around content, a band between splits,
typing that still reaches the shell, and a click on a gutter that bounces."""

from __future__ import annotations

import json
import tempfile
from itertools import pairwise
from pathlib import Path

from tmux_workspaces.application import socket_path
from tmux_workspaces.model import leaves
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
        "#{pane_height} #{pane_active} #{window-style} #{pane-border-style}",
    ).splitlines():
        pane, gutter, left, top, width, height, active, style, border = line.split()
        rows.append(
            {
                "id": pane,
                "gutter": gutter == "1",
                "left": int(left),
                "top": int(top),
                "width": int(width),
                "height": int(height),
                "active": active == "1",
                "band": style == "bg=#31343b",
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

        wait(client, lambda: "Detach" in sidebar(), "sidebar failed to initialize")
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


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-gutters-", dir="/tmp") as directory:
        exercise(Path(directory))
