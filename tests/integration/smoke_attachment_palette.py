"""Reused viewer panes discard chooser colors before any terminal attachment."""

import json
import shlex
import sys
from pathlib import Path

from tests.integration.support import FixtureResources, wait

PROGRAM = r"""
import json, os, select, sys, termios, time, tty
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from tmux_workspaces.attachments import leaf_main
from tmux_workspaces.theme import DEFAULT_THEME, palette_sequence

def colors():
    # Query an unowned sentinel last. Its response proves tmux processed both
    # queries even when a reset slot has no override and produces no response.
    os.write(1, b"\x1b]4;16;?\x1b\\\x1b]4;7;?\x1b\\")
    response = b""
    deadline = time.monotonic() + 3
    while b"\x1b]4;7;" not in response or not response.endswith(b"\x1b\\"):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([0], [], [], remaining)[0]:
            raise RuntimeError(f"palette query timed out: {response!r}")
        response += os.read(0, 4096)
    return {part.split(";", 2)[1]: part.split(";", 2)[2]
            for part in response.decode().split("\x1b\\") if part.startswith("\x1b]4;")}

mode, result = sys.argv[2], Path(sys.argv[3])
original = termios.tcgetattr(0)
tty.setraw(0)
try:
    if mode == "seed":
        palette = DEFAULT_THEME.resolve(256)
        os.write(1, palette_sequence({name: slot for slot, name in palette.rgb.items()}).encode())
        os.write(1, b"\x1b]4;7;#123456\x1b\\")
        result.write_text(json.dumps(colors()))
        while True:
            time.sleep(60)
    else:
        before = colors()
        class Done(Exception):
            pass
        def attach(*args, **kwargs):
            result.write_text(json.dumps({"before": before, "after": colors()}))
            raise Done
        args = SimpleNamespace(agent="external" if mode == "external" else "",
                               terminal="" if mode == "external" else "ordinary",
                               source_socket="/unused/source", host_socket=None, host_pane=None)
        with patch("tmux_workspaces.attachments.subprocess.run", side_effect=attach):
            try:
                leaf_main(args)
            except Done:
                pass
finally:
    termios.tcsetattr(0, termios.TCSANOW, original)
"""


def exercise(resources):
    viewer = resources.server("viewer")
    root = str(Path(__file__).resolve().parents[2])

    def command(mode, result):
        return shlex.join([sys.executable, "-c", PROGRAM, root, mode, str(result)])

    seed = resources.root / "seed.json"
    viewer.run(
        "-f",
        "/dev/null",
        "new-session",
        "-d",
        "-s",
        "probe",
        "-e",
        "HOME=" + str(resources.root),
        command("seed", seed),
    )
    viewer.run("set-option", "-w", "-t", "=probe:", "remain-on-exit", "on")
    pane = viewer.run("display-message", "-p", "-t", "=probe:", "#{pane_id}")
    client = resources.client(
        [], launcher=["tmux", "-S", viewer.socket, "attach-session", "-t", "=probe:"]
    )
    for kind in ("ordinary", "external"):
        if kind == "external":
            seed.unlink()
            viewer.run("respawn-pane", "-k", "-t", pane, command("seed", seed))
        wait(client, seed.exists, "chooser palette was not installed")
        seeded = json.loads(seed.read_text())
        result = resources.root / (kind + ".json")
        viewer.run("respawn-pane", "-k", "-t", pane, command(kind, result))
        wait(client, result.exists, "terminal startup did not query its palette")
        observed = json.loads(result.read_text())
        assert observed["before"] == seeded, "fixture did not retain chooser palette across respawn"
        assert "rgb:0000/0000/0000" not in seeded["16"], "chooser RGB precondition missing"
        assert observed["after"].get("16", "rgb:0000/0000/0000") == "rgb:0000/0000/0000", observed
        assert observed["after"]["7"] == seeded["7"], "startup reset an unowned palette slot"
    print("PASS: ordinary and external startup reset chooser RGB slots in reused pane only")


if __name__ == "__main__":
    with FixtureResources(prefix="tw-attachment-palette-") as resources:
        exercise(resources)
