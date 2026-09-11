"""Install a clean source archive twice and retain live work across both prefixes."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack, chdir
from pathlib import Path
from unittest.mock import patch

from tests.integration.support import (
    OUTLINE,
    Client,
    click_attach,
    click_button,
    content_panes,
    saved,
    wait,
)
from tmux_workspaces.application import socket_path
from tmux_workspaces.model import leaves
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux, clean_env


def cleanup_views(library: Path) -> None:
    # This fresh library belongs exclusively to this fixture. Never let a runtime
    # manifest redirect teardown at an unrelated tmux server.
    pattern = Path(socket_path(library, "view-"))
    with ExitStack() as cleanup:
        for path in pattern.parent.glob(pattern.name.removesuffix(".sock") + "*.sock"):
            if not path.is_symlink():
                cleanup.callback(Tmux(str(path)).run, "kill-server", check=False)


def readonly_payload(runtime: Path, cleanup: ExitStack) -> set[Path]:
    paths = [runtime, *runtime.rglob("*")]
    modes = [(path, path.stat().st_mode & 0o777) for path in paths if not path.is_symlink()]

    def restore():
        # Restore parent directories first so TemporaryDirectory can remove the
        # copied payload even when a scenario assertion fails.
        for path, mode in modes:
            path.chmod(mode)

    cleanup.callback(restore)
    for path, mode in modes:
        path.chmod(mode & ~0o222)
    return {path.relative_to(runtime) for path in paths}


def close_client(client: Client) -> None:
    try:
        client.close()
    except subprocess.TimeoutExpired:
        client.process.kill()
        client.process.wait(timeout=5)
    finally:
        if client.master is not None:
            os.close(client.master)
            client.master = None


def exercise(source: Path, root: Path) -> None:
    python = str(Path(sys.executable).resolve())
    tmux = shutil.which("tmux")
    if tmux is None:
        raise RuntimeError("tmux is required for the installed-bundle PTY suite")
    tmux = str(Path(tmux).resolve())
    prefixes = [root / "prefix one", root / "prefix two 'quoted'"]
    library = root / "library"
    library.mkdir()
    home = root / "home"
    home.mkdir()
    cwd = root / "work directory"
    cwd.mkdir()
    source_server = Tmux(str(Path(socket_path(root, "pack-source")).resolve()))
    outer = Tmux(str(Path(socket_path(root, "pack-outer")).resolve()))
    shells = Tmux(socket_path(library, "terminals"))
    terminal_env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "SHELL": "/bin/sh",
        "PATH": "/usr/bin:/bin",
    }
    with (
        ExitStack() as cleanup,
        chdir(root),
        patch.dict(os.environ, {"HOME": str(home), "SHELL": "/bin/sh"}),
    ):
        for server in (source_server, outer, shells):
            cleanup.callback(server.run, "kill-server", check=False)
        cleanup.callback(Path(shells.socket + ".viewer-lock").unlink, missing_ok=True)
        cleanup.callback(cleanup_views, library)
        env = clean_env() | terminal_env
        assert "PYTHONPATH" not in env
        installed_trees = {}
        for prefix in prefixes:
            subprocess.run(
                [
                    python,
                    str(source / "scripts/install_bundle.py"),
                    "--prefix",
                    str(prefix),
                    "--python",
                    python,
                    "--tmux",
                    tmux,
                ],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            runtime = prefix / "libexec/tmux-workspaces"
            assert (runtime / "run").read_text().splitlines()[0] == "#!" + python
            assert not (runtime / "preview").exists()
            dry = subprocess.run(
                [
                    str(prefix / "bin/tmux-workspaces-ghostty"),
                    "--dry-run",
                    "--data-dir",
                    str(library),
                ],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            shell_command = next(
                argument.removeprefix("--command=shell:")
                for argument in shlex.split(dry.stdout)
                if argument.startswith("--command=shell:")
            )
            command_argv = shlex.split(shell_command)
            assert Path(command_argv[0]).resolve() == Path(python)
            assert command_argv[1] == str(runtime / "run")
            installed_trees[runtime] = readonly_payload(runtime, cleanup)

        def own_client(arguments, launcher):
            client = Client(arguments, launcher=launcher, terminal_env=terminal_env)
            cleanup.callback(close_client, client)
            return client

        def sidebar(viewer):
            return viewer.run("capture-pane", "-p", "-t", "%0").translate(OUTLINE)

        def initialize(client, prefix, manifest=None):
            manifest = manifest or (lambda: client.manifest(library))
            wait(client, manifest, "installed viewer did not create its runtime manifest")
            viewer = Tmux(json.loads(manifest().read_text())["viewer_socket"])
            wait(client, lambda: "Detach" in sidebar(viewer), "installed sidebar missing")

            def installed_command():
                for line in viewer.run("list-panes", "-F", "#{pane_start_command}").splitlines():
                    arguments = shlex.split(line)
                    # tmux serializes a shell command as one quoted argv item.
                    if len(arguments) == 1:
                        arguments = shlex.split(arguments[0])
                    if str(prefix / "libexec/tmux-workspaces/run") in arguments:
                        return True
                return False

            wait(client, installed_command, "display wrappers did not use the installed bundle")
            return viewer

        def launch(prefix):
            client = own_client(
                ["--data-dir", str(library), "--source-socket", source_server.socket],
                [str(prefix / "bin/tmux-workspaces")],
            )
            return client, initialize(client, prefix)

        def identity():
            return shells.run(
                "list-panes", "-a", "-F", "#{session_name}|#{pane_pid}|#{pane_current_path}"
            )

        def select_leaf(client, viewer, leaf):
            def pane():
                return next(
                    (
                        line.split("|")
                        for line in viewer.run(
                            "list-panes", "-F", "#{@viewer_leaf_id}|#{pane_left}|#{pane_top}"
                        ).splitlines()
                        if line.split("|")[0] == leaf
                    ),
                    None,
                )

            wait(client, pane, "saved split pane missing")
            row = pane()
            client.click(int(row[1]) + 2, int(row[2]) + 1)

        def exit_viewer(client, viewer, *, nested=False):
            click_button(client, viewer, "Exit")
            wait(
                client,
                lambda: not list((library / "windows").glob("*/runtime.json")),
                "installed viewer did not detach and remove its manifest",
            )
            if not nested:
                client.process.wait(timeout=10)
                assert client.process.returncode == 0
                close_client(client)

        source_server.run(
            "-f",
            "/dev/null",
            "new-session",
            "-d",
            "-s",
            "external",
            "-c",
            str(cwd),
            "-e",
            f"HOME={home}",
            "/bin/sh -i",
        )
        external_identity = source_server.run(
            "list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}|#{pane_current_path}"
        )
        client, viewer = launch(prefixes[0])
        original_leaf = saved(library).pane["id"]
        client.type("\x07rInstalled work\r")
        wait(
            client,
            lambda: saved(library).tab["name"] == "Installed work",
            "installed rename failed",
        )
        for label in ("Split →", "Split ↓", "Split →"):
            count = len(leaves(saved(library).tab["tree"]))
            click_button(client, viewer, label)
            wait(
                client,
                lambda count=count: len(content_panes(viewer)) == count + 2,
                "installed split button failed",
            )
        attachment_leaf = saved(library).pane["id"]
        click_attach(client, viewer, attachment_leaf)
        click_button(client, viewer, "external")
        wait(client, lambda: saved(library).pane["agent"] == "external", "installed attach failed")
        assert saved(library).tab["name"] == "Installed work"
        click_button(client, viewer, "+ Tab")
        wait(client, lambda: len(saved(library).space["tabs"]) == 2, "installed new tab failed")
        other_tab = saved(library).tab["name"]
        click_button(client, viewer, "Installed work")
        wait(
            client,
            lambda: len(content_panes(viewer)) == 5,
            "saved four-pane tab missing",
        )
        select_leaf(client, viewer, original_leaf)

        program = root / "foreground.py"
        state = root / "foreground.json"
        received = root / "received.txt"
        program.write_text(
            "import json, os, pathlib, sys\n"
            f"pathlib.Path({str(state)!r}).write_text(json.dumps(dict(\n"
            " pid=os.getpid(), cwd=os.getcwd(), pythonpath=os.environ.get('PYTHONPATH'))))\n"
            "for line in sys.stdin:\n"
            f" with open({str(received)!r}, 'a') as output: output.write(line)\n"
        )
        client.type(
            "cd " + shlex.quote(str(cwd)) + "; " + shlex.join([python, str(program)]) + "\r"
        )
        wait(client, state.exists, "installed shell did not start foreground program")
        foreground = json.loads(state.read_text())
        assert foreground["cwd"] == str(cwd) and foreground["pythonpath"] is None
        # Navigation records the live cwd and rendered ratios before snapshotting
        # persistence. Starting a foreground command must survive this switch too.
        click_button(client, viewer, other_tab)
        wait(client, lambda: len(content_panes(viewer)) == 2, "new tab not selected")
        click_button(client, viewer, "Installed work")
        wait(
            client,
            lambda: len(content_panes(viewer)) == 5,
            "return tab lost splits",
        )
        expected_identity = identity()
        expected_tree = json.dumps(saved(library).tab["tree"], sort_keys=True)
        original_terminal = "=" + Shells.name({"id": original_leaf}) + ":"
        first_pid = shells.run("display-message", "-p", "-t", original_terminal, "#{pane_pid}")
        rounds = []

        def check_retained(client, viewer, label):
            wait(
                client,
                lambda: len(content_panes(viewer)) == 5,
                "reopen lost splits",
            )
            assert saved(library).tab["name"] == "Installed work"
            assert json.dumps(saved(library).tab["tree"], sort_keys=True) == expected_tree
            assert identity() == expected_identity
            assert (
                shells.run("display-message", "-p", "-t", original_terminal, "#{pane_pid}")
                == first_pid
            )
            assert external_identity == source_server.run(
                "list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}|#{pane_current_path}"
            )
            assert (
                next(p for p in leaves(saved(library).tab["tree"]) if p["id"] == attachment_leaf)[
                    "agent"
                ]
                == "external"
            )
            os.kill(foreground["pid"], 0)
            select_leaf(client, viewer, attachment_leaf)
            client.type("printf 'BUNDLE_%s\\n' " + shlex.quote(label) + "\r")
            wait(
                client,
                lambda: (
                    "BUNDLE_" + label in source_server.run("capture-pane", "-p", "-t", "=external:")
                ),
                "installed bundle did not reconnect the saved external attachment",
            )
            select_leaf(client, viewer, original_leaf)
            client.type(label + "\r")
            rounds.append(label)
            wait(
                client,
                lambda: received.exists() and received.read_text().splitlines() == rounds,
                "foreground input did not survive installed-prefix navigation",
            )

        check_retained(client, viewer, "first-prefix")
        exit_viewer(client, viewer)
        for prefix, label in ((prefixes[1], "second-prefix"), (prefixes[0], "return-first")):
            client, viewer = launch(prefix)
            check_retained(client, viewer, label)
            exit_viewer(client, viewer)

        # Load the installed TPM entry, retaining the host's processes and options.
        outer.run(
            "-f",
            "/dev/null",
            "new-session",
            "-d",
            "-s",
            "host",
            "-c",
            str(root),
            "-e",
            f"HOME={home}",
            "/bin/sh",
        )
        outer.run("set-environment", "-g", "PATH", terminal_env["PATH"])
        outer.run("set-environment", "-g", "SHELL", "/bin/sh")
        outer.run("set-option", "-g", "@tmux-workspaces-data-dir", str(library))
        host_identity = outer.run("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}")
        host_options = outer.run("show-options", "-g")
        plugin = prefixes[1] / "share/tmux-workspaces/tmux-workspaces.tmux"
        outer.run("run-shell", shlex.quote(str(plugin)).replace("#", "##"))
        client = own_client([], [tmux, "-S", outer.socket, "attach-session", "-t", "=host:"])
        wait(client, lambda: outer.run("list-clients"), "installed plugin outer client missing")
        client.type("\x02")
        wait(
            client,
            lambda: outer.run("list-clients", "-F", "#{client_prefix}") == "1",
            "outer prefix lost",
        )
        client.type("W")
        viewer = initialize(
            client, prefixes[1], lambda: next((library / "windows").glob("*/runtime.json"), None)
        )
        check_retained(client, viewer, "installed-tpm")
        exit_viewer(client, viewer, nested=True)
        wait(
            client,
            lambda: outer.run("list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}") == host_identity,
            "installed TPM exit changed the original host panes",
        )
        assert outer.run("show-options", "-g") == host_options
        assert client.process.poll() is None
        assert identity() == expected_identity
        assert external_identity == source_server.run(
            "list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}|#{pane_current_path}"
        )
        assert json.loads(state.read_text()) == foreground
        for runtime, membership in installed_trees.items():
            assert {
                Path("."),
                *(path.relative_to(runtime) for path in runtime.rglob("*")),
            } == membership, ("installed runtime wrote files into its read-only payload", runtime)
        print(
            "PASS: source bundle installed in two read-only prefixes, "
            "pinned launchers outside checkout, "
            "mouse/splits/navigation, attachment and foreground PID/cwd/input "
            "across first→second→first "
            "prefixes and installed TPM launch; host options/processes retained. "
            "Same-version relocation only; no arbitrary-version upgrade claim.",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="extracted clean source archive")
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / "scripts/install_bundle.py").is_file():
        parser.error("--source must contain scripts/install_bundle.py")
    with tempfile.TemporaryDirectory(prefix="tw-package-", dir="/tmp") as directory:
        exercise(source, Path(directory).resolve())


if __name__ == "__main__":
    main()
