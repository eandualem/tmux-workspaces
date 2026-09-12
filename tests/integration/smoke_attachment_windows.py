"""External helper window selection survives viewer-only recreation."""

import json
import os
from contextlib import closing

from tests.integration.support import FixtureResources, sidebar, wait
from tmux_workspaces.attachments import GROUPED_MARKER
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.persistence import Store
from tmux_workspaces.tmux import Tmux


def exercise(resources):
    source = resources.server()
    source.run(
        "-f",
        "/dev/null",
        "new-session",
        "-d",
        "-s",
        "sample",
        "-e",
        "HOME=" + str(resources.root),
        "-c",
        str(resources.root),
        "/bin/sh",
    )
    for name in ("second", "third"):
        source.run(
            "new-window", "-d", "-t", "=sample:", "-n", name, "-c", str(resources.root), "/bin/sh"
        )
    windows = source.run("list-windows", "-t", "=sample:", "-F", "#{window_id}").splitlines()
    source.run("select-window", "-t", "=sample:" + windows[1])
    original_options = source.run("show-options", "-t", "=sample:")
    original_bindings = source.run("list-keys")

    def processes():
        return source.run("list-panes", "-s", "-t", "=sample:", "-F", "#{pane_id}|#{pane_pid}")

    original_processes = processes()
    library = resources.library()
    with closing(Store(library)) as store:
        model = store.load()
        model.attach("sample", source.socket)
        leaf_id = model.tab["focus"]
        first = model.tab["id"]
        model.add_tab("Other", empty=True)
        model.space["selected"] = first
        store.save(model)
    client = resources.client(["--data-dir", str(library), "--source-socket", source.socket])
    wait(client, lambda: client.manifest(library), "viewer missing")
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    wait(client, lambda: "Configure…" in sidebar(viewer), "sidebar missing")

    def helper():
        displayed = viewer.run("list-panes", "-F", "#{@viewer_leaf_id}|#{pane_tty}")
        tty = next(
            (
                row.split("|", 1)[1]
                for row in displayed.splitlines()
                if row.startswith(leaf_id + "|")
            ),
            None,
        )
        clients = source.run(
            "list-clients",
            "-F",
            f"#{{client_tty}}|#{{session_name}}|#{{window_id}}|#{{{GROUPED_MARKER}}}",
        )
        return next(
            (
                parts[1:3]
                for row in clients.splitlines()
                if len(parts := row.split("|")) == 4 and parts[0] == tty and parts[3] == "1"
            ),
            None,
        )

    wait(
        client,
        lambda: (found := helper()) and found[1] == windows[1],
        "helper did not start on source's current window",
    )
    source.run("select-window", "-t", "=" + helper()[0] + ":" + windows[2])

    def retained():
        return (found := helper()) and found[1] == windows[2]

    wait(client, retained, "helper window selection failed")

    def routed(label):
        token = os.urandom(6).hex()
        marker = "WINDOW_" + token
        client.type("printf 'WINDOW_%s\\n' " + token + "\r")
        wait(
            client,
            lambda: (
                marker
                in source.run("capture-pane", "-J", "-S", "-", "-p", "-t", "=sample:" + windows[2])
            ),
            label,
        )
        for window in windows[:2]:
            assert marker not in source.run(
                "capture-pane", "-J", "-S", "-", "-p", "-t", "=sample:" + window
            ), "input reached another source window"
        assert source.run("display-message", "-p", "-t", "=sample:", "#{window_id}") == windows[1]
        assert processes() == original_processes

    routed("initial helper input missing")
    before = helper()[0]
    client.resize(135, 32)
    wait(client, lambda: retained() and helper()[0] != before, "resize lost the selected window")
    routed("resized helper input missing")
    client.type(direct_sequence("select-tab-2"))
    wait(client, lambda: helper() is None, "other tab did not replace attachment")
    client.type(direct_sequence("select-tab-1"))
    wait(client, retained, "tab navigation lost the selected window")
    routed("returned helper input missing")
    assert source.run("show-options", "-t", "=sample:") == original_options
    assert source.run("list-keys") == original_bindings
    print("PASS: helper window survives resize/navigation; source selection and shells unchanged")


if __name__ == "__main__":
    with FixtureResources(prefix="tw-attachment-window-") as resources:
        exercise(resources)
