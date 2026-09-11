"""Read disposable demo fixture metadata without tmux or service configuration."""

import json
import time
from pathlib import Path

from ..discovery import Snapshot, session_items
from ..targets import valid_session


class DemoProvider:
    # Items carry an agent state, so they make up the sidebar's roster.
    provides_states = True

    def __init__(self, path: Path):
        self.path = path
        self.last = Snapshot()

    def read(self) -> Snapshot:
        try:
            items = session_items(json.loads(self.path.read_text()), valid_session, "demo")
            self.last = Snapshot(items, observed_at=time.time())
        except (OSError, ValueError, TypeError):
            self.last = self.last.unavailable("Demo list unavailable; states stale")
        return self.last
