"""Lifecycle of one built-in editor on the viewer's private tmux server."""

import contextlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

from .entrypoints import script_command
from .tmux import clean_env

# Rows a popup takes beyond its content: the title bar, a blank row, the
# footer and, for the editor, the cursor row.
EDITOR_ROWS = 30


class ConfigPopup:
    def __init__(self, display, kind, path, *, keymap=None, theme_state=None, colors=None):
        self.display, self.kind = display, kind
        self.resources = contextlib.ExitStack()
        directory = self.resources.enter_context(tempfile.TemporaryDirectory(prefix="tw-json-"))
        self.result = Path(directory) / "result.json"
        source = path
        if kind == "reference":
            path = Path(directory) / "keymap.json"
            path.write_text(json.dumps(keymap.to_dict()))
        self.stderr = self.resources.enter_context(tempfile.TemporaryFile())  # noqa: SIM115
        self.started = time.monotonic()
        arguments = [
            "_config-editor",
            "--config-kind",
            kind,
            "--config-path",
            str(path),
            "--config-result",
            str(self.result),
        ]
        if theme_state:
            arguments += ["--chooser-theme", theme_state]
        if colors:
            arguments += ["--terminal-colors", str(colors)]
        if kind == "reference" and source is not None:
            arguments += ["--keymap-source", str(source)]
        command = script_command(*arguments)
        rows = EDITOR_ROWS
        if kind == "reference":
            from .shortcut_reference import reference_rows

            rows = len(reference_rows(keymap)) + 4
        left, bottom, width, height = display.popup_geometry(rows)
        options = [
            "-E",
            "-b",
            "rounded",
            "-x",
            str(left),
            "-y",
            str(bottom),
            "-w",
            str(width),
            "-h",
            str(height),
        ]
        if theme_state:
            options += self._styles(theme_state, colors)
        try:
            self.process = subprocess.Popen(
                [
                    "tmux",
                    "-S",
                    display.tmux.socket,
                    "display-popup",
                    *options,
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

    @staticmethod
    def _styles(theme_state: str, colors: int | None) -> list[str]:
        """The popup's own ground and its dim border, in the theme's colors."""
        from .theme import ThemeError, extra_color, parse_theme_state

        try:
            theme = parse_theme_state(theme_state)
        except (ThemeError, ValueError):
            return []
        palette = colors or 256
        text = theme.tmux_role("normal", palette)[0]
        return [
            "-s",
            f"fg={text},bg={theme.panel}",
            "-S",
            f"fg={extra_color('dim', palette)},bg={theme.panel}",
        ]

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
