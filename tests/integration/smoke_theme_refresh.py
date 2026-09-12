"""Live theme transitions and helper discovery on disposable terminal sessions."""

import json
import shlex
import tempfile
import tomllib
from contextlib import closing
from pathlib import Path

from tests.integration.smoke_json_settings import replace
from tests.integration.smoke_themes import Screen, screen
from tests.integration.support import FixtureResources, click_button, saved, sidebar, wait
from tmux_workspaces.adapters.tmux import TmuxProvider
from tmux_workspaces.attachments import GROUPED_MARKER
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.model import leaves
from tmux_workspaces.persistence import Store
from tmux_workspaces.theme import parse_theme, preset_theme
from tmux_workspaces.tmux import Tmux


def exercise(resources):
    source = resources.server()
    ordinary, lookalike, legacy = "ordinary", "tw-1234-abcdef", "tw-5678-abcdef"
    for name in (ordinary, lookalike):
        source.run("-f", "/dev/null", "new-session", "-d", "-s", name, "/bin/sh")
    source.run("new-session", "-d", "-t", "=ordinary:", "-s", legacy)
    original_pane = source.run("list-panes", "-t", "=ordinary:", "-F", "#{pane_id}|#{pane_pid}")
    original_options = source.run("show-options", "-t", "=ordinary:")
    original_bindings = source.run("list-keys")
    library = resources.library()
    with closing(Store(library)) as store:
        model = store.load()
        model.pane["empty"] = True
        model.split("right", empty=True)
        model.attach(ordinary, source.socket)
        chooser, attached = [leaf["id"] for leaf in leaves(model.tab["tree"])]
        store.save(model)
    theme = resources.root / "theme.toml"
    theme.write_text(preset_theme("plain").to_toml())
    client = resources.client(
        [
            "--data-dir",
            str(library),
            "--source-socket",
            source.socket,
            "--theme",
            str(theme),
        ]
    )
    wait(client, lambda: client.manifest(library), "theme viewer missing")
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    wait(client, lambda: "Configure…" in sidebar(viewer), "theme sidebar missing")

    def content(leaf):
        return next(
            (
                row.split("|")[0]
                for row in viewer.run(
                    "list-panes", "-F", "#{pane_id}|#{@viewer_leaf_id}"
                ).splitlines()
                if row.endswith("|" + leaf)
            ),
            None,
        )

    def chooser_ready():
        pane = content(chooser)
        return pane and "Choose what this pane runs." in viewer.run(
            "capture-pane", "-p", "-t", pane
        )

    def helpers():
        return source.run(
            "list-sessions", "-f", f"#{{==:#{{{GROUPED_MARKER}}},1}}", "-F", "#{session_name}"
        ).splitlines()

    def gutters():
        return viewer.run("list-panes", "-f", "#{@viewer_gutter}", "-F", "#{pane_id}").splitlines()

    wait(client, chooser_ready, "initial chooser missing")
    wait(client, helpers, "marked helper missing")
    helper = helpers()[0]
    discovered = TmuxProvider(source.socket).read()
    assert set(discovered.sessions) == {ordinary, lookalike, legacy}, discovered
    assert helper not in viewer.run("capture-pane", "-p", "-t", content(chooser))
    assert not gutters(), "plain theme unexpectedly padded"

    def save_theme(data):
        client.output = b""
        click_button(client, viewer, "Edit theme…")
        wait(client, lambda: b"saved as TOML" in client.output, "theme editor missing")
        replace(client, json.dumps(data))
        client.type("\x13")
        wait(client, lambda: "Colors saved and applied" in sidebar(viewer), "theme save missing")
        wait(client, chooser_ready, "chooser did not return after theme save")

    def chooser_pid():
        return viewer.run("display-message", "-p", "-t", content(chooser), "#{pane_pid}")

    previous_pid = chooser_pid()
    save_theme({"preset": "default"})
    wait(client, lambda: len(gutters()) == 3, "plain-to-padded theme did not rebuild")
    assert chooser_pid() != previous_pid, "chooser retained the old theme process"
    assert viewer.run("show-options", "-pv", "-t", content(chooser), "window-style") == (
        "bg=" + preset_theme("default").surface
    )

    previous_pid = chooser_pid()
    retained_pane = content(chooser)
    emissions = resources.root / "chooser-output"
    viewer.run("pipe-pane", "-O", "-t", retained_pane, "cat > " + shlex.quote(str(emissions)))
    # Grounds remain identical; a role-only change must still reload the chooser.
    save_theme(
        {
            "preset": "default",
            "accent": {"foreground": "red"},
            "outline": {"foreground": "red"},
            "muted": {"foreground": 16},
        }
    )
    assert chooser_pid() != previous_pid, "role-only edit left a stale chooser palette"
    assert content(chooser) == retained_pane, "fixture did not exercise a respawned pane"
    wait(
        client,
        lambda: emissions.exists() and b"\x1b]104;16\x1b\\" in emissions.read_bytes(),
        "respawned chooser did not clear the inherited RGB slot",
    )
    viewer.run("pipe-pane", "-t", retained_pane)
    assert b"\x1b]104;16" not in client.output, "palette reset escaped the private pane"
    rules = viewer.run("list-panes", "-F", "#{pane_start_command}")
    assert "--color red" in rules, rules
    save_theme({"preset": "paper", "panel": "bright-blue", "surface": "brightgreen"})
    wait(
        client,
        lambda: (screen(viewer).at("Workspace", first_row=True) or (None, None))[1] == 12,
        "panel override did not change the sidebar interior",
    )
    wait(
        client,
        lambda: {style[1] for style in screen(viewer).lines[0][1]} == {10},
        "surface override did not reach the frame",
    )
    restored = tomllib.loads(theme.read_text())
    assert "background" not in restored["normal"], "Save froze an inherited panel background"
    restored["panel"] = "bright-red"
    save_theme(restored)
    wait(
        client,
        lambda: (screen(viewer).at("Workspace", first_row=True) or (None, None))[1] == 9,
        "panel edit after reopening did not apply",
    )
    assert parse_theme(theme.read_bytes()).panel == "brightred"
    assert parse_theme(theme.read_bytes()).surface == "brightgreen"
    previous_pid = chooser_pid()
    save_theme({"preset": "plain"})
    wait(client, lambda: not gutters(), "padded-to-plain theme retained gutters")
    assert chooser_pid() != previous_pid
    assert (
        source.run("list-panes", "-t", "=ordinary:", "-F", "#{pane_id}|#{pane_pid}")
        == original_pane
    )
    assert source.run("show-options", "-t", "=ordinary:") == original_options
    assert source.run("list-keys") == original_bindings
    assert set(TmuxProvider(source.socket).read().sessions) == {ordinary, lookalike, legacy}
    assert content(attached), "attached leaf disappeared after theme changes"
    print(
        "PASS: live plain/padded/role-only themes, refreshed chooser, named separator, "
        "marked-helper filtering and preserved source sessions",
        flush=True,
    )


def exercise_viewer_theme_snapshots(resources, launcher=None):
    library = resources.library()
    with closing(Store(library)) as store:
        model = store.load()
        model.pane["empty"] = True
        model.pane["cwd"] = str(resources.root)
        first = model.pane["id"]
        store.save(model)
    theme = resources.root / "shared-theme.toml"
    theme.write_text(
        parse_theme(
            b'preset = "plain"\n'
            b'[normal]\nforeground = "red"\n'
            b'[muted]\nforeground = "red"\n'
            b'[accent]\nforeground = "red"\n'
        ).to_toml()
    )
    arguments = [
        "--data-dir",
        str(library),
        "--source-socket",
        str(resources.root / "absent.sock"),
        "--theme",
        str(theme),
    ]
    clients, viewers = [], []
    for _ in range(2):
        client = resources.client(arguments, launcher=launcher, cwd=resources.root)
        wait(client, lambda client=client: client.manifest(library), "shared-theme viewer missing")
        clients.append(client)
        viewers.append(Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"]))

    def chooser(viewer, leaf):
        pane = next(
            (
                row.split("|")[0]
                for row in viewer.run(
                    "list-panes", "-F", "#{pane_id}|#{@viewer_leaf_id}"
                ).splitlines()
                if row.endswith("|" + leaf)
            ),
            None,
        )
        if not pane:
            return None
        style = Screen(viewer.run("capture-pane", "-e", "-p", "-t", pane)).at("Choose what")
        return (pane, viewer.run("display-message", "-p", "-t", pane, "#{pane_pid}"), style)

    def has_theme(viewer, leaf, color):
        content = chooser(viewer, leaf)
        panel = screen(viewer).at("Workspace", first_row=True)
        return bool(
            content and content[2] and content[2][0] == color and panel and panel[0] == color
        )

    for client, viewer in zip(clients, viewers, strict=True):
        wait(client, lambda viewer=viewer: has_theme(viewer, first, 1), "initial red theme missing")
    sidebar_pids = [
        viewer.run("display-message", "-p", "-t", "%0", "#{pane_pid}") for viewer in viewers
    ]
    green = {
        "preset": "plain",
        "normal": {"foreground": "green"},
        "muted": {"foreground": "green"},
        "accent": {"foreground": "green"},
    }

    def apply(client, viewer):
        client.output = b""
        click_button(client, viewer, "Edit theme…")
        wait(client, lambda: b"saved as TOML" in client.output, "shared-theme editor missing")
        replace(client, json.dumps(green))
        client.type("\x13")
        wait(
            client,
            lambda: "Colors saved and applied" in sidebar(viewer),
            "local theme apply missing",
        )

    apply(clients[0], viewers[0])
    wait(clients[0], lambda: has_theme(viewers[0], first, 2), "saving viewer did not apply green")
    assert parse_theme(theme.read_bytes()).roles["normal"].foreground == ("green",)
    assert has_theme(viewers[1], first, 1), "peer viewer changed its installed theme unexpectedly"
    clients[1].type(direct_sequence("new-tab"))
    wait(clients[1], lambda: len(saved(library).space["tabs"]) == 2, "peer new tab missing")
    second = saved(library).pane["id"]
    wait(
        clients[1],
        lambda: has_theme(viewers[1], second, 1),
        "new peer chooser reread shared theme instead of using the installed red theme",
    )
    previous = chooser(viewers[1], second)[1]
    clients[1].resize(144, 32)
    wait(
        clients[1],
        lambda: has_theme(viewers[1], second, 1) and chooser(viewers[1], second)[1] != previous,
        "resized peer chooser did not retain its installed red theme",
    )
    clients[0].type(direct_sequence("select-tab-2"))
    wait(clients[0], lambda: has_theme(viewers[0], second, 2), "saving viewer lost its green theme")
    apply(clients[1], viewers[1])
    wait(
        clients[1],
        lambda: has_theme(viewers[1], second, 2),
        "peer local apply did not update chooser",
    )
    for index, viewer in enumerate(viewers):
        assert viewer.run("display-message", "-p", "-t", "%0", "#{pane_pid}") == sidebar_pids[index]
    print(
        "PASS: two viewers retain their installed sidebar/chooser themes "
        "through new tabs and resize"
    )


if __name__ == "__main__":
    with (
        tempfile.TemporaryDirectory(prefix="tw-theme-refresh-", dir="/tmp") as directory,
        FixtureResources(parent=Path(directory)) as resources,
    ):
        exercise(resources)
    with FixtureResources(prefix="tw-theme-snapshots-") as resources:
        exercise_viewer_theme_snapshots(resources)
