"""Container reuse preserves real tmux geometry, targets and conservative recovery."""

import copy
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager, suppress
from pathlib import Path
from unittest.mock import patch

from tmux_workspaces.display import Display
from tmux_workspaces.model import Model, leaves
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux


def four_panes():
    model = Model.initial()
    for direction in ("right", "below", "right"):
        model.split(direction)
    return model.tab


def failure_diagnostics(viewer, source):
    """Report only these disposable servers; never replace the test failure."""

    def report(tmux, *command):
        try:
            output = tmux.run(*command, timeout=1)
        except Exception as error:
            output = f"{type(error).__name__}: {error}"
        print(f"geometry diagnostic {tmux.socket} {command!r}:\n{output}", file=sys.stderr)
        return output

    report(source, "display-message", "-p", "server_pid=#{pid}")
    report(
        source,
        "list-sessions",
        "-F",
        "#{session_id} #{session_name} attached=#{session_attached} "
        "destroy-unattached=#{destroy-unattached}",
    )
    report(source, "show-options", "-g")
    report(source, "list-panes", "-a", "-F", "#{pane_id} pid=#{pane_pid} dead=#{pane_dead}")
    panes = report(
        viewer,
        "list-panes",
        "-a",
        "-F",
        "#{pane_id} pid=#{pane_pid} dead=#{pane_dead} exit=#{pane_dead_status} "
        "command=#{pane_start_command}",
    )
    for line in panes.splitlines():
        pane = line.split(maxsplit=1)[0] if line else ""
        if pane.startswith("%") and pane[1:].isdigit():
            report(viewer, "capture-pane", "-p", "-t", pane, "-S", "-50")


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix="tw-geometry-", dir="/tmp") as directory:
        root = Path(directory)
        viewer = Tmux(str(root / "viewer.sock"))
        source = Tmux(str(root / "source.sock"))
        shells = Tmux(str(root / "shells.sock"))
        with patch.dict(os.environ, {"SHELL": "/bin/sh", "HOME": str(root)}):
            try:
                viewer.run(
                    "-f",
                    "/dev/null",
                    "new-session",
                    "-d",
                    "-s",
                    "viewer",
                    "-x",
                    "180",
                    "-y",
                    "50",
                    "/bin/sh",
                )
                viewer.run("set-window-option", "-g", "remain-on-exit", "on")
                sidebar = viewer.run("display-message", "-p", "#{pane_id}")
                display = Display(
                    viewer.socket, source.socket, sidebar, shells.socket, str(root / "actions.sock")
                )
                yield display, viewer, source, shells
            except BaseException:
                # Reporting must not obscure the original assertion or error.
                with suppress(Exception):
                    failure_diagnostics(viewer, source)
                raise
            finally:
                for tmux in (viewer, source, shells):
                    tmux.run("kill-server", check=False)
                Path(shells.socket + ".viewer-lock").unlink(missing_ok=True)


def containers(viewer):
    return viewer.run(
        "list-panes",
        "-F",
        "#{pane_id}|#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}",
    )


def pane_pids(viewer):
    return dict(
        line.split()
        for line in viewer.run("list-panes", "-F", "#{pane_id} #{pane_pid}").splitlines()
    )


@unittest.skipUnless(shutil.which("tmux"), "tmux required for pane reuse")
class PaneGeometryTests(unittest.TestCase):
    def assert_targets(self, display, viewer, tab):
        for leaf in leaves(tab["tree"]):
            if leaf["id"] not in display.panes:
                continue
            actual = viewer.run(
                "display-message",
                "-p",
                "-t",
                display.panes[leaf["id"]],
                "#{@viewer_tab_id}|#{@viewer_leaf_id}|#{pane_start_command}",
            )
            tab_id, leaf_id, command = actual.split("|", 2)
            self.assertEqual((tab_id, leaf_id), (tab["id"], leaf["id"]))
            self.assertIn(leaf["agent"] or Shells.name(leaf), command)
            if leaf.get("source_socket"):
                self.assertIn(leaf["source_socket"], command)

    def test_equal_geometry_keeps_containers_and_restarts_wrappers_in_one_batch(self):
        with fixture() as (display, viewer, source, shells):
            first, second = four_panes(), four_panes()
            display.render(first, False)
            geometry, original_pids = containers(viewer), pane_pids(viewer)
            original_shells = shells.run("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}")
            source.run("-f", "/dev/null", "new-session", "-d", "-s", "external", "/bin/sh")
            external = source.run(
                "list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}:#{pane_current_path}"
            )
            attached = leaves(second["tree"])[1]
            attached.update(agent="external", source_socket=source.socket)
            order = list(display.panes.values())
            with patch.object(display.tmux, "batch", wraps=display.tmux.batch) as batch:
                display.render(second, False)
            self.assertEqual(batch.call_count, 1)
            commands = batch.call_args.args[0]
            self.assertEqual(commands[0], ["select-pane", "-t", display.sidebar])
            self.assertEqual(commands[-1], ["select-pane", "-t", display.panes[second["focus"]]])
            self.assertEqual(sum(command[0] == "respawn-pane" for command in commands), 4)
            self.assertFalse(
                any(
                    command[0] in {"kill-pane", "split-window", "resize-pane"}
                    for command in commands
                )
            )
            self.assertEqual(containers(viewer), geometry)
            self.assertEqual(list(display.panes.values()), order)
            replaced_pids = pane_pids(viewer)
            self.assertEqual(original_pids[display.sidebar], replaced_pids[display.sidebar])
            self.assertTrue(all(original_pids[pane] != replaced_pids[pane] for pane in order))
            self.assert_targets(display, viewer, second)
            second["name"] = "Names do not restart wrappers"
            display.render(second, False)
            self.assertEqual(pane_pids(viewer), replaced_pids)
            display.render(first, False)
            self.assertEqual(containers(viewer), geometry)
            self.assert_targets(display, viewer, first)
            retained = shells.run("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}").splitlines()
            self.assertTrue(set(original_shells.splitlines()).issubset(retained))
            self.assertEqual(
                source.run("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}:#{pane_current_path}"),
                external,
            )

    def test_geometry_mismatches_use_full_rebuild(self):
        for change in ("ratio", "topology", "resize", "missing", "dead", "empty", "drag"):
            with self.subTest(change=change), fixture() as (display, viewer, _source, _shells):
                first, second = four_panes(), four_panes()
                display.render(first, False)
                if change == "ratio":
                    second["tree"]["ratio"] = 0.6
                elif change == "topology":
                    second["tree"]["direction"] = "below"
                elif change == "resize":
                    viewer.run("resize-window", "-x", "170", "-y", "48")
                elif change == "missing":
                    viewer.run("kill-pane", "-t", list(display.panes.values())[-1])
                elif change == "dead":
                    dead = list(display.panes.values())[-1]
                    viewer.run("respawn-pane", "-k", "-t", dead, "/usr/bin/true")
                    deadline = time.monotonic() + 5
                    while viewer.run("display-message", "-p", "-t", dead, "#{pane_dead}") != "1":
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.01)
                elif change == "empty":
                    second = None
                elif change == "drag":
                    viewer.run("resize-pane", "-t", next(iter(display.panes.values())), "-x", "85")
                    display.remember_ratios(first["tree"])
                with patch.object(
                    display, "_reuse_containers", wraps=display._reuse_containers
                ) as reuse:
                    display.render(second, False)
                self.assertFalse(reuse.called)
                if second:
                    self.assert_targets(display, viewer, second)
                self.assertEqual(
                    viewer.run("display-message", "-p", "-t", display.sidebar, "#{pane_width}"),
                    "28",
                )

    def test_effective_focus_tree_reuses_only_matching_visible_geometry(self):
        with fixture() as (display, viewer, _source, _shells):
            first, second = four_panes(), four_panes()
            display.render(first, True)
            geometry = containers(viewer)
            display.render(second, True)
            self.assertEqual(containers(viewer), geometry)
            self.assertEqual(set(display.panes), {second["focus"]})
            self.assert_targets(display, viewer, second)
            with patch.object(
                display, "_reuse_containers", wraps=display._reuse_containers
            ) as reuse:
                display.render(second, False)
            self.assertFalse(reuse.called)
            self.assertEqual(len(display.panes), 4)

    def test_partial_reuse_failure_keeps_focus_safe_and_next_render_rebuilds(self):
        with fixture() as (display, viewer, _source, _shells):
            first, second = four_panes(), four_panes()
            display.render(first, False)
            original_batch = display.tmux.batch

            def partial(commands, **kwargs):
                original_batch(commands[:2], **kwargs)
                raise RuntimeError("injected partial respawn failure")

            with (
                patch.object(display.tmux, "batch", side_effect=partial),
                self.assertRaisesRegex(RuntimeError, "injected"),
            ):
                display.render(second, False)
            self.assertTrue(display.state().panes[display.sidebar].active)
            self.assertFalse(display.panes)
            self.assertIsNone(display._rendered_key)
            self.assertIsNone(display._rendered_geometry)
            with patch.object(
                display, "_reuse_containers", wraps=display._reuse_containers
            ) as reuse:
                display.render(second, False)
            self.assertFalse(reuse.called)
            self.assert_targets(display, viewer, second)
            self.assertEqual(display.focused_leaf(), second["focus"])

    def test_invalid_target_is_precomputed_before_any_respawn(self):
        with fixture() as (display, viewer, _source, _shells):
            first, second = four_panes(), four_panes()
            display.render(first, False)
            original = pane_pids(viewer)
            second = copy.deepcopy(second)
            leaves(second["tree"])[-1].update(agent="invalid source", source_socket=viewer.socket)
            with (
                patch.object(display.tmux, "batch", wraps=display.tmux.batch) as batch,
                self.assertRaises(ValueError),
            ):
                display.render(second, False)
            self.assertFalse(batch.called)
            self.assertEqual(pane_pids(viewer), original)
            self.assertTrue(display.state().panes[display.sidebar].active)
