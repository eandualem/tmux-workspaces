"""Resize padded split layouts without replacing any terminal clients."""

from .model import leaves, minimum_size


def splits(tree):
    if not tree or "agent" in tree:
        return []
    return [tree, *splits(tree["first"]), *splits(tree["second"])]


def bounds(tree, panes, geometry):
    items = [geometry[panes[leaf["id"]]] for leaf in leaves(tree)]
    left, top = min(item.left for item in items), min(item.top for item in items)
    return (
        left,
        top,
        max(item.left + item.width for item in items) - left,
        max(item.top + item.height for item in items) - top,
    )


def minimum(tree):
    width, height = minimum_size(tree)
    if "agent" in tree:
        return width, height
    first, second = minimum(tree["first"]), minimum(tree["second"])
    if tree["direction"] == "right":
        return first[0] + second[0] + 3, max(first[1], second[1])
    return max(first[0], second[0]), first[1] + second[1] + 3


def layout(tree, panes, bands, sidebar, blanks, state):
    """Encode an exact tmux layout, retaining the sidebar and one-cell gutters."""

    def cell(rect, pane=None, children=None, right=True):
        x, y, width, height = rect
        head = f"{width}x{height},{x},{y}"
        if pane is not None:
            return head + "," + pane.removeprefix("%")
        opening, closing = ("{", "}") if right else ("[", "]")
        return head + opening + ",".join(children) + closing

    def branch(node, rect):
        if "agent" in node:
            return cell(rect, panes[node["id"]])
        x, y, width, height = rect
        right = node["direction"] == "right"
        axis = 0 if right else 1
        available = (width if right else height) - 3
        lower, upper = minimum(node["first"])[axis], minimum(node["second"])[axis]
        if available < lower + upper:
            raise ValueError("Window is too small to resize this split")
        first = min(max(round(node.get("ratio", 0.5) * available), lower), available - upper)
        if right:
            a, band, b = (
                (x, y, first, height),
                (x + first + 1, y, 1, height),
                (x + first + 3, y, available - first, height),
            )
        else:
            a, band, b = (
                (x, y, width, first),
                (x, y + first + 1, width, 1),
                (x, y + first + 3, width, available - first),
            )
        return cell(
            rect,
            children=[
                branch(node["first"], a),
                cell(band, bands[node["id"]]),
                branch(node["second"], b),
            ],
            right=right,
        )

    width, height = state.size
    panel = state.panes[sidebar].width
    left, right = sorted(blanks, key=lambda pane: state.panes[pane].left)
    body = cell(
        (0, 0, width, height),
        children=[
            cell((0, 0, panel, height), sidebar),
            cell((panel + 1, 0, 1, height), left),
            branch(tree, (panel + 3, 0, width - panel - 5, height)),
            cell((width - 1, 0, 1, height), right),
        ],
    )
    checksum = 0
    for byte in body.encode():
        checksum = ((checksum >> 1) | ((checksum & 1) << 15)) + byte
        checksum &= 0xFFFF
    return f"{checksum:04x}," + body
