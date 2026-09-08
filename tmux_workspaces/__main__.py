"""Standard Python module entry point."""

import contextlib

from .bootstrap import main

with contextlib.suppress(BrokenPipeError):
    raise SystemExit(main())
