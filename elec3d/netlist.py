# -*- coding: utf-8 -*-
"""KiCad legacy ``.net`` parser and document-free placeholder helpers.

The import wizard deliberately keeps this module free of Qt and FreeCAD.  A
KiCad netlist contains component references/values/footprint names and the
connectivity between reference/pin pairs; it does not contain footprint
geometry.  Until the corresponding ``.kicad_mod`` file is resolved, the viewer
uses a small generated footprint so placement and the ratsnest remain usable.
"""

from . import sexpr
from .sexpr import child, children, value
from .footprint import Footprint, Pad, Line, Text

__all__ = [
    "NetlistComponent", "NetlistNet", "KiCadNetlist", "parse_netlist",
    "parse_netlist_file", "placeholder_footprint", "pins_by_ref",
    "ratsnest_mst_pairs",
]


class NetlistComponent(object):
    def __init__(self, ref, value_text="", footprint=""):
        self.ref = str(ref or "?")
        self.value = str(value_text or "")
        self.footprint = str(footprint or "")

    def snapshot(self):
        return {"ref": self.ref, "value": self.value, "footprint": self.footprint}


class NetlistNet(object):
    def __init__(self, name="", nodes=None):
        self.name = str(name or "")
        self.nodes = list(nodes or [])  # [{"ref": str, "pin": str}]

    def snapshot(self):
        return {"name": self.name, "nodes": [dict(n) for n in self.nodes]}


class KiCadNetlist(object):
    def __init__(self, components=None, nets=None, source_file=""):
        self.components = list(components or [])
        self.nets = list(nets or [])
        self.source_file = str(source_file or "")

    def footprint_names(self):
        seen = []
        for comp in self.components:
            if comp.footprint and comp.footprint not in seen:
                seen.append(comp.footprint)
        return seen

    def pad_net_map(self):
        """Return ``(reference, pin) -> net name`` exactly as in the netlist."""
        result = {}
        for net in self.nets:
            for node in net.nodes:
                ref = str(node.get("ref", ""))
                pin = str(node.get("pin", ""))
                if ref and pin:
                    result[(ref, pin)] = net.name
        return result

    def net_name(self, ref, pin, default=None):
        """Look up the schematic net attached to one component pad."""
        return self.pad_net_map().get((str(ref or ""), str(pin or "")), default)

    def snapshot(self):
        return {
            "source_file": self.source_file,
            "components": [c.snapshot() for c in self.components],
            "nets": [n.snapshot() for n in self.nets],
        }

    @classmethod
    def restore(cls, data):
        data = data or {}
        return cls(
            [NetlistComponent(c.get("ref"), c.get("value"), c.get("footprint"))
             for c in data.get("components", [])],
            [NetlistNet(n.get("name"), n.get("nodes", []))
             for n in data.get("nets", [])],
            data.get("source_file", ""),
        )


def parse_netlist(src, source_file=""):
    root = sexpr.parse(src)
    if not root or root[0] != "export":
        head = root[0] if root else "?"
        raise ValueError("Expected a KiCad netlist '(export ...)' root, got '(%s ...)'." % head)

    comps_node = child(root, "components") or []
    components = []
    for node in children(comps_node, "comp"):
        components.append(NetlistComponent(
            value(node, "ref", "?"),
            value(node, "value", ""),
            value(node, "footprint", ""),
        ))

    nets_node = child(root, "nets") or []
    nets = []
    for node in children(nets_node, "net"):
        nodes = []
        for nd in children(node, "node"):
            ref = value(nd, "ref", "")
            pin = value(nd, "pin", "")
            if ref and pin:
                nodes.append({"ref": str(ref), "pin": str(pin)})
        nets.append(NetlistNet(value(node, "name", ""), nodes))

    return KiCadNetlist(components, nets, source_file)


def parse_netlist_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_netlist(fh.read(), source_file=path)


def _pin_sort_key(pin):
    text = str(pin)
    try:
        return (0, int(text), text)
    except ValueError:
        return (1, 0, text)


def pins_by_ref(netlist):
    result = {}
    for net in netlist.nets:
        for node in net.nodes:
            result.setdefault(node["ref"], set()).add(str(node["pin"]))
    return dict((ref, sorted(pins, key=_pin_sort_key)) for ref, pins in result.items())



def ratsnest_mst_pairs(points):
    """Return index pairs for the shortest spanning tree over ``points``.

    The reference web viewer draws a ratsnest as a minimum spanning tree rather
    than a star rooted at whichever net node happens to be listed first.  This
    pure helper implements the same geometry-only part for the current editor,
    which does not yet contain routed copper/vias.  ``points`` may contain
    ``(x, y)`` tuples or objects with ``x``/``y`` attributes.
    """
    pts = list(points or [])
    if len(pts) < 2:
        return []

    def xy(p):
        if hasattr(p, "x") and hasattr(p, "y"):
            x = p.x() if callable(p.x) else p.x
            y = p.y() if callable(p.y) else p.y
            return float(x), float(y)
        return float(p[0]), float(p[1])

    coords = [xy(p) for p in pts]
    # Collapse coincident pads.  They are already electrically at the same
    # physical point and drawing a zero-length airwire is not useful.
    unique = []
    representatives = []
    seen = {}
    for index, (x, y) in enumerate(coords):
        key = (round(x, 9), round(y, 9))
        if key in seen:
            continue
        seen[key] = len(unique)
        unique.append((x, y))
        representatives.append(index)
    if len(unique) < 2:
        return []

    in_tree = {0}
    rest = set(range(1, len(unique)))
    result = []
    while rest:
        best = None
        for i in in_tree:
            ax, ay = unique[i]
            for j in rest:
                bx, by = unique[j]
                d2 = (ax - bx) ** 2 + (ay - by) ** 2
                if best is None or d2 < best[0]:
                    best = (d2, i, j)
        if best is None:
            break
        _d2, i, j = best
        result.append((representatives[i], representatives[j]))
        in_tree.add(j)
        rest.remove(j)
    return result

def placeholder_footprint(footprint_name, pins=None):
    """Return a compact generated Footprint while real geometry is unresolved."""
    short = (footprint_name or "unknown").split(":")[-1] or "unknown"
    fp = Footprint(short)
    fp.descr = "placeholder - footprint file not loaded yet"
    fp.attr = "placeholder"
    pins = list(pins or ["1", "2"])
    if not pins:
        pins = ["1", "2"]

    per_row = max(1, (len(pins) + 1) // 2)
    pitch = 1.27
    half_w = ((per_row - 1) * pitch) / 2.0
    body_half_w = half_w + 1.2
    top_y, bottom_y = -1.6, 1.6
    body_y = 2.4

    fp.lines.extend([
        Line(-body_half_w, -body_y, body_half_w, -body_y, "Dwgs.User", 0.1),
        Line(body_half_w, -body_y, body_half_w, body_y, "Dwgs.User", 0.1),
        Line(body_half_w, body_y, -body_half_w, body_y, "Dwgs.User", 0.1),
        Line(-body_half_w, body_y, -body_half_w, -body_y, "Dwgs.User", 0.1),
    ])
    fp.texts.append(Text("reference", "REF**", 0.0, 0.0, 0.0,
                         0.9, 0.15, "F.SilkS", False))

    for index, pin in enumerate(pins):
        row = 0 if index < per_row else 1
        col = index if row == 0 else index - per_row
        x = col * pitch - half_w
        y = top_y if row == 0 else bottom_y
        fp.pads.append(Pad(str(pin), "smd", "rect", x, y, 0.0,
                           0.8, 0.8, ["F.Cu", "F.Paste"]))
    return fp
