"""Real-PTY startup failures leave fresh libraries and tmux servers untouched."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from tests.integration.support import FixtureResources, wait
from tmux_workspaces.application import socket_path

ROOT = Path(__file__).resolve().parents[2]


def fake_tmux(directory: Path, version: str) -> Path:
    """Record every invocation; this executable cannot start or contact a server."""
    log = directory / "tmux.calls"
    program = directory / "tmux_probe.py"
    program.write_text(
        "import json, sys\n"
        f"with open({str(log)!r}, 'a') as output:\n"
        " output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:] != ['-V']:\n"
        " raise SystemExit('fixture refuses any tmux server command')\n"
        f"print({version!r})\n"
    )
    executable = directory / "tmux"
    executable.write_text(
        "#!/bin/sh\nexec " + shlex.join([sys.executable, str(program)]) + ' "$@"\n'
    )
    executable.chmod(0o700)
    return log


def failure(
    name: str,
    expected: tuple[str, ...],
    *,
    version="tmux 3.3",
    term="xterm-256color",
    missing_tmux=False,
    missing_term=False,
    missing_curses=False,
) -> None:
    with FixtureResources(prefix="tw-preflight-") as resources:
        library = resources.library()
        binary_dir = resources.root / "bin"
        binary_dir.mkdir()
        log = None if missing_tmux else fake_tmux(binary_dir, version)
        source = resources.root / "absent-source.sock"
        env = {
            "PATH": str(binary_dir),
            "TERM": term,
            "XDG_CONFIG_HOME": str(resources.root / "config"),
            "XDG_DATA_HOME": str(resources.root / "data"),
        }
        launcher = None
        if missing_term or missing_curses:
            program = "import os, runpy, sys\n"
            if missing_term:
                program += "os.environ.pop('TERM', None)\n"
            if missing_curses:
                program += (
                    "class NoCurses:\n"
                    " def find_spec(self, fullname, path=None, target=None):\n"
                    "  if fullname in ('curses', '_curses'):\n"
                    "   raise ModuleNotFoundError('fixture: Python built without curses')\n"
                    "sys.meta_path.insert(0, NoCurses())\n"
                )
            program += (
                f"sys.path.insert(0, {str(ROOT)!r})\n"
                f"runpy.run_path({str(ROOT / 'run')!r}, run_name='__main__')\n"
            )
            launcher = [sys.executable, "-c", program]
        client = resources.client(
            ["--data-dir", str(library), "--source-socket", str(source), "--no-keymap"],
            terminal_env=env,
            launcher=launcher,
            cwd=resources.root,
        )
        wait(client, lambda: client.process.poll() is not None, f"{name}: startup did not fail")
        client.pump(0.1)
        output = client.output.decode(errors="replace")
        assert client.process.returncode != 0, (name, output)
        assert "Traceback" not in output, (name, output)
        assert "\x1b[" not in output, (name, "failure entered the screen", output)
        assert len(output.splitlines()) <= 2, (name, output)
        for fragment in expected:
            assert fragment in output, (name, fragment, output)
        assert not list(library.iterdir()), (name, "startup wrote library state")
        assert not source.exists(), (name, "startup created an attachment socket")
        shell_socket = Path(socket_path(library, "terminals"))
        assert not list(shell_socket.parent.glob(shell_socket.name + "*")), name
        view_socket = Path(socket_path(library, "view-"))
        assert not list(view_socket.parent.glob(view_socket.name.removesuffix(".sock") + "*")), name
        if log is not None:
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            assert calls == [["-V"]], (name, "unexpected tmux command", calls)
        print(f"PASS: {name} failed clearly before creating runtime state")


def main() -> None:
    failure("missing tmux", ("tmux not found", "3.3", "Install"), missing_tmux=True)
    failure("old tmux", ("tmux 3.2a detected", "3.3", "Upgrade"), version="tmux 3.2a")
    failure("missing TERM", ("TERM is missing", "correct TERM"), missing_term=True)
    failure(
        "unknown TERM",
        ("tw-no-such-terminal-preflight", "terminfo", "Install"),
        term="tw-no-such-terminal-preflight",
    )
    failure(
        "monochrome terminal", ("vt100", "0 colors", "8 colors", "matching terminfo"), term="vt100"
    )
    failure(
        "missing curses",
        ("curses support is unavailable", "Python 3.11", "ncurses support"),
        missing_curses=True,
    )
    print("PASS: six real-PTY preflight failures; no library writes or tmux server commands")


if __name__ == "__main__":
    main()
