"""Explicit viewer refresh over real PTYs: one replacement, untouched work beneath it."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import sys
import tempfile
from pathlib import Path

from tests.integration.smoke_themes import pair_colors, styles
from tests.integration.support import (
    Client,
    FixtureResources,
    click_button,
    saved,
    sidebar,
    wait,
)
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = "prefix = 'C-g'\n"
EDITED_CONFIG = "prefix = 'C-a'\n"
BROKEN_CONFIG = "prefix = 'C-a'\nbindings = 5\n"


def run(directory: Path) -> None:
    with FixtureResources(parent=directory) as resources:
        exercise(resources)


def recovery(directory: Path) -> None:
    with FixtureResources(parent=directory) as resources:
        exercise_recovery(resources)


def supervision(directory: Path) -> None:
    with FixtureResources(parent=directory) as resources:
        exercise_supervision(resources)


def copied_checkout(directory: Path) -> Path:
    """Own a disposable copy, so faults are injected far from this checkout."""
    checkout = directory / "checkout"
    checkout.mkdir()
    shutil.copytree(
        ROOT / "tmux_workspaces",
        checkout / "tmux_workspaces",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("run", "pyproject.toml"):
        shutil.copy(ROOT / name, checkout / name)
    return checkout


def runtime(client: Client, library: Path) -> dict:
    return json.loads(client.manifest(library).read_text())


def terminal(library: Path) -> str:
    return "=" + Shells.name(saved(library).pane) + ":"


def ready(client: Client, library: Path) -> bool:
    manifest = client.manifest(library)
    if manifest is None:
        return False
    return "Layouts saved" in sidebar(Tmux(json.loads(manifest.read_text())["viewer_socket"]))


def title_pair(client: Client):
    """The header's own color pair, or None until that row has been drawn."""
    drawn = styles(Tmux(client.viewer_socket)).get("title")
    return pair_colors(drawn) if drawn else None


def start(
    resources: FixtureResources,
    library: Path,
    source: Tmux,
    config: Path,
    checkout: Path | None = None,
    theme: Path | None = None,
) -> Client:
    client = resources.client(
        [
            "--data-dir",
            str(library),
            "--source-socket",
            source.socket,
            "--keymap",
            str(config),
            *(["--theme", str(theme)] if theme else []),
        ],
        launcher=[sys.executable, str(checkout / "run")] if checkout else None,
    )
    # A busy machine can spend a while on a private server plus its first shell.
    wait(client, lambda: ready(client, library), "viewer did not become ready", timeout=25)
    client.viewer_socket = runtime(client, library)["viewer_socket"]
    return client


def displays(library: Path) -> list[Path]:
    """Private display servers still answering for this library."""
    prefix = Path(socket_path(library, "view-"))
    sockets = sorted(prefix.parent.glob(prefix.name.removesuffix(".sock") + "*.sock"))
    return [item for item in sockets if Tmux(str(item)).run("list-sessions", check=False)]


def confirmation(client: Client) -> None:
    client.type("\x07f")
    wait(client, lambda: "Refresh viewer" in sidebar_of(client), "confirmation did not open")


def break_settings(checkout: Path) -> str:
    """Break only what reading the settings needs, not delivering a shortcut.

    A package that can no longer produce its own configuration is exactly the
    state in which a replacement would come up unusable.
    """
    module = checkout / "tmux_workspaces" / "keymap.py"
    original = module.read_text()
    module.write_text(original + "\nthis line is deliberately not Python\n")
    return original


def mark_application(checkout: Path) -> None:
    """Change launch-side application code the way installing a new version would."""
    module = checkout / "tmux_workspaces" / "application.py"
    text = module.read_text()
    marker = '            "window_pid": os.getpid(),\n'
    assert marker in text, "the window manifest no longer names its own process"
    module.write_text(text.replace(marker, marker + '            "reloaded": True,\n'))


def delay_window(checkout: Path, seconds: float) -> None:
    """Make one replacement slow enough to signal while it is still starting."""
    module = checkout / "tmux_workspaces" / "application.py"
    text = module.read_text()
    marker = "def window_main(args) -> int:\n"
    assert marker in text
    module.write_text(text.replace(marker, marker + f'    __import__("time").sleep({seconds})\n'))


def shows(client: Client, command: str) -> bool:
    """The pane wraps a long command anywhere, so compare without its spacing."""
    return "".join(command.split()) in "".join(sidebar_of(client).split())


def ignore_stop(checkout: Path, grace: float) -> None:
    """Model a window that does not act on the signal it is sent.

    Only the window ignores it: the launcher still has to stop, which is the
    behaviour under test.
    """
    module = checkout / "tmux_workspaces" / "cli.py"
    text = module.read_text()
    marker = "        install_stop_signals()\n"
    assert marker in text, "the launcher no longer installs its own stop handlers"
    module.write_text(
        text.replace(
            marker,
            marker
            + "        if args.mode == '_window':\n"
            + "            signal.signal(signal.SIGTERM, signal.SIG_IGN)\n",
        )
    )
    supervisor = checkout / "tmux_workspaces" / "supervisor.py"
    text = supervisor.read_text()
    marker = "STOP_GRACE = 10.0\n"
    assert marker in text, "the launcher no longer bounds its stop"
    supervisor.write_text(text.replace(marker, f"STOP_GRACE = {grace}\n"))


def skew_launcher(checkout: Path) -> None:
    """Model the argument contract an upgraded or downgraded package would use.

    The running launcher keeps the arguments it already imported, so its next
    child is rejected before it can report anything for itself.
    """
    module = checkout / "tmux_workspaces" / "cli.py"
    text = module.read_text()
    option = '    result.add_argument("--keymap-source", type=Path, help=argparse.SUPPRESS)\n'
    assert option in text, "launcher no longer carries the keymap selection"
    module.write_text(text.replace(option, ""))


def attach(client: Client, library: Path, source: Tmux, name: str) -> None:
    viewer = Tmux(runtime(client, library)["viewer_socket"])
    click_button(client, viewer, "Attach session…")
    wait(
        client,
        lambda: name in [line.strip() for line in sidebar(viewer).splitlines()],
        "chooser omitted the external session",
    )
    lines = sidebar(viewer).splitlines()
    row = next(index for index, line in enumerate(lines) if line.strip() == name)
    top = int(viewer.run("display-message", "-p", "-t", "%0", "#{pane_top}"))
    client.click(3, row + top + 1)
    wait(
        client,
        lambda: saved(library).pane["agent"] == name,
        "attaching the external session failed",
    )


def selected(client: Client, library: Path) -> str:
    viewer = Tmux(runtime(client, library)["viewer_socket"])
    line = next(line for line in sidebar(viewer).splitlines() if line.startswith("▶"))
    # "▶ 2 Beta            1": drop the marker, the index and the pane count.
    return line.strip("▶ ").split(" ", 1)[1].rsplit(" ", 1)[0].strip()


def sidebar_of(client: Client) -> str:
    return sidebar(Tmux(client.viewer_socket))


def exercise(resources: FixtureResources) -> None:
    directory = resources.root
    library = resources.library()
    source = resources.server("refresh-source")
    config = directory / "keymap.toml"
    config.write_text(DEFAULT_CONFIG)
    shells = Tmux(str(Path(socket_path(library, "terminals")).resolve()))
    source.run(
        "-f",
        "/dev/null",
        "new-session",
        "-d",
        "-s",
        "external",
        "-e",
        f"HOME={directory}",
        "/bin/sh -i",
    )
    source.run("set-option", "-t", "=external:", "status", "off")
    identities = source.run(
        "list-sessions", "-F", "#{session_id}:#{session_created}:#{session_name}"
    )
    external_pid = source.run("display-message", "-p", "-t", "=external:", "#{pane_pid}")

    theme = directory / "custom colors.toml"
    theme.write_text('[normal]\nforeground = "white"\nbackground = "blue"\n')
    client = start(resources, library, source, config, theme=theme)
    wait(client, lambda: title_pair(client) == (7, 4), "viewer did not load its explicit theme")
    first_instance = client.manifest(library).parent
    first_viewer = Tmux(client.viewer_socket)

    # A shell with history and a running foreground program must survive intact.
    token = os.urandom(8).hex()
    first_terminal = terminal(library)
    shell_pid = shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    client.type(f"printf 'REFRESH_MARKER_%s\\n' {token}\r")
    wait(
        client,
        lambda: (
            "REFRESH_MARKER_" + token
            in shells.run("capture-pane", "-S", "-", "-p", "-t", first_terminal)
        ),
        "ordinary shell did not run its command",
    )
    client.type("\x07rAlpha\r")
    wait(client, lambda: saved(library).tab["name"] == "Alpha", "tab rename failed")
    client.type("\x07t")
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "second tab was not created")
    client.type("\x07rBeta\r")
    wait(client, lambda: saved(library).tab["name"] == "Beta", "second tab rename failed")
    attach(client, library, source, "external")
    client.type(direct_sequence("select-tab-1"))
    wait(client, lambda: selected(client, library) == "Alpha", "tab selection failed")
    layout = saved(library).state["workspaces"]

    # A second window keeps its own navigation, keymap snapshot and display.
    peer = start(resources, library, source, config)
    peer_instance = peer.manifest(library).parent
    peer.type(direct_sequence("select-tab-2"))
    wait(peer, lambda: selected(peer, library) == "Beta", "peer did not select its own tab")
    peer_panes = Tmux(peer.viewer_socket).run("list-panes", "-F", "#{pane_id}:#{pane_pid}")

    # Cancelling leaves this viewer exactly where it was.
    confirmation(client)
    assert "Enter refresh" in sidebar_of(client), sidebar_of(client)
    client.type("\x1b")
    wait(client, lambda: "Refresh viewer" not in sidebar_of(client), "Escape did not cancel")
    assert client.manifest(library).parent == first_instance, "cancelling replaced the viewer"
    client.type("\x07t")
    wait(client, lambda: len(saved(library).space["tabs"]) == 3, "cancelled viewer stopped working")
    client.type("\x07&")
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "extra tab was not closed")
    client.type(direct_sequence("select-tab-1"))
    wait(client, lambda: selected(client, library) == "Alpha", "tab selection failed")

    # A broken keymap is reported before anything is torn down.
    config.write_text(BROKEN_CONFIG)
    confirmation(client)
    client.type("\r")
    wait(client, lambda: "keymap" in sidebar_of(client), "invalid keymap was not reported")
    assert "Refresh viewer now" not in sidebar_of(client), "a broken keymap still offered to close"
    client.pump(0.4)
    assert client.manifest(library).parent == first_instance, "invalid keymap replaced the viewer"
    assert client.process.poll() is None, "invalid keymap closed the launcher"

    # A valid edited keymap applies to the replacement, and only to it.
    config.write_text(EDITED_CONFIG)
    confirmation(client)
    # The next interpreter must reload the same selected theme file too.
    theme.write_text('[normal]\nforeground = "white"\nbackground = "red"\n')
    # Two confirmations in one write must still produce a single replacement.
    client.type("\r\r")
    wait(
        client,
        lambda: (
            client.manifest(library) is not None
            and client.manifest(library).parent != first_instance
        ),
        "viewer was not replaced",
    )
    client.viewer_socket = runtime(client, library)["viewer_socket"]
    wait(
        client,
        lambda: ready(client, library),
        "replacement viewer did not become ready",
        timeout=25,
    )
    wait(
        client,
        lambda: title_pair(client) == (7, 1),
        "replacement did not reload its explicit theme",
    )
    second_instance = client.manifest(library).parent
    assert second_instance != first_instance
    assert client.viewer_socket != first_viewer.socket, "replacement reused the display socket"
    assert not first_viewer.run("list-sessions", check=False), "old private display server remains"
    assert len(list((library / "windows").glob("*/runtime.json"))) == 2, "duplicate replacement"
    assert Tmux(client.viewer_socket).run("show-option", "-gv", "prefix") == "C-a"
    assert Tmux(peer.viewer_socket).run("show-option", "-gv", "prefix") == "C-g"

    # The work beneath the viewer is untouched.
    assert shell_pid == shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    assert "REFRESH_MARKER_" + token in shells.run(
        "capture-pane", "-S", "-", "-p", "-t", first_terminal
    )
    assert identities == source.run(
        "list-sessions", "-F", "#{session_id}:#{session_created}:#{session_name}"
    )
    assert external_pid == source.run("display-message", "-p", "-t", "=external:", "#{pane_pid}")
    assert saved(library).state["workspaces"] == layout, "refresh changed the saved arrangement"
    assert selected(client, library) == "Alpha", "replacement adopted another window's selection"
    assert selected(peer, library) == "Beta", "refresh moved the other window"
    assert peer.manifest(library).parent == peer_instance, "refresh replaced the other window"
    assert peer_panes == Tmux(peer.viewer_socket).run("list-panes", "-F", "#{pane_id}:#{pane_pid}")
    peer.type("\x07t")
    wait(peer, lambda: len(saved(library).space["tabs"]) == 3, "other window stopped working")

    client.type("\x01d")
    wait(client, lambda: client.process.poll() is not None, "refreshed viewer did not exit")
    assert client.process.returncode == 0
    peer.type("\x07d")
    wait(peer, lambda: peer.process.poll() is not None, "second viewer did not exit")
    assert shell_pid == shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    assert external_pid == source.run("display-message", "-p", "-t", "=external:", "#{pane_pid}")
    print(
        "PASS: refresh confirmation and cancellation, invalid keymap reported without teardown, "
        "single replacement with a reloaded keymap and theme, preserved shells, attachments, "
        "navigation and a concurrent viewer",
        flush=True,
    )


def exercise_recovery(resources: FixtureResources) -> None:
    """Faults injected into a disposable copy, never into this checkout."""
    directory = resources.root
    library = resources.library("recovery")
    source = resources.server("recovery-source")
    checkout = copied_checkout(directory)
    config = directory / "recovery-keymap.toml"
    config.write_text(DEFAULT_CONFIG)
    shells = Tmux(str(Path(socket_path(library, "terminals")).resolve()))
    client = start(resources, library, source, config, checkout)
    instance = client.manifest(library).parent
    first_terminal = terminal(library)
    shell_pid = shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")

    # A launcher that can no longer start a working viewer is found first.
    original = break_settings(checkout)
    confirmation(client)
    client.type("\r")
    wait(client, lambda: "Cannot reopen" in sidebar_of(client), "broken launcher was not reported")
    assert "Refresh viewer now" not in sidebar_of(client), (
        "a broken launcher still offered to close"
    )
    client.pump(0.4)
    assert client.manifest(library).parent == instance, "broken launcher replaced the viewer"
    assert client.process.poll() is None, "a refused refresh closed the launcher"
    client.type("\x1b")
    wait(client, lambda: "Esc close" not in sidebar_of(client), "Escape did not close the refusal")
    (checkout / "tmux_workspaces" / "keymap.py").write_text(original)
    client.type("\x07t")
    wait(client, lambda: len(saved(library).space["tabs"]) == 2, "refusal left an unusable viewer")

    # A replacement that dies before it can report still explains itself, and
    # its text has to survive in a surface that closes with the launcher.
    skew_launcher(checkout)
    confirmation(client)
    client.type("\r")
    wait(
        client,
        lambda: "Reopen with:" in client.output.decode(errors="replace"),
        "failed replacement printed no recovery instructions",
    )
    output = client.output.decode(errors="replace")
    # A rejected argument ends the window before it can report anything at all.
    assert "exited with status 2" in output, output[-2000:]
    assert str(checkout / "run") in output, output[-2000:]
    assert str(library) in output, output[-2000:]
    assert client.process.poll() is None, "recovery text vanished with the window"
    # The surface stays usable: an ordinary shell, not a prompt that closes.
    token = os.urandom(8).hex()
    client.type(f"echo RECOVERED_{token}\r")
    wait(
        client,
        lambda: f"RECOVERED_{token}" in client.output.decode(errors="replace").split("echo")[-1],
        "the recovery shell did not run a typed command",
    )
    client.type("exit 7\r")
    wait(client, lambda: client.process.poll() is not None, "the recovery shell did not exit")
    # The terminal belongs to that shell now, so its own status is what returns.
    assert client.process.returncode == 7, client.process.returncode
    assert shell_pid == shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    wait(None, lambda: not displays(library), "a failed replacement left a display server")
    wait(None, lambda: not list((library / "windows").glob("*/runtime.json")), "instance remained")

    # Interrupting the launcher while a refresh is pending starts nothing.
    other = start(resources, library, source, config)
    confirmation(other)
    os.killpg(other.process.pid, signal.SIGTERM)
    wait(other, lambda: other.process.poll() is not None, "interrupted launcher did not exit")
    assert other.process.returncode in (143, -signal.SIGTERM), other.process.returncode
    wait(None, lambda: not displays(library), "an interrupted viewer left a display server")
    wait(None, lambda: not list((library / "windows").glob("*/runtime.json")), "instance remained")
    assert shell_pid == shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    print(
        "PASS: broken launcher refused before teardown, a failed replacement left a working "
        "recovery shell with its exact reopen command, interruption left no display server, "
        "and the ordinary shell kept running throughout",
        flush=True,
    )


def exercise_supervision(resources: FixtureResources) -> None:
    """One launcher, one window at a time, and a window that reloads the code."""
    directory = resources.root
    library = resources.library("supervision")
    source = resources.server("supervision-source")
    checkout = copied_checkout(directory)
    config = directory / "supervision-keymap.toml"
    config.write_text(DEFAULT_CONFIG)
    shells = Tmux(str(Path(socket_path(library, "terminals")).resolve()))
    client = start(resources, library, source, config, checkout)
    first = runtime(client, library)
    assert first["pid"] == client.process.pid, "the launcher does not own its library manifest"
    assert first["window_pid"] != client.process.pid, "the window is not a separate interpreter"
    assert "reloaded" not in first
    first_terminal = terminal(library)
    shell_pid = shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    client.type("printf 'SUPERVISION_READY\\n'\r")
    wait(
        client,
        lambda: (
            "SUPERVISION_READY" in shells.run("capture-pane", "-S", "-", "-p", "-t", first_terminal)
        ),
        "ordinary shell did not run its command",
    )

    # Application code replaced on disk takes effect in the next window.
    mark_application(checkout)
    instance = client.manifest(library).parent
    confirmation(client)
    client.type("\r")
    wait(
        client,
        lambda: (
            client.manifest(library) is not None and client.manifest(library).parent != instance
        ),
        "the window was not replaced",
    )
    client.viewer_socket = runtime(client, library)["viewer_socket"]
    wait(client, lambda: ready(client, library), "replacement did not become ready", timeout=25)
    second = runtime(client, library)
    assert second.get("reloaded") is True, "the replacement kept the old application code"
    assert second["pid"] == client.process.pid, "the launcher was replaced too"
    assert second["window_pid"] != first["window_pid"], "the window process was reused"
    assert len(list((library / "windows").glob("*/runtime.json"))) == 1, "two windows at once"
    assert shell_pid == shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    assert "SUPERVISION_READY" in shells.run("capture-pane", "-S", "-", "-p", "-t", first_terminal)

    # A second refresh keeps the same launcher and one window child.
    confirmation(client)
    client.type("\r")
    wait(
        client,
        lambda: (
            client.manifest(library) is not None
            and client.manifest(library).parent not in (instance, second["viewer_socket"])
        ),
        "a second refresh did not open a window",
    )
    client.viewer_socket = runtime(client, library)["viewer_socket"]
    wait(client, lambda: ready(client, library), "second replacement not ready", timeout=25)
    third = runtime(client, library)
    assert third["pid"] == client.process.pid
    assert third["window_pid"] not in (first["window_pid"], second["window_pid"])
    assert len(list((library / "windows").glob("*/runtime.json"))) == 1
    client.type("\x07d")
    wait(client, lambda: client.process.poll() is not None, "supervised viewer did not exit")
    assert client.process.returncode == 0

    # A window whose keys were fixed by a launcher is reopened by hand, with a
    # command this fixture can run for itself.
    pinned = resources.client(
        [
            "--data-dir",
            str(library),
            "--source-socket",
            source.socket,
            "--keymap-state",
            DEFAULT_CONFIG,
        ],
        launcher=[sys.executable, str(checkout / "run")],
    )
    wait(pinned, lambda: ready(pinned, library), "fixed-key viewer did not start", timeout=25)
    pinned.viewer_socket = runtime(pinned, library)["viewer_socket"]
    pinned_instance = pinned.manifest(library).parent
    confirmation(pinned)
    wait(pinned, lambda: "Esc close" in sidebar_of(pinned), "manual reopen was not offered")
    assert "Refresh viewer now" not in sidebar_of(pinned), "a fixed-key window offered to close"
    command = runtime(pinned, library)["reopen_command"]
    assert command.startswith(sys.executable), command
    assert str(checkout / "run") in command, command
    assert "--keymap-state" not in command, command
    assert "_sidebar" not in command and "--action-socket" not in command, command
    assert shows(pinned, command), sidebar_of(pinned)
    pinned.type("\x1b")
    pinned.pump(0.4)
    assert pinned.manifest(library).parent == pinned_instance, "the fixed-key window was replaced"

    reopened = resources.own_client(
        Client([], launcher=shlex.split(command), terminal_env={"HOME": str(directory)})
    )
    wait(reopened, lambda: ready(reopened, library), "the offered command did not open", timeout=25)
    assert reopened.manifest(library).parent != pinned_instance
    assert shell_pid == shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    reopened.type("\x07d")
    wait(reopened, lambda: reopened.process.poll() is not None, "reopened viewer did not exit")
    pinned.type("\x07d")
    wait(pinned, lambda: pinned.process.poll() is not None, "fixed-key viewer did not exit")

    # Signalling only the launcher, while a replacement is starting, stops both.
    delay_window(checkout, 6)
    slow = start(resources, library, source, config, checkout)
    slow_instance = slow.manifest(library).parent
    confirmation(slow)
    slow.type("\r")
    wait(
        slow,
        lambda: slow.manifest(library) is None or slow.manifest(library).parent != slow_instance,
        "the window did not close for its replacement",
    )
    os.kill(slow.process.pid, signal.SIGTERM)
    wait(slow, lambda: slow.process.poll() is not None, "the signalled launcher did not exit")
    assert slow.process.returncode in (143, -signal.SIGTERM), slow.process.returncode
    wait(None, lambda: not displays(library), "an interrupted replacement left a display server")
    wait(None, lambda: not list((library / "windows").glob("*/runtime.json")), "instance remained")
    assert shell_pid == shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    # A window that ignores its stop is ended plainly, and said so.
    ignore_stop(checkout, 2.0)
    stubborn = start(resources, library, source, config, checkout)
    os.kill(stubborn.process.pid, signal.SIGTERM)
    wait(stubborn, lambda: stubborn.process.poll() is not None, "an ignored stop hung the launcher")
    output = stubborn.output.decode(errors="replace")
    assert "did not stop within" in output, output[-2000:]
    assert "cleanup may be incomplete" in output, output[-2000:]
    assert stubborn.process.returncode in (143, -signal.SIGTERM), stubborn.process.returncode
    # A window ended that way cannot close its own display, so its attach client
    # outlives it and the private server goes when the terminal does, which is
    # what the message above says.
    stubborn.close_terminal()
    wait(None, lambda: not displays(library), "a forced window left a private display server")
    assert shell_pid == shells.run("display-message", "-p", "-t", first_terminal, "#{pane_pid}")
    # What it left behind does not stand in the way of opening the library again.
    after = start(resources, library, source, config, checkout)
    assert after.manifest(library) is not None
    after.type("\x07d")
    wait(after, lambda: after.process.poll() is not None, "the library did not open again")
    assert after.process.returncode == 0
    print(
        "PASS: one launcher supervising one window at a time, replaced application code taking "
        "effect with unchanged shells, a fixed-key window reopened by its own offered command, "
        "a launcher signalled mid-replacement leaving nothing behind, and a window that ignores "
        "its stop ended within a bounded grace",
        flush=True,
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-refresh-smoke-", dir="/tmp") as directory:
        run(Path(directory))
        recovery(Path(directory))
        supervision(Path(directory))
