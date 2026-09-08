"""Standard Python module entry point."""

import contextlib

from .cli import main

with contextlib.suppress(BrokenPipeError):
    raise SystemExit(main())
