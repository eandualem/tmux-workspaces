"""Saved workspace layouts and attachment references; never external session state."""

from __future__ import annotations

import copy
import uuid
from pathlib import Path

from .layout_validation import InvalidLayout, validate_state


def identity() -> str:
    return uuid.uuid4().hex[:12]


def leaf(cwd: str | None = None, *, empty: bool = False) -> dict:
    """A pane. Empty ones run a chooser instead of a shell until one is picked."""
    item = {"id": identity(), "agent": None, "cwd": cwd or str(Path.cwd())}
    if empty:
        item["empty"] = True
    return item


def is_empty(pane: dict) -> bool:
    return bool(pane.get("empty")) and pane.get("agent") is None


def leaves(tree: dict | None) -> list[dict]:
    if not tree:
        return []
    if "agent" in tree:
        return [tree]
    return leaves(tree["first"]) + leaves(tree["second"])


def split(tree: dict, target: str, direction: str, cwd: str | None = None) -> str:
    if direction not in {"right", "below"}:
        raise ValueError("Invalid split direction")
    if tree["id"] == target and "agent" in tree:
        old = copy.deepcopy(tree)
        added = leaf(cwd)
        tree.clear()
        tree.update(id=identity(), direction=direction, ratio=0.5, first=old, second=added)
        return added["id"]
    if "agent" not in tree:
        for child in (tree["first"], tree["second"]):
            if any(item["id"] == target for item in leaves(child)):
                return split(child, target, direction, cwd)
    raise ValueError("Selected pane no longer exists")


def minimum_size(tree: dict | None) -> tuple[int, int]:
    if not tree or "agent" in tree:
        return 34, 6
    first, second = minimum_size(tree["first"]), minimum_size(tree["second"])
    if tree["direction"] == "right":
        return first[0] + second[0] + 1, max(first[1], second[1])
    return max(first[0], second[0]), first[1] + second[1] + 1


def remove_leaf(tree: dict, target: str) -> dict | None:
    if "agent" in tree:
        return None if tree["id"] == target else tree
    first = remove_leaf(tree["first"], target)
    second = remove_leaf(tree["second"], target)
    if first is None:
        return second
    if second is None:
        return first
    return {**tree, "first": first, "second": second}


def new_tab(name: str | None = None, *, empty: bool = False) -> dict:
    tree = leaf(empty=empty)
    return {
        "id": identity(),
        "name": name or "Tab " + identity()[:4],
        "tree": tree,
        "focus": tree["id"],
    }


def workspace(name: str) -> dict:
    return {"id": identity(), "name": name, "tabs": [], "selected": ""}


class Model:
    def __init__(self, state: dict):
        self.state = state

    @classmethod
    def initial(cls) -> Model:
        space = workspace("Workspace 1")
        model = cls({"version": 2, "workspaces": [space], "selected": space["id"], "focus": False})
        model.add_tab()
        return model

    @property
    def space(self) -> dict:
        spaces = self.state["workspaces"]
        return next((s for s in spaces if s["id"] == self.state["selected"]), spaces[0])

    @property
    def tab(self) -> dict | None:
        tabs = self.space["tabs"]
        return next(
            (t for t in tabs if t["id"] == self.space["selected"]), tabs[0] if tabs else None
        )

    def add_tab(self, name: str | None = None, *, empty: bool = False) -> None:
        tab = new_tab(name, empty=empty)
        self.space["tabs"].append(tab)
        self.space["selected"] = tab["id"]

    def add_workspace(self, name: str) -> None:
        space = workspace(name)
        self.state["workspaces"].append(space)
        self.state["selected"] = space["id"]

    def split(self, direction: str, cwd: str | None = None) -> None:
        tab = self.tab
        if not tab:
            self.add_tab()
            return
        tab["focus"] = split(tab["tree"], tab["focus"], direction, cwd)
        self.state["focus"] = False

    @property
    def pane(self) -> dict | None:
        tab = self.tab
        return (
            next((p for p in leaves(tab["tree"]) if p["id"] == tab["focus"]), None) if tab else None
        )

    def attach(self, agent: str | None, source_socket: str | None = None) -> None:
        if not self.pane:
            raise ValueError("Create a tab first")
        self.pane["agent"] = agent
        # Choosing anything, including a return to shell, ends the empty state.
        self.pane.pop("empty", None)
        # Keep the v2 field for compatibility; it now also represents generic sessions.
        # Names are meaningful only on their original server, including while offline.
        if agent and source_socket:
            self.pane["source_socket"] = source_socket
        else:
            self.pane.pop("source_socket", None)

    def open_terminal(self) -> None:
        """Give the focused empty pane an ordinary shell."""
        if not self.pane:
            raise ValueError("Create a tab first")
        self.pane.pop("empty", None)

    def close_tab(self) -> None:
        tab = self.tab
        if tab:
            self.space["tabs"].remove(tab)
            self.space["selected"] = self.space["tabs"][0]["id"] if self.space["tabs"] else ""

    def close_pane(self) -> None:
        tab = self.tab
        if not tab:
            return
        tree = remove_leaf(tab["tree"], tab["focus"])
        if tree is None:
            self.close_tab()
        else:
            tab["tree"] = tree
            tab["focus"] = leaves(tree)[0]["id"]

    def next_pane(self, offset: int = 1) -> None:
        if self.tab:
            ids = [item["id"] for item in leaves(self.tab["tree"])]
            self.tab["focus"] = ids[(ids.index(self.tab["focus"]) + offset) % len(ids)]


class LayoutConflict(ValueError):
    pass


def shared_layout(state: dict) -> dict:
    result = copy.deepcopy(state)
    result.pop("selected", None)
    result.pop("focus", None)
    for space in result["workspaces"]:
        space.pop("selected", None)
        for tab in space["tabs"]:
            tab.pop("focus", None)
    return result


def local_navigation(shared: dict, previous: dict) -> dict:
    """Shared layout edits never change another window's current selection."""
    state = copy.deepcopy(shared)
    old_spaces = {space["id"]: space for space in previous["workspaces"]}
    old_tabs = {tab["id"]: tab for space in old_spaces.values() for tab in space["tabs"]}
    for space in state["workspaces"]:
        ids = [tab["id"] for tab in space["tabs"]]
        selected = old_spaces.get(space["id"], {}).get("selected")
        space["selected"] = selected if selected in ids else (ids[0] if ids else "")
        for tab in space["tabs"]:
            ids = [item["id"] for item in leaves(tab["tree"])]
            selected = old_tabs.get(tab["id"], {}).get("focus")
            tab["focus"] = selected if selected in ids else ids[0]
    ids = [space["id"] for space in state["workspaces"]]
    state["selected"] = previous.get("selected") if previous.get("selected") in ids else ids[0]
    state["focus"] = previous.get("focus", False)
    return state


_MISSING = object()


def merge_layout(base, mine, latest, path=()):
    """Three-way merge by stable IDs; refuse conflicting edits to the same field."""
    if mine == base:
        return latest
    if latest == base or mine == latest:
        return mine
    if path and path[-1] == "tree":
        raise LayoutConflict("Layout changed in another window; refreshed. Please try again.")
    if all(isinstance(value, dict) for value in (base, mine, latest)):
        result = {}
        for key in base.keys() | mine.keys() | latest.keys():
            value = merge_layout(
                base.get(key, _MISSING),
                mine.get(key, _MISSING),
                latest.get(key, _MISSING),
                (*path, key),
            )
            if value is not _MISSING:
                result[key] = value
        return result
    if all(isinstance(value, list) for value in (base, mine, latest)):
        mappings = [{item["id"]: item for item in value} for value in (base, mine, latest)]
        merged = merge_layout(*mappings, path)
        # Preserve the other window's order unless this window reordered existing items.
        common = set(mappings[0]) & set(mappings[1]) & set(mappings[2])
        reordered = [x for x in mappings[0] if x in common] != [
            x for x in mappings[1] if x in common
        ]
        latest_order = [x for x in mappings[2] if x in common]
        base_order = [x for x in mappings[0] if x in common]
        mine_order = [x for x in mappings[1] if x in common]
        if reordered and latest_order not in (base_order, mine_order):
            raise LayoutConflict("Order changed in another window; refreshed. Please try again.")
        primary, secondary = (mappings[1], mappings[2]) if reordered else (mappings[2], mappings[1])
        order = dict.fromkeys([*primary, *secondary])
        return [merged[key] for key in order if key in merged]
    raise LayoutConflict("Item changed in another window; refreshed. Please try again.")


def validate_library(state: dict) -> None:
    spaces = state["workspaces"]
    if not spaces:
        raise LayoutConflict("Keep at least one workspace; refreshed. Please try again.")
    ids = [space["id"] for space in spaces]
    ids.extend(tab["id"] for space in spaces for tab in space["tabs"])
    if len(set(ids)) != len(ids):
        raise LayoutConflict("Tab moved in another window; refreshed. Please try again.")
    try:
        validate_state(state, navigation=False)
    except InvalidLayout as error:
        raise LayoutConflict("Merged layout is invalid; refreshed. Please try again.") from error
