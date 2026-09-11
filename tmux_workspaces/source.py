"""Integration-neutral polling and composition of read-only session providers."""

from __future__ import annotations

import copy
import threading

from .discovery import Provider, Snapshot
from .targets import valid_session


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
        # The roster: agents and their states, from the providers that report
        # states (a metadata overlay such as Backbone, or the demo list).
        # Plain tmux discovery knows session names, not what an agent is doing.
        self.providers = (discovery, *overlays)
        self.roster_indexes = [
            index
            for index, provider in enumerate(self.providers)
            if getattr(provider, "provides_states", False)
        ]
        self.current_roster: Snapshot | None = Snapshot() if self.roster_indexes else None
        self._provider_snapshots = [Snapshot() for _ in range(1 + len(overlays))]
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def _read(self, provider: Provider, index: int) -> Snapshot:
        try:
            snapshot = provider.read()
            if (
                not isinstance(snapshot, Snapshot)
                or not isinstance(snapshot.sessions, dict)
                or not isinstance(snapshot.error, str)
                or not isinstance(snapshot.stale, bool)
                or (
                    snapshot.observed_at is not None
                    and not isinstance(snapshot.observed_at, (int, float))
                )
                or any(
                    not valid_session(name) or not isinstance(item, dict)
                    for name, item in snapshot.sessions.items()
                )
            ):
                raise ValueError("Invalid provider observation")
            # Keep provider-owned objects separate from both the UI snapshot and
            # the fallback used if this provider later raises unexpectedly.
            detached = copy.deepcopy(snapshot)
            if not snapshot.stale:
                self._provider_snapshots[index] = detached
            return detached
        except Exception:
            # Third-party providers can fail outside their expected IO paths.
            # A failure must not prevent another provider from publishing or
            # terminate polling. Never expose exception text (it may hold secrets),
            # and deliberately let BaseException cancellation propagate.
            previous = self._provider_snapshots[index]
            if index == 0:
                # Cached discovery names are references, not proof that their
                # sessions remain attachable after discovery becomes unavailable.
                return Snapshot(
                    {name: {**item, "online": False} for name, item in previous.sessions.items()},
                    error="Session discovery unavailable; states stale",
                    stale=True,
                    observed_at=previous.observed_at,
                )
            return previous.unavailable("Session metadata unavailable; states stale")

    def refresh(self) -> None:
        available = self._read(self.discovery, 0)
        agents = copy.deepcopy(available.sessions)
        observations = [available]
        for index, provider in enumerate(self.overlays, 1):
            snapshot = self._read(provider, index)
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
        roster = None
        if self.roster_indexes:
            reports = [observations[index] for index in self.roster_indexes]
            roster = Snapshot(
                {
                    name: copy.deepcopy(item)
                    for report in reports
                    for name, item in report.sessions.items()
                },
                error="; ".join(report.error for report in reports if report.error),
                stale=any(report.stale for report in reports),
                observed_at=min(
                    (report.observed_at for report in reports if report.observed_at is not None),
                    default=None,
                ),
            )
        with self.lock:
            self.current = combined
            self.current_roster = roster

    def observation(self) -> Snapshot:
        with self.lock:
            return copy.deepcopy(self.current)

    def roster(self) -> Snapshot | None:
        """Agents and the states they report, or None when no provider reports
        states. A stale roster carries its last names, but its states are not
        current and the UI says so rather than showing them."""
        with self.lock:
            return copy.deepcopy(self.current_roster)

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
