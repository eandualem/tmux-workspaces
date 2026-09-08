"""Guard public launchers before importing the Python 3.11 application.

Keep this module and the thin entry points parseable on Python 3.6 so an older
interpreter reports the requirement instead of failing inside a runtime import.
"""

import importlib
import sys

MINIMUM_PYTHON = (3, 11)


def require_python():
    """Raise an actionable error before any application modules are imported."""
    if sys.version_info[:2] < MINIMUM_PYTHON:
        detected = ".".join(str(part) for part in sys.version_info[:3])
        raise RuntimeError(
            f"Python {detected} is unsupported; Python 3.11+ is required. "
            "Install Python 3.11 or newer and use it to run this command."
        )


def _dispatch(module, *args):
    try:
        require_python()
    except RuntimeError as error:
        print(f"tmux-workspaces: {error}", file=sys.stderr)
        return 2
    return importlib.import_module(module, __package__).main(*args)


def main():
    """Installed console command and standalone viewer entry point."""
    return _dispatch(".cli")


def ghostty_main(arguments=None):
    """Dedicated terminal launcher, retaining its programmatic argument API."""
    return _dispatch(".ghostty_launcher", arguments)


def preview_main():
    return _dispatch(".ui_preview")


def plugin_main():
    return _dispatch(".tmux_plugin")
