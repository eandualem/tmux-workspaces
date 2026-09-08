"""Validate durable arrangements before recursive model or terminal operations."""

import math
import os
import re
from pathlib import Path

from .targets import valid_session

MAX_TREE_DEPTH = 64
MAX_NODES = 10000
MAX_VALUES = 100000


class InvalidLayout(ValueError):
    """A saved or proposed arrangement does not satisfy its schema."""


def validate_state(state: object, *, navigation: bool = True, legacy: bool = False) -> None:
    def require(condition, location, detail):
        if not condition:
            raise InvalidLayout(f"{location}: {detail}")

    # Bound all containers, including unknown extension fields, before deepcopy,
    # merge or serialization. JSON decode has its own recursion guard as well.
    pending_data = [(state, 0)]
    count = 0
    visited = 0
    while pending_data:
        value, depth = pending_data.pop()
        visited += 1
        if isinstance(value, float):
            require(math.isfinite(value), "layout", "non-finite JSON number")
        if isinstance(value, (dict, list)):
            count += 1
            require(depth <= 128, "layout", "data nesting exceeds 128 levels")
            require(count <= MAX_NODES, "layout", "too many containers (limit 10000)")
            values = value.values() if isinstance(value, dict) else value
            require(
                visited + len(pending_data) + len(values) <= MAX_VALUES,
                "layout",
                "too many data values (limit 100000)",
            )
            pending_data.extend((child, depth + 1) for child in values)
    require(isinstance(state, dict), "layout", "expected an object")
    version = state.get("version")
    require(
        type(version) is int and version in ((1, 2) if legacy else (2,)),
        "layout.version",
        "unsupported version",
    )
    spaces = state.get("workspaces")
    require(
        isinstance(spaces, list) and bool(spaces), "layout.workspaces", "expected a nonempty list"
    )
    identities = set()

    def identify(item, location):
        require(isinstance(item, dict), location, "expected an object")
        key = item.get("id")
        require(
            isinstance(key, str) and re.fullmatch(r"[a-f0-9]{12}", key),
            location + ".id",
            "expected a 12-character lowercase hexadecimal identity",
        )
        require(key not in identities, location + ".id", "duplicate identity")
        identities.add(key)
        require(len(identities) <= MAX_NODES, "layout", "too many objects (limit 10000)")
        return key

    def name(item, location):
        value = item.get("name")
        require(
            isinstance(value, str) and bool(value.strip()) and value.isprintable(),
            location + ".name",
            "expected a nonempty printable name",
        )

    def path(value, location):
        require(
            isinstance(value, str)
            and bool(value)
            and "\0" not in value
            and Path(value).is_absolute(),
            location,
            "expected an absolute path without NUL",
        )

        try:
            os.fsencode(value)
        except UnicodeError as error:
            raise InvalidLayout(
                location + ": path cannot be encoded for this filesystem"
            ) from error

    space_ids = []
    for i, space in enumerate(spaces):
        where = f"workspaces[{i}]"
        space_ids.append(identify(space, where))
        name(space, where)
        tabs = space.get("tabs")
        require(isinstance(tabs, list), where + ".tabs", "expected a list")
        tab_ids = []
        for j, tab in enumerate(tabs):
            location = f"{where}.tabs[{j}]"
            tab_ids.append(identify(tab, location))
            name(tab, location)
            pending = [(tab.get("tree"), location + ".tree", 0)]
            leaf_ids = []
            while pending:
                node, branch, depth = pending.pop()
                require(depth <= MAX_TREE_DEPTH, branch, "split nesting exceeds 64 levels")
                key = identify(node, branch)
                if "agent" in node:
                    require(
                        not any(
                            field in node for field in ("first", "second", "direction", "ratio")
                        ),
                        branch,
                        "leaf also contains split fields",
                    )
                    agent = node["agent"]
                    require(
                        agent is None or valid_session(agent),
                        branch + ".agent",
                        "invalid session name",
                    )
                    if "cwd" in node:
                        path(node["cwd"], branch + ".cwd")
                    if "source_socket" in node:
                        require(
                            agent is not None,
                            branch + ".source_socket",
                            "socket without an attachment",
                        )
                        path(node["source_socket"], branch + ".source_socket")
                    leaf_ids.append(key)
                else:
                    require(
                        node.get("direction") in ("right", "below"),
                        branch + ".direction",
                        "expected right or below",
                    )
                    ratio = node.get("ratio")
                    require(
                        type(ratio) in (int, float)
                        and 0.01 <= ratio <= 0.99
                        and math.isfinite(ratio),
                        branch + ".ratio",
                        "expected a finite split ratio from 0.01 to 0.99",
                    )
                    require(
                        not any(field in node for field in ("cwd", "source_socket")),
                        branch,
                        "split also contains leaf fields",
                    )
                    pending.extend(
                        [
                            (node.get("second"), branch + ".second", depth + 1),
                            (node.get("first"), branch + ".first", depth + 1),
                        ]
                    )
            if navigation:
                require(
                    tab.get("focus") in leaf_ids,
                    location + ".focus",
                    "expected an existing leaf identity",
                )
        if navigation:
            require(
                space.get("selected") in tab_ids if tab_ids else space.get("selected") == "",
                where + ".selected",
                "expected an existing tab identity (or empty for an empty workspace)",
            )
    if navigation:
        require(
            state.get("selected") in space_ids,
            "layout.selected",
            "expected an existing workspace identity",
        )
        require(type(state.get("focus")) is bool, "layout.focus", "expected a boolean")
