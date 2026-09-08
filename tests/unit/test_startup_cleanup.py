"""Partial startup closes only its own resources, including setup-time failures."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tmux_workspaces import application
from tmux_workspaces.cli import parser


class StartupCleanupTests(unittest.TestCase):
    def test_cleanup_attempts_all_callbacks_and_preserves_original_failure(self):
        callbacks = [Mock(), Mock(side_effect=OSError("cleanup failed")), Mock()]
        original = RuntimeError("original startup failure")
        with self.assertRaises(RuntimeError) as raised, application.startup_cleanup() as cleanup:
            for callback in callbacks:
                cleanup.callback(callback)
            raise original
        self.assertIs(raised.exception, original)
        self.assertIn("cleanup failed", original.__notes__[0])
        for callback in callbacks:
            callback.assert_called_once()

    def test_sidebar_setup_failures_close_prior_resources_and_report_to_launcher(self):
        for stage in ("refresh", "store", "actions", "curses"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                args = parser().parse_args(["--no-keymap", "--data-dir", directory])
                args.instance_dir = Path(directory)
                args.viewer_socket = "/fixture/private-view.sock"
                args.source_socket = "/fixture/external.sock"
                args.shell_socket = "/fixture/shared-shells.sock"
                source, store, tmux = Mock(), Mock(), Mock()
                tmux.run.return_value = "1"
                curses = Mock()
                curses.error = type("CursesError", (Exception,), {})
                failure = RuntimeError("fixture failure at " + stage)
                if stage == "refresh":
                    source.refresh.side_effect = failure
                if stage == "curses":
                    curses.wrapper.side_effect = curses.error("late initialization")
                with (
                    patch.object(application, "Tmux", return_value=tmux) as factory,
                    patch.object(application, "load_curses", return_value=curses),
                    patch.object(application, "make_source", return_value=source),
                    patch.object(
                        application,
                        "Store",
                        side_effect=failure if stage == "store" else None,
                        return_value=store,
                    ) as stores,
                    patch.object(
                        application, "Actions", side_effect=failure if stage == "actions" else None
                    ) as actions,
                    patch.object(application, "Display"),
                    patch.dict(os.environ, {"TMUX_PANE": "%0"}),
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        application.sidebar_main(args)
                    if stage == "curses":
                        self.assertIn("Terminal initialization failed", str(raised.exception))
                    else:
                        self.assertIs(raised.exception, failure)
                    source.close.assert_called_once()
                    if stage in ("actions", "curses"):
                        store.close.assert_called_once()
                    else:
                        store.close.assert_not_called()
                    if stage == "refresh":
                        stores.assert_not_called()
                    if stage == "curses":
                        actions.return_value.close.assert_called_once()
                    factory.assert_called_once_with(args.viewer_socket)
                    tmux.run.assert_any_call("kill-server", check=False)
                    error = (Path(directory) / "error.txt").read_text()
                    self.assertIn(str(raised.exception), error)
                    self.assertNotIn("Traceback", error)

    def test_partial_demo_launch_cleans_both_private_servers_even_when_one_cleanup_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = parser().parse_args(
                ["--no-keymap", "--demo", "--data-dir", str(root / "library")]
            )
            own_view, own_demo = Mock(), Mock()
            own_view.run.side_effect = OSError("viewer teardown failed")

            def sockets(_library, name):
                return str(root / (name + ".sock"))

            failure = RuntimeError("partial demo startup failed")
            with (
                patch.object(application, "check_startup"),
                patch.object(application, "Store"),
                patch.object(application, "socket_path", side_effect=sockets),
                patch.object(application, "Tmux", side_effect=[own_view, own_demo]) as servers,
                patch.object(application, "start_demo", side_effect=failure),
                patch.object(application.subprocess, "run") as command,
            ):
                with self.assertRaises(RuntimeError) as raised:
                    application.launch(args)
                self.assertIs(raised.exception, failure)
                self.assertIn("viewer teardown failed", failure.__notes__[0])
                own_view.run.assert_called_once_with("kill-server", check=False)
                own_demo.run.assert_called_once_with("kill-server", check=False)
                self.assertEqual(servers.call_count, 2)
                self.assertNotIn(
                    "terminals", " ".join(call.args[0] for call in servers.call_args_list)
                )
                command.assert_not_called()
                self.assertFalse(args.instance_dir.exists())


if __name__ == "__main__":
    unittest.main()
