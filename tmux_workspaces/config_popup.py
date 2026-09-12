"""Lifecycle of one built-in editor on the viewer's private tmux server."""

import contextlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

from .entrypoints import script_command
from .tmux import clean_env


class ConfigPopup:
    def __init__(self, display, kind, path):
        self.display, self.kind = display, kind
        self.resources = contextlib.ExitStack()
        directory = self.resources.enter_context(tempfile.TemporaryDirectory(prefix="tw-json-"))
        self.result = Path(directory) / "result.json"
        self.stderr = self.resources.enter_context(tempfile.TemporaryFile())  # noqa: SIM115
        self.started = time.monotonic()
        command = script_command(
            "_config-editor",
            "--config-kind",
            kind,
            "--config-path",
            str(path),
            "--config-result",
            str(self.result),
        )
        try:
            self.process = subprocess.Popen(
                [
                    "tmux",
                    "-S",
                    display.tmux.socket,
                    "display-popup",
                    "-E",
                    "-w",
                    "95%",
                    "-h",
                    "95%",
                    "-t",
                    display.sidebar,
                    command,
                ],
                env=clean_env(),
                stdout=subprocess.DEVNULL,
                stderr=self.stderr,
            )
        except BaseException:
            self.resources.close()
            raise

    def poll(self):
        """None means still open; an explicit saved result is required for success."""
        code = self.process.poll()
        if code is None:
            if (
                not self.result.with_suffix(".ready").exists()
                and time.monotonic() - self.started > 10
            ):
                self.close()
                return {"error": "Settings editor did not start; try again."}
            return None
        try:
            if self.result.stat().st_size > 1024:
                raise ValueError("Invalid settings editor result")
            result = json.loads(self.result.read_text())
            if code or result.get("kind") != self.kind or not isinstance(result.get("saved"), bool):
                raise ValueError("Settings editor exited unexpectedly")
            return result
        except (OSError, ValueError, AttributeError):
            self.stderr.seek(0)
            detail = self.stderr.read(1000).decode(errors="replace").strip()
            return {"error": detail or "Settings editor closed without a save result."}

    def close(self):
        try:
            if self.process.poll() is None:
                with contextlib.suppress(RuntimeError, OSError, subprocess.SubprocessError):
                    self.display.tmux.run(
                        "display-popup", "-C", "-t", self.display.sidebar, check=False
                    )
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        finally:
            self.resources.close()
