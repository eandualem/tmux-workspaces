"""Integration-neutral polling and composition of read-only session providers."""

from __future__ import annotations

import copy
import threading

from .discovery import Provider, Snapshot


class Source:
    def __init__(
        self,
        socket: str,
        discovery: Provider,
        overlays: tuple[Provider, ...] = (),
        *,
        persistent_socket: bool = True,
    ):
        self.socket = socket
        self.discovery, self.overlays = discovery, overlays
        self.persistent_socket = persistent_socket
        self.current = Snapshot()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def refresh(self) -> None:
        available = self.discovery.read()
        agents = copy.deepcopy(available.sessions)
        observations = [available]
        for provider in self.overlays:
            snapshot = provider.read()
            observations.append(snapshot)
            for name, item in snapshot.sessions.items():
                # Only discovery describes attachment availability. Metadata may
                # contribute offline references but never own external sessions.
                agents[name] = {
                    **copy.deepcopy(item),
                    "online": bool(available.sessions.get(name, {}).get("online", False)),
                }
        dates = [item.observed_at for item in observations]
        combined = Snapshot(
            agents,
            error="; ".join(item.error for item in observations if item.error),
            stale=any(item.stale for item in observations),
            observed_at=min(dates) if all(date is not None for date in dates) else None,
        )
        with self.lock:
            self.current = combined

    def observation(self) -> Snapshot:
        with self.lock:
            return copy.deepcopy(self.current)

    def snapshot(self) -> tuple[dict[str, dict], str]:
        observation = self.observation()
        return observation.sessions, observation.error

    def start(self) -> None:
        if self.thread is not None:
            return

        def poll():
            while not self.stop_event.wait(5):
                self.refresh()

        self.thread = threading.Thread(target=poll, daemon=True, name="workspace-sessions")
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
