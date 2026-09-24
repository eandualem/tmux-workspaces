"""Open a disposable viewer in a native macOS terminal and capture its window.

The sample library from ./preview is seeded into a temporary directory, opened in
Ghostty or Terminal.app, and only that window is captured with screencapture(1).
The user's library, sessions and other windows are never touched: the viewer runs
in demo mode on its own tmux servers, which --close stops by their own sockets.

    python3 -m scripts.capture_window --terminal ghostty --out ghostty.png
    python3 -m scripts.capture_window --terminal terminal --out terminal.png --keep-open
    python3 -m scripts.capture_window --recapture DIR --out again.png --crop 0,2000,900,146,2
    python3 -m scripts.capture_window --close DIR

The process running this needs macOS Screen Recording permission; without it the
capture shows only the desktop background.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A process's largest on-screen window, from CoreGraphics. Ghostty also owns
# small helper windows at the normal layer; the terminal window is the largest.
WINDOWS_JS = """
ObjC.import("CoreGraphics");
function run(argv) {
  const list = ObjC.deepUnwrap(ObjC.castRefToObject(
    $.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly, 0)));
  const area = w => w.kCGWindowBounds.Width * w.kCGWindowBounds.Height;
  const own = list.filter(w => w.kCGWindowOwnerPID === Number(argv[0]) && w.kCGWindowLayer === 0
    && area(w) > 200 * 200).sort((a, b) => area(b) - area(a));
  return own.length ? String(own[0].kCGWindowNumber) : "";
}
"""

# Whether any of a grid of sampled pixels is not black. A window on a Space that
# is not showing, such as a full-screen one, captures as all black.
PAINTED_JS = """
ObjC.import("AppKit");
function run(argv) {
  const rep = $.NSBitmapImageRep.imageRepWithData($.NSData.dataWithContentsOfFile(argv[0]));
  const w = rep.pixelsWide, h = rep.pixelsHigh;
  for (let i = 1; i < 16; i++) for (let j = 1; j < 16; j++) {
    const c = rep.colorAtXY(Math.floor(w * i / 16), Math.floor(h * j / 16));
    if (c.redComponent + c.greenComponent + c.blueComponent > 0.01) return "yes";
  }
  return "no";
}
"""

# Crop a PNG and enlarge it without smoothing, so single pixels stay visible.
CROP_JS = """
ObjC.import("AppKit");
function run(argv) {
  const [src, dst, x, y, w, h, scale] = argv.map((v, i) => i < 2 ? v : Number(v));
  const rep = $.NSBitmapImageRep.imageRepWithData($.NSData.dataWithContentsOfFile(src));
  const part = $.CGImageCreateWithImageInRect(rep.CGImage, $.CGRectMake(x, y, w, h));
  const out = $.NSBitmapImageRep.alloc
    .initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
      null, w * scale, h * scale, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0);
  const ctx = $.NSGraphicsContext.graphicsContextWithBitmapImageRep(out);
  $.NSGraphicsContext.setCurrentContext(ctx);
  ctx.imageInterpolation = $.NSImageInterpolationNone;
  $.CGContextDrawImage(ctx.CGContext, $.CGRectMake(0, 0, w * scale, h * scale), part);
  const png = out.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $());
  png.writeToFileAtomically(dst, true);
}
"""


def jxa(script: str, *args: str) -> str:
    return subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script, *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def ghostty_pids() -> set[int]:
    found = subprocess.run(["pgrep", "-x", "ghostty"], capture_output=True, text=True).stdout
    return {int(pid) for pid in found.split()}


def wait(predicate, description: str, timeout: float = 20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.3)
    raise RuntimeError(description)


def viewer_args(library: Path) -> list[str]:
    """Demo mode, with a theme and keymap of its own: the viewer never opens
    the user's settings files, even if someone clicks Configure in it."""
    return [
        "--data-dir",
        str(library),
        "--demo",
        "--theme",
        str(library.parent / "theme.toml"),
        "--no-keymap",
    ]


def open_ghostty(library: Path) -> dict:
    before = ghostty_pids()
    subprocess.run([str(ROOT / "ghostty"), *viewer_args(library)], check=True)
    pid = wait(lambda: min(ghostty_pids() - before, default=None), "Ghostty did not start")
    window = wait(lambda: jxa(WINDOWS_JS, str(pid)), "no Ghostty window")
    return {"terminal": "ghostty", "pid": pid, "window": int(window)}


def open_terminal_app(library: Path) -> dict:
    command = shlex.join([sys.executable, str(ROOT / "run"), *viewer_args(library)])
    command = "exec " + command
    window = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "Terminal"',
            "-e",
            f"set t to do script {json.dumps(command)}",
            "-e",
            "return id of window 1 whose tabs contains t",
            "-e",
            "end tell",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {"terminal": "terminal", "window": int(window)}


def viewer_ready(library: Path) -> bool:
    """The newest window of the demo library has drawn its sidebar."""
    try:
        manifests = sorted(
            (library / "demo" / "windows").glob("*/runtime.json"), key=lambda p: p.stat().st_mtime
        )
        if not manifests:
            return False
        # The viewer may still be writing its manifest.
        socket = json.loads(manifests[-1].read_text())["viewer_socket"]
    except (OSError, ValueError, KeyError):
        return False
    shown = subprocess.run(
        ["tmux", "-S", socket, "capture-pane", "-p", "-t", "%0"], capture_output=True, text=True
    )
    return "Configure" in shown.stdout


def capture(state: dict, out: Path, crops: list[str]) -> None:
    subprocess.run(["screencapture", "-x", "-o", "-l", str(state["window"]), str(out)], check=True)
    if jxa(PAINTED_JS, str(out)) != "yes":
        raise RuntimeError(
            f"{out} is all black: the window is not showing, for example full screen on "
            "another Space, or this process lacks Screen Recording permission"
        )
    print(out)
    for index, crop in enumerate(crops, 1):
        x, y, w, h, *scale = crop.split(",")
        target = out.with_name(f"{out.stem}-crop{index}.png")
        jxa(CROP_JS, str(out), str(target), x, y, w, h, scale[0] if scale else "1")
        print(target)


def close(directory: Path) -> None:
    state = json.loads((directory / "capture.json").read_text())
    if state["terminal"] == "ghostty":
        with contextlib.suppress(ProcessLookupError):
            os.kill(state["pid"], signal.SIGTERM)
    else:
        # Stop the viewer first: Terminal asks before closing a window whose
        # processes still run, and the attached tmux client ends with its server.
        stop_sample(directory / "library")
        time.sleep(1)
        subprocess.run(
            [
                "osascript",
                "-e",
                f'tell application "Terminal" to close (every window whose id is {state["window"]})'
                " saving no",
            ],
            capture_output=True,
        )
    time.sleep(1)
    stop_sample(directory / "library")
    shutil.rmtree(directory)


def stop_sample(library: Path) -> None:
    """Stop the sample's own tmux servers: its viewers', then its shells'.
    Closing a viewer keeps its shells by design; these are the sample's own."""
    from tmux_workspaces.application import socket_path

    for manifest in (library / "demo" / "windows").glob("*/runtime.json"):
        with contextlib.suppress(OSError, ValueError, KeyError):
            socket = json.loads(manifest.read_text())["viewer_socket"]
            subprocess.run(["tmux", "-S", socket, "kill-server"], capture_output=True)
    subprocess.run(
        ["tmux", "-S", socket_path(library / "demo", "terminals"), "kill-server"],
        capture_output=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--terminal", choices=("ghostty", "terminal"), default="ghostty")
    parser.add_argument("--out", type=Path, help="PNG to write (default: in the run directory)")
    parser.add_argument(
        "--crop",
        action="append",
        default=[],
        metavar="X,Y,W,H[,SCALE]",
        help="also write an enlarged region, in the capture's pixels; repeatable",
    )
    parser.add_argument("--keep-open", action="store_true", help="leave the window open")
    parser.add_argument("--wait", type=float, default=1.5, help="seconds to let it paint")
    parser.add_argument("--recapture", type=Path, metavar="DIR", help="capture a kept window")
    parser.add_argument("--close", type=Path, metavar="DIR", help="close a kept window")
    options = parser.parse_args()
    if sys.platform != "darwin":
        print("capture_window: native capture needs macOS", file=sys.stderr)
        return 1
    sys.path.insert(0, str(ROOT))
    if options.close:
        close(options.close)
        return 0
    if options.recapture:
        state = json.loads((options.recapture / "capture.json").read_text())
        capture(state, options.out or options.recapture / "capture.png", options.crop)
        return 0

    from tmux_workspaces.ui_preview import seed

    directory = Path(tempfile.mkdtemp(prefix="tmux-workspaces-capture-"))
    library = directory / "library"
    seed(library)
    opener = open_ghostty if options.terminal == "ghostty" else open_terminal_app
    try:
        state = opener(library) | {"library": str(library)}
    except BaseException:
        # No window to close yet: stop what the viewer started, which also
        # ends a Ghostty instance whose command was the viewer.
        stop_sample(library)
        shutil.rmtree(directory)
        raise
    (directory / "capture.json").write_text(json.dumps(state))
    try:
        wait(lambda: viewer_ready(library), "the viewer did not draw its sidebar", timeout=30)
        time.sleep(options.wait)
        capture(state, options.out or directory / "capture.png", options.crop)
    except BaseException:
        close(directory)
        raise
    if options.keep_open:
        print(f"kept open: {directory}")
    else:
        close(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
