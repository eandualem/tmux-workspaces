"""Burst navigation waits for slow ordinary attachments, on disposable sockets."""

import json
import os
import shutil
import sys
from contextlib import closing
from pathlib import Path

from tests.integration.support import FixtureResources, open_terminal, sidebar, wait
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


def exercise_click_during_readiness(resources):
    launcher, _, _ = delayed_launcher(resources)
    display = Path(launcher[-1]).parent / "tmux_workspaces" / "display.py"
    flag, entered, release, finished = (
        resources.root / name
        for name in ("probe-delay", "probe-entered", "probe-release", "probe-done")
    )
    source = display.read_text()
    anchor = "            return tmux.run(*args, timeout=remaining)\n"
    assert source.count(anchor) == 1, "readiness query injection point changed"
    source = source.replace(
        anchor,
        "            from pathlib import Path\n"
        f"            if tmux is self.shells.tmux and Path({str(flag)!r}).exists():\n"
        f"                Path({str(flag)!r}).unlink()\n"
        f"                Path({str(entered)!r}).touch()\n"
        "                until = time.monotonic() + 2\n"
        f"                while not Path({str(release)!r}).exists() and time.monotonic() < until:\n"
        "                    time.sleep(0.005)\n" + anchor,
    )
    anchor = "    def wait_for_input(self, tab: dict | None, *, timeout: float = 3) -> None:\n"
    assert source.count(anchor) == 1, "readiness completion injection point changed"
    source = source.replace(
        anchor,
        anchor + "        from pathlib import Path\n"
        "        try:\n"
        "            self._original_wait_for_input(tab, timeout=timeout)\n"
        "        finally:\n"
        f"            if Path({str(entered)!r}).exists(): Path({str(finished)!r}).touch()\n\n"
        "    def _original_wait_for_input(self, tab: dict | None, "
        "*, timeout: float = 3) -> None:\n",
    )
    display.write_text(source)
    library = resources.library()
    with closing(Store(library)) as store:
        model = store.load()
        for direction in ("right", "below", "right"):
            model.split(direction, str(resources.root))
        items = leaves(model.tab["tree"])
        for leaf in items:
            leaf["cwd"] = str(resources.root)
        model.pane["empty"] = True
        selected = model.pane["id"]
        store.save(model)
    client = resources.client(
        ["--data-dir", str(library), "--source-socket", str(resources.root / "absent.sock")],
        launcher=launcher,
        terminal_env={"HOME": str(resources.root)},
        cwd=resources.root,
    )
    wait(client, lambda: client.manifest(library), "click readiness viewer missing")
    viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
    shells = Tmux(socket_path(library, "terminals"))
    wait(client, lambda: "Configure…" in sidebar(viewer), "click readiness sidebar missing")

    def source_processes():
        return set(shells.run("list-panes", "-a", "-F", "#{session_name}|#{pane_pid}").splitlines())

    original = source_processes()
    flag.touch()
    open_terminal(client, viewer, library)
    wait(client, entered.exists, "ordinary readiness probe was not delayed")
    assert not finished.exists(), "readiness gate expired before the test could click"
    created = source_processes()
    assert len(created) == 4 and original <= created, "opening a shell replaced existing processes"

    def rows():
        return {
            row.split("|")[1]: row.split("|")
            for row in viewer.run(
                "list-panes",
                "-F",
                "#{pane_id}|#{@viewer_leaf_id}|#{@viewer_tab_id}|#{pane_active}|"
                "#{pane_dead}|#{pane_tty}|#{pane_pid}|#{pane_left}|#{pane_top}",
            ).splitlines()
        }

    before = rows()[selected]
    target = rows()[items[0]["id"]]
    client.click(int(target[7]) + 2, int(target[8]) + 1)
    after = rows()[selected]
    assert before[:3] + before[4:] == after[:3] + after[4:], (before, after)
    assert before[3] == "1" and after[3] == "0", (before, after)
    release.touch()
    wait(client, finished.exists, "readiness did not finish after the content click")
    assert rows()[items[0]["id"]][3] == "1", "readiness stole focus from the clicked terminal"
    token = os.urandom(8).hex()
    marker = "CLICK_READY_" + token
    client.type("printf 'CLICK_READY_%s\\n' " + token + "\r")
    target_session = "=" + Shells.name(items[0]) + ":"
    wait(
        client,
        lambda: marker in shells.run("capture-pane", "-p", "-t", target_session),
        "typing after readiness interruption missed the clicked terminal",
    )
    for leaf in items:
        text = shells.run("capture-pane", "-p", "-t", "=" + Shells.name(leaf) + ":")
        assert text.count(marker) == (1 if leaf == items[0] else 0), "click input misrouted"
    assert "Terminal attachment" not in sidebar(viewer), "legitimate click left a readiness error"
    assert created == source_processes(), "readiness interruption replaced an existing shell"
    print(
        "Attachment readiness: pending shell creation yields to a content click; "
        "input and shells preserved"
    )


def main():
    with FixtureResources(prefix="tw-attachment-ready-") as resources:
        exercise(resources)
    with FixtureResources(prefix="tw-attachment-click-") as resources:
        exercise_click_during_readiness(resources)


if __name__ == "__main__":
    main()
