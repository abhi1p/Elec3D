# -*- coding: utf-8 -*-
"""Parse a ``.kicad_mod`` file into a plain Python model.

Nothing in this module imports FreeCAD, so it can be exercised from a normal
Python interpreter (see ``tests/test_parser.py``).

All coordinates are kept exactly as they appear in the file, i.e. in
millimetres with **Y pointing down** (KiCad convention).  Converting to the
FreeCAD convention (Y up) is the job of ``builder.py``.
"""

import math
import os

from . import sexpr
from .sexpr import child, children, has_flag, num, nums, value

__all__ = [
    "Footprint", "Pad", "Drill", "Text", "Line", "Rect", "Circle", "Arc",
    "Poly", "Model", "parse_footprint", "parse_footprint_file",
]


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
class _Base(object):
    def __repr__(self):
        cls = self.__class__.__name__
        items = ", ".join("%s=%r" % (k, v) for k, v in sorted(self.__dict__.items()))
        return "%s(%s)" % (cls, items)


class Line(_Base):
    def __init__(self, x1, y1, x2, y2, layer, width):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.layer, self.width = layer, width


class Rect(_Base):
    def __init__(self, x1, y1, x2, y2, layer, width, filled=False):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.layer, self.width, self.filled = layer, width, filled


class Circle(_Base):
    def __init__(self, cx, cy, r, layer, width, filled=False):
        self.cx, self.cy, self.r = cx, cy, r
        self.layer, self.width, self.filled = layer, width, filled


class Arc(_Base):
    """Always normalised to start / mid / end points."""

    def __init__(self, start, mid, end, layer, width):
        self.start, self.mid, self.end = start, mid, end
        self.layer, self.width = layer, width


class Poly(_Base):
    def __init__(self, points, layer, width, filled=True):
        self.points, self.layer = points, layer
        self.width, self.filled = width, filled


class Text(_Base):
    def __init__(self, kind, text, x, y, rot, size, thickness, layer, hide, mirror=False):
        self.kind, self.text = kind, text
        self.x, self.y, self.rot = x, y, rot
        self.size, self.thickness = size, thickness
        self.layer, self.hide, self.mirror = layer, hide, mirror


class Drill(_Base):
    def __init__(self, oval, w, h, offx=0.0, offy=0.0):
        self.oval, self.w, self.h = oval, w, h
        self.offx, self.offy = offx, offy


class Pad(_Base):
    def __init__(self, number, pad_type, shape, x, y, rot, w, h, layers,
                 drill=None, rratio=0.25, delta=(0.0, 0.0), primitives=None,
                 anchor="rect", chamfer_ratio=0.0, chamfer=None):
        self.number, self.pad_type, self.shape = number, pad_type, shape
        self.x, self.y, self.rot = x, y, rot
        self.w, self.h = w, h
        self.layers = layers or []
        self.drill = drill
        self.rratio = rratio
        self.delta = delta
        self.primitives = primitives or []
        self.anchor = anchor
        self.chamfer_ratio = chamfer_ratio
        self.chamfer = chamfer or []
        # Filled by the netlist-aware viewer.  Keeping the name on the pad model
        # makes Properties/hover/highlighting use one authoritative assignment.
        self.net_name = None

    @property
    def is_through_hole(self):
        return self.pad_type in ("thru_hole", "np_thru_hole")

    @property
    def copper_layer(self):
        for candidate in ("*.Cu", "F.Cu", "B.Cu"):
            if candidate in self.layers:
                return candidate
        return self.layers[0] if self.layers else "F.Cu"


class Model(_Base):
    def __init__(self, path, offset, scale, rotate):
        self.path, self.offset = path, offset
        self.scale, self.rotate = scale, rotate


class Footprint(_Base):
    def __init__(self, name):
        self.name = name
        self.descr = ""
        self.tags = ""
        self.attr = ""
        self.layer = "F.Cu"
        self.version = ""
        self.generator = ""
        self.source_file = ""
        # Keep the parsed source text in memory so a .e3d_pcb board can preserve the exact layout geometry even
        # if the project footprint copy or original library later moves.
        self.source_text = ""
        self.original_source_file = ""
        self.lines = []
        self.rects = []
        self.circles = []
        self.arcs = []
        self.polys = []
        self.texts = []
        self.pads = []
        self.model = None
        self.warnings = []

    # -- convenience ------------------------------------------------------
    def layers_used(self):
        seen = []
        for item in (self.lines + self.rects + self.circles + self.arcs +
                     self.polys + self.texts):
            if item.layer not in seen:
                seen.append(item.layer)
        for pad in self.pads:
            lay = pad.copper_layer
            if lay not in seen:
                seen.append(lay)
        return seen

    def bbox(self):
        """(min_x, min_y, max_x, max_y) in KiCad coordinates."""
        xs, ys = [], []

        def add(x, y):
            xs.append(x)
            ys.append(y)

        for l in self.lines:
            add(l.x1, l.y1)
            add(l.x2, l.y2)
        for r in self.rects:
            add(r.x1, r.y1)
            add(r.x2, r.y2)
        for c in self.circles:
            add(c.cx - c.r, c.cy - c.r)
            add(c.cx + c.r, c.cy + c.r)
        for a in self.arcs:
            for p in (a.start, a.mid, a.end):
                add(p[0], p[1])
        for p in self.polys:
            for pt in p.points:
                add(pt[0], pt[1])
        for pad in self.pads:
            half = max(pad.w, pad.h) / 2.0
            add(pad.x - half, pad.y - half)
            add(pad.x + half, pad.y + half)
        if not xs:
            return (-5.0, -5.0, 5.0, 5.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def summary(self):
        return "%s - %d pad(s), %d graphic item(s), layers: %s" % (
            self.name, len(self.pads),
            len(self.lines) + len(self.rects) + len(self.circles) +
            len(self.arcs) + len(self.polys),
            ", ".join(self.layers_used()) or "-")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _stroke_width(node, fallback=0.12):
    w = child(node, "width")
    if w is not None and len(w) > 1:
        return num(w[1], fallback)
    stroke = child(node, "stroke")
    if stroke is not None:
        sw = child(stroke, "width")
        if sw is not None and len(sw) > 1:
            return num(sw[1], fallback)
    return fallback


def _layer_of(node):
    lay = child(node, "layer")
    if lay is None or len(lay) < 2:
        return "unknown"
    return lay[1]


def _is_filled(node, default=False):
    fill = child(node, "fill")
    if fill is None:
        return default
    if len(fill) < 2:
        return default
    return fill[1] in ("solid", "yes", "true")


def _xy(node, default=(0.0, 0.0)):
    if node is None:
        return default
    vals = nums(node)
    if len(vals) < 2:
        return default
    return (vals[0], vals[1])


def _xyz(node, default=(0.0, 0.0, 0.0)):
    if node is None:
        return default
    inner = child(node, "xyz") or node
    vals = nums(inner)
    if len(vals) < 3:
        return default
    return (vals[0], vals[1], vals[2])


def _arc_mid_from_angle(cx, cy, sx, sy, angle_deg):
    """Legacy arc: (start = centre) (end = start point) (angle a)."""
    r = math.hypot(sx - cx, sy - cy)
    a0 = math.atan2(sy - cy, sx - cx)
    a_half = a0 + math.radians(angle_deg) / 2.0
    a_end = a0 + math.radians(angle_deg)
    mid = (cx + r * math.cos(a_half), cy + r * math.sin(a_half))
    end = (cx + r * math.cos(a_end), cy + r * math.sin(a_end))
    return (sx, sy), mid, end


def _parse_primitives(node):
    """Custom pad primitives, kept as raw graphic objects in pad local coords."""
    out = []
    prim = child(node, "primitives")
    if prim is None:
        return out
    for p in prim:
        if not isinstance(p, list) or not p:
            continue
        kind = p[0]
        width = _stroke_width(p, 0.0)
        filled = _is_filled(p, True)
        if kind == "gr_line":
            s, e = _xy(child(p, "start")), _xy(child(p, "end"))
            out.append(Line(s[0], s[1], e[0], e[1], "pad", width))
        elif kind == "gr_rect":
            s, e = _xy(child(p, "start")), _xy(child(p, "end"))
            out.append(Rect(s[0], s[1], e[0], e[1], "pad", width, filled))
        elif kind == "gr_circle":
            c, e = _xy(child(p, "center")), _xy(child(p, "end"))
            out.append(Circle(c[0], c[1], math.hypot(e[0] - c[0], e[1] - c[1]),
                              "pad", width, filled))
        elif kind == "gr_arc":
            s = _xy(child(p, "start"))
            m = child(p, "mid")
            e = _xy(child(p, "end"))
            if m is not None:
                out.append(Arc(s, _xy(m), e, "pad", width))
            else:
                st, mid, end = _arc_mid_from_angle(
                    s[0], s[1], e[0], e[1], num(value(p, "angle", "90"), 90.0))
                out.append(Arc(st, mid, end, "pad", width))
        elif kind == "gr_poly":
            pts = child(p, "pts")
            if pts is not None:
                pointlist = [_xy(x) for x in children(pts, "xy")]
                if len(pointlist) >= 3:
                    out.append(Poly(pointlist, "pad", width, filled))
    return out


# ---------------------------------------------------------------------------
# main entry points
# ---------------------------------------------------------------------------
def parse_footprint(src, source_file=""):
    """Parse the text of a ``.kicad_mod`` file into a :class:`Footprint`."""
    root = sexpr.parse(src)
    if not root or root[0] not in ("module", "footprint"):
        raise ValueError("Not a KiCad footprint: expected (footprint ...) or "
                         "(module ...), got (%s ...)" % (root[0] if root else "empty"))

    name = root[1] if len(root) > 1 and isinstance(root[1], str) else "unnamed"
    fp = Footprint(name)
    fp.source_file = source_file
    fp.original_source_file = source_file
    fp.source_text = str(src)
    fp.descr = value(root, "descr", "")
    fp.tags = value(root, "tags", "")
    fp.version = value(root, "version", "")
    fp.generator = value(root, "generator", "")
    fp.layer = value(root, "layer", "F.Cu")
    fp.attr = " ".join(" ".join(a[1:]) for a in children(root, "attr"))

    for node in root:
        if not isinstance(node, list) or not node:
            continue
        kind = node[0]

        # ---------------- graphics ----------------
        if kind in ("fp_line", "gr_line"):
            s, e = _xy(child(node, "start")), _xy(child(node, "end"))
            fp.lines.append(Line(s[0], s[1], e[0], e[1],
                                 _layer_of(node), _stroke_width(node)))

        elif kind in ("fp_rect", "gr_rect"):
            s, e = _xy(child(node, "start")), _xy(child(node, "end"))
            fp.rects.append(Rect(s[0], s[1], e[0], e[1], _layer_of(node),
                                 _stroke_width(node), _is_filled(node)))

        elif kind in ("fp_circle", "gr_circle"):
            c, e = _xy(child(node, "center")), _xy(child(node, "end"))
            fp.circles.append(Circle(c[0], c[1],
                                     math.hypot(e[0] - c[0], e[1] - c[1]),
                                     _layer_of(node), _stroke_width(node),
                                     _is_filled(node)))

        elif kind in ("fp_arc", "gr_arc"):
            s = _xy(child(node, "start"))
            m = child(node, "mid")
            e = _xy(child(node, "end"))
            if m is not None:                      # KiCad 6+ : start / mid / end
                fp.arcs.append(Arc(s, _xy(m), e, _layer_of(node),
                                   _stroke_width(node)))
            else:                                  # legacy : centre / start / angle
                angle = num(value(node, "angle", "90"), 90.0)
                st, mid, end = _arc_mid_from_angle(s[0], s[1], e[0], e[1], angle)
                fp.arcs.append(Arc(st, mid, end, _layer_of(node),
                                   _stroke_width(node)))

        elif kind in ("fp_poly", "gr_poly"):
            pts = child(node, "pts")
            if pts is None:
                continue
            points = [_xy(p) for p in children(pts, "xy")]
            if len(points) >= 3:
                fp.polys.append(Poly(points, _layer_of(node),
                                     _stroke_width(node, 0.0), _is_filled(node, True)))

        # ---------------- text ----------------
        elif kind in ("fp_text", "property"):
            if kind == "fp_text":
                text_kind = node[1] if len(node) > 1 else "user"
                text = node[2] if len(node) > 2 else ""
            else:
                text_kind = str(node[1]).lower() if len(node) > 1 else ""
                text = node[2] if len(node) > 2 else ""
                if text_kind not in ("reference", "value"):
                    continue           # datasheet / description carry no geometry
            at = child(node, "at")
            if at is None:
                continue
            pos = nums(at)
            effects = child(node, "effects")
            font = child(effects, "font") if effects else None
            size_node = child(font, "size") if font else None
            size_vals = nums(size_node) if size_node else []
            thickness = num(value(font, "thickness", "0.15"), 0.15) if font else 0.15
            fp.texts.append(Text(
                kind=text_kind,
                text=text,
                x=pos[0] if len(pos) > 0 else 0.0,
                y=pos[1] if len(pos) > 1 else 0.0,
                rot=pos[2] if len(pos) > 2 else 0.0,
                size=size_vals[1] if len(size_vals) > 1 else 1.0,
                thickness=thickness,
                layer=_layer_of(node),
                hide=has_flag(node, "hide"),
                mirror=bool(effects) and has_flag(effects, "mirror"),
            ))

        # ---------------- pads ----------------
        elif kind == "pad":
            number = node[1] if len(node) > 1 else ""
            pad_type = node[2] if len(node) > 2 else "smd"
            shape = node[3] if len(node) > 3 else "rect"
            at = nums(child(node, "at")) or [0.0, 0.0]
            size = nums(child(node, "size")) or [1.0, 1.0]
            layers_node = child(node, "layers")
            layers = [l for l in (layers_node[1:] if layers_node else []) if isinstance(l, str)]

            drill = None
            dnode = child(node, "drill")
            if dnode is not None:
                dvals = nums(dnode)
                doff = _xy(child(dnode, "offset"))
                if "oval" in dnode and len(dvals) >= 2:
                    drill = Drill(True, dvals[0], dvals[1], doff[0], doff[1])
                elif dvals:
                    drill = Drill(False, dvals[0], dvals[0], doff[0], doff[1])

            delta = _xy(child(node, "rect_delta"))
            chamfer_node = child(node, "chamfer")
            fp.pads.append(Pad(
                number=number, pad_type=pad_type, shape=shape,
                x=at[0], y=at[1] if len(at) > 1 else 0.0,
                rot=at[2] if len(at) > 2 else 0.0,
                w=size[0], h=size[1] if len(size) > 1 else size[0],
                layers=layers, drill=drill,
                rratio=num(value(node, "roundrect_rratio", "0.25"), 0.25),
                delta=delta,
                primitives=_parse_primitives(node),
                anchor=value(child(node, "options") or [], "anchor", "rect"),
                chamfer_ratio=num(value(node, "chamfer_ratio", "0"), 0.0),
                chamfer=[c for c in (chamfer_node[1:] if chamfer_node else [])],
            ))

        # ---------------- 3D model ----------------
        elif kind == "model":
            path = node[1] if len(node) > 1 else ""
            off_node = child(node, "offset")
            if off_node is not None:
                offset = _xyz(off_node)
            else:
                # legacy (module ...) stores the model offset in inches
                legacy = _xyz(child(node, "at"))
                offset = tuple(v * 25.4 for v in legacy)
            fp.model = Model(path, offset,
                             _xyz(child(node, "scale"), (1.0, 1.0, 1.0)),
                             _xyz(child(node, "rotate")))

    # warn about things the 2D view cannot represent faithfully
    for pad in fp.pads:
        if pad.shape == "custom" and not pad.primitives:
            fp.warnings.append("Pad %s: custom shape without primitives, "
                               "drawn as its anchor shape." % pad.number)
    return fp


def parse_footprint_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    fp = parse_footprint(src, source_file=os.path.abspath(path))
    if fp.name in ("", "unnamed"):
        fp.name = os.path.splitext(os.path.basename(path))[0]
    return fp
