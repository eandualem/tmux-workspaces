"""Burst navigation waits for slow ordinary attachments, on disposable sockets."""

import json
import os
import shutil
import sys
from contextlib import closing
from pathlib import Path

from tests.integration.support import FixtureResources, sidebar, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.model import leaves
from tmux_workspaces.persistence import Store
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux


def delayed_launcher(resources):
    """Delay a private source copy, without a production test flag or user files."""
    flag = resources.root / "delay"
    invocations = resources.root / "delayed-starts"
    gate = resources.root / "leaf_gate.py"
    gate.write_text(
        "import os, sys, time\nfrom pathlib import Path\n"
        f"if Path({str(flag)!r}).exists():\n"
        f" with Path({str(invocations)!r}).open('a') as out: out.write('delayed\\n')\n"
        " time.sleep(0.5)\n"
        "os.execv(sys.argv[1], sys.argv[1:])\n"
    )
    root = Path(__file__).resolve().parents[2]
    checkout = resources.root / "source"
    checkout.mkdir()
    shutil.copytree(
        root / "tmux_workspaces",
        checkout / "tmux_workspaces",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("run", "pyproject.toml"):
        shutil.copyfile(root / name, checkout / name)
    display = checkout / "tmux_workspaces" / "display.py"
    source = display.read_text()
    anchor = "    def _leaf_command(self, pane: dict | None) -> str:\n"
    wrapper = (
        anchor + "        import sys\n"
        "        value = self._original_leaf_command(pane)\n"
        "        argv = shlex.split(value)\n"
        f"        return (shlex.join([sys.executable, {str(gate)!r}, *argv])\n"
        "                if '--terminal' in argv else value)\n\n"
        "    def _original_leaf_command(self, pane: dict | None) -> str:\n"
    )
    assert source.count(anchor) == 1, "ordinary attachment injection point changed"
    display.write_text(source.replace(anchor, wrapper))
    return [sys.executable, str(checkout / "run")], flag, invocations


def exercise(resources):
    launcher, flag, invocations = delayed_launcher(resources)
    library = resources.library()
    with closing(Store(library)) as store:
        model = store.load()
        for index in range(2):
            if index:
                model.add_workspace("Second")
                model.add_tab()
            for direction in ("right", "below", "right"):
                model.split(direction, str(resources.root))
            for leaf in leaves(model.tab["tree"]):
                leaf["cwd"] = str(resources.root)
        spaces = model.state["workspaces"]
        model.state["selected"] = spaces[0]["id"]
        store.save(model)
    targets = ["=" + Shells.name({"id": space["tabs"][0]["focus"]}) + ":" for space in spaces]
    all_targets = [
        "=" + Shells.name(leaf) + ":"
        for space in spaces
        for leaf in leaves(space["tabs"][0]["tree"])
    ]
    shells = Tmux(socket_path(library, "terminals"))
    client = resources.client(
        ["--data-dir", str(library), "--source-socket", str(resources.root / "absent.sock")],
        launcher=launcher,
        terminal_env={"HOME": str(resources.root)},
        cwd=resources.root,
    )
    wait(client, lambda: client.manifest(library), "viewer missing")
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    wait(client, lambda: "Configure…" in sidebar(viewer), "sidebar missing")

    def packet(index):
        token = os.urandom(8).hex()
        # The expected marker never appears contiguously in the echoed command.
        return (
            direct_sequence(f"select-workspace-{index + 1}")
            + "printf 'READY_%s\\n' "
            + token
            + "\r",
            "READY_" + token,
            targets[index],
        )

    for index in (0, 1, 0):
        command, marker, target = packet(index)
        client.type(command)
        wait(
            client,
            lambda marker=marker, target=target: (
                marker in shells.run("capture-pane", "-J", "-S", "-", "-p", "-t", target)
            ),
            "warmup shell missing",
        )

    def processes():
        return shells.run("list-panes", "-a", "-F", "#{session_name}|#{pane_id}|#{pane_pid}")

    original_processes = processes()
    for delayed in (False, True):
        if delayed:
            flag.touch()
        packets = [packet(index) for index in (1, 0, 1, 0)]
        # One PTY write, no pump/delay between shortcut, typing, and next shortcut.
        os.write(client.master, "".join(command for command, _, _ in packets).encode())
        observed = {}

        def delivered(packets=packets, observed=observed):
            captures = {
                target: shells.run("capture-pane", "-J", "-S", "-", "-p", "-t", target)
                for target in all_targets
            }
            observed.update(
                {
                    marker: {
                        target: text.count(marker)
                        for target, text in captures.items()
                        if marker in text
                    }
                    for _, marker, _ in packets
                }
            )
            return all(observed[marker] == {target: 1} for _, marker, target in packets)

        try:
            wait(client, delivered, "burst commands missing or routed to the wrong shell")
        except AssertionError as error:
            raise AssertionError(f"delayed={delayed}, destinations={observed}") from error
        assert processes() == original_processes, "navigation replaced a persistent ordinary shell"
    assert len(invocations.read_text().splitlines()) >= 4, "slow attachment path was not exercised"
    print("Attachment readiness: normal and delayed bursts routed exactly once; shells preserved")


def main():
    with FixtureResources(prefix="tw-attachment-ready-") as resources:
        exercise(resources)


if __name__ == "__main__":
    main()
