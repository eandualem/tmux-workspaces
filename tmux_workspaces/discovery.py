"""Read-only session observations shared by providers and the UI source."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol


@dataclass(frozen=True)
class Snapshot:
    """Online means attachable; stale describes observation freshness, not ownership.

    Only the primary discovery provider supplies attachment availability; metadata
    providers enrich names/states and do not establish online status.
    observed_at is the last successful observation's Unix timestamp, or None when
    no successful observation exists. Errors never contain credentials or payloads.
    Providers own their session dictionaries; consumers receive detached copies.
    """

    sessions: dict[str, dict] = field(default_factory=dict)
    error: str = ""
    stale: bool = False
    observed_at: float | None = None

    def unavailable(self, error: str) -> Snapshot:
        return replace(self, error=error, stale=True)


class Provider(Protocol):
    """Discover or enrich sessions without owning their lifecycle."""

    def read(self) -> Snapshot: ...


def session_items(items: object, valid_name: Callable, origin: str) -> dict[str, dict]:
    if not isinstance(items, list):
        raise ValueError("Invalid session list")
    result = {}
    for item in items:
        if (
            not isinstance(item, dict)
            or not valid_name(item.get("name"))
            or not item.get("configured", True)
        ):
            continue
        item = dict(item)
        item["origin"] = origin
        if not isinstance(item.get("state"), str):
            item["state"] = "unknown"
        result[item["name"]] = item
    return result
