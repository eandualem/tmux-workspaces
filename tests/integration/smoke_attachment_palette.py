"""Reused viewer panes discard chooser colors before any terminal attachment."""

import json
import re
import shlex
import sys
from pathlib import Path

from tests.integration.support import FixtureResources, wait


def palette_reply(response: bytes, slot: int) -> str | None:
    """Decode one OSC 4 query followed by tmux's ordered device-status reply."""
    if not response.endswith(b"\x1b[0n"):
        raise ValueError(f"palette query has no completion reply: {response!r}")
    payload = response[:-4]
    if not payload:
        return None  # tmux has no pane override for this slot.
    match = re.fullmatch(
        rb"\x1b\]4;(?:([0-9]+);)?(rgb:[0-9a-fA-F]{4}/[0-9a-fA-F]{4}/[0-9a-fA-F]{4})(?:\x1b\\|\x07)",
        payload,
    )
    if not match or (match[1] is not None and int(match[1]) != slot):
        raise ValueError(f"unexpected palette reply for slot {slot}: {response!r}")
    return match[2].decode().lower()


def painted_color(output: bytes, marker: str):
    """Read the RGB/indexed foreground actually emitted to this owned terminal."""
    foreground = None
    text, colors = [], []
    tokens = re.compile(rb"\x1b\[([0-9;:?]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
    position = 0
    for match in tokens.finditer(output):
        chunk = output[position : match.start()].decode(errors="replace")
        text.extend(chunk)
        colors.extend([foreground] * len(chunk))
        if match[2] == b"m":
            codes = [int(part or 0) for part in match[1].split(b";")]
            index = 0
            while index < len(codes):
                code = codes[index]
                if code in (0, 39):
                    foreground = None
                elif code in (38, 48) and codes[index + 1 : index + 2] == [2]:
                    if code == 38:
                        foreground = tuple(codes[index + 2 : index + 5])
                    index += 4
                elif code in (38, 48) and codes[index + 1 : index + 2] == [5]:
                    if code == 38:
                        foreground = ("index", codes[index + 2])
                    index += 2
                elif 30 <= code <= 37:
                    foreground = ("index", code - 30)
                index += 1
        position = match.end()
    chunk = output[position:].decode(errors="replace")
    text.extend(chunk)
    colors.extend([foreground] * len(chunk))
    start = "".join(text).rfind(marker)
    if start >= 0 and len(set(colors[start : start + len(marker)])) == 1:
        return colors[start]
    return None


PROGRAM = r"""
import json, os, select, sys, termios, time, tty
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from tmux_workspaces.attachments import leaf_main
from tmux_workspaces.theme import DEFAULT_THEME, palette_sequence
from tests.integration.smoke_attachment_palette import palette_reply

def colors():
    result = {}
    for slot in (16, 7):
        # Older tmux omits the index in OSC 4 replies. Query each slot alone;
        # the following DSR proves processing finished even if it had no override.
        os.write(1, f"\x1b]4;{slot};?\x1b\\\x1b[5n".encode())
        response = b""
        deadline = time.monotonic() + 3
        while not response.endswith(b"\x1b[0n"):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([0], [], [], remaining)[0]:
                raise RuntimeError(f"palette query timed out: {response!r}")
            response += os.read(0, 4096)
        value = palette_reply(response, slot)
        if value is not None:
            result[str(slot)] = value
    return result

mode, result = sys.argv[2], Path(sys.argv[3])
def paint(phase):
    # Fresh rows make tmux emit the complete marker instead of a changed suffix.
    row = 3 if phase == "seed" else 4 if phase.endswith("_before") else 5
    os.write(1, f"\x1b[{row};1H\x1b[38;5;16mPALETTE_{phase}_16\x1b[0m".encode())
original = termios.tcgetattr(0)
tty.setraw(0)
try:
    if mode == "seed":
        palette = DEFAULT_THEME.resolve(256)
        os.write(1, palette_sequence({name: slot for slot, name in palette.rgb.items()}).encode())
        os.write(1, b"\x1b]4;7;#123456\x1b\\")
        paint("seed")
        result.write_text(json.dumps(colors()))
        while True:
            time.sleep(60)
    else:
        before = colors()
        paint(mode + "_before")
        result.with_suffix(".before").write_text(json.dumps(before))
        deadline = time.monotonic() + 10
        while not result.with_suffix(".continue").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("fixture did not observe pre-attachment colors")
            time.sleep(0.01)
        class Done(Exception):
            pass
        def attach(*args, **kwargs):
            paint(mode + "_after")
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
    viewer.run("set-option", "-as", "terminal-features", ",xterm*:RGB")
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
        wait(
            client,
            lambda: painted_color(client.output, "PALETTE_seed_16") == (204, 204, 204),
            "chooser RGB color was not rendered",
        )
        result = resources.root / (kind + ".json")
        viewer.run("respawn-pane", "-k", "-t", pane, command(kind, result))
        wait(
            client,
            lambda result=result, kind=kind: (
                result.with_suffix(".before").exists()
                and painted_color(client.output, f"PALETTE_{kind}_before_16") == (204, 204, 204)
            ),
            "respawn did not retain the rendered chooser color",
        )
        result.with_suffix(".continue").touch()
        wait(client, result.exists, "terminal startup did not query its palette")
        observed = json.loads(result.read_text())
        assert observed["before"] == seeded, "fixture did not retain chooser palette across respawn"
        # tmux <=3.5 cannot query extended slots. The terminal rendering is the
        # oracle on every version; an absent query reply alone never proves reset.
        wait(
            client,
            lambda kind=kind: (
                painted_color(client.output, f"PALETTE_{kind}_after_16")
                in (("index", 16), (0, 0, 0))
            ),
            "chooser override remained visible after reset",
        )
        assert observed["after"]["7"] == seeded["7"], "startup reset an unowned palette slot"
    print("PASS: ordinary and external startup reset chooser RGB slots in reused pane only")


if __name__ == "__main__":
    with FixtureResources(prefix="tw-attachment-palette-") as resources:
        exercise(resources)
