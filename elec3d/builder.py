# -*- coding: utf-8 -*-
"""Turn a parsed :class:`~elec3d.footprint.Footprint` into FreeCAD objects.

Coordinate convention
---------------------
KiCad works in millimetres with **Y pointing down**; FreeCAD's XY plane has
**Y pointing up**.  Every point therefore goes through :func:`vec`, which maps
``(x, y)`` to ``(x, -y)``.  A KiCad rotation angle stays a positive
(counter-clockwise) rotation about +Z after that flip, so pad and text angles
are applied unchanged.

Each layer is drawn at its own tiny Z offset (see :mod:`elec3d.layers`) so
coplanar faces don't z-fight in the 3D view.  The result is still a flat 2D
drawing when seen from the top.
"""

import math
import re

import FreeCAD as App
import Part

from . import layers as layermod
from .options import ImportOptions          # re-exported for existing callers

try:                                   # Draft is only needed for texts
    import Draft
    _HAS_DRAFT = True
except Exception:                      # pragma: no cover - depends on install
    _HAS_DRAFT = False

Vector = App.Vector
_SQ = math.sqrt(0.5)


# ---------------------------------------------------------------------------
# low level geometry helpers (all inputs in KiCad coordinates, Y down)
# ---------------------------------------------------------------------------
def vec(x, y, z=0.0):
    """KiCad point -> FreeCAD vector (Y flipped)."""
    return Vector(x, -y, z)


def _wire_from_path(path, z=0.0, close=True):
    """Build a wire from a small path description.

    ``path`` is a list of tuples in KiCad coordinates::

        ("M", (x, y))               move to (must be first)
        ("L", (x, y))               line to
        ("A", (mx, my), (x, y))     arc through mid to point
    """
    edges = []
    start = cur = path[0][1]
    for seg in path[1:]:
        if seg[0] == "L":
            nxt = seg[1]
            if abs(nxt[0] - cur[0]) > 1e-9 or abs(nxt[1] - cur[1]) > 1e-9:
                edges.append(Part.LineSegment(vec(cur[0], cur[1], z),
                                              vec(nxt[0], nxt[1], z)).toShape())
            cur = nxt
        elif seg[0] == "A":
            mid, nxt = seg[1], seg[2]
            try:
                edges.append(Part.Arc(vec(cur[0], cur[1], z),
                                      vec(mid[0], mid[1], z),
                                      vec(nxt[0], nxt[1], z)).toShape())
            except Part.OCCError:       # degenerate arc -> straight segment
                edges.append(Part.LineSegment(vec(cur[0], cur[1], z),
                                              vec(nxt[0], nxt[1], z)).toShape())
            cur = nxt
    if close and (abs(cur[0] - start[0]) > 1e-9 or abs(cur[1] - start[1]) > 1e-9):
        edges.append(Part.LineSegment(vec(cur[0], cur[1], z),
                                      vec(start[0], start[1], z)).toShape())
    return Part.Wire(edges)


def _corner_path(w, h, radii, chamfers=None):
    """Rounded / chamfered rectangle centred on the origin, KiCad coords.

    ``radii`` is a 4-tuple in the order TL, TR, BR, BL.
    ``chamfers`` is an optional 4-tuple of booleans in the same order.
    """
    chamfers = chamfers or (False, False, False, False)
    dx, dy = w / 2.0, h / 2.0
    rtl, rtr, rbr, rbl = [max(0.0, min(r, dx, dy)) for r in radii]

    path = [("M", (-dx + rtl, -dy))]
    # top edge -> top-right corner
    path.append(("L", (dx - rtr, -dy)))
    if rtr > 0:
        c = (dx - rtr, -dy + rtr)
        if chamfers[1]:
            path.append(("L", (dx, -dy + rtr)))
        else:
            path.append(("A", (c[0] + rtr * _SQ, c[1] - rtr * _SQ), (dx, -dy + rtr)))
    # right edge -> bottom-right corner
    path.append(("L", (dx, dy - rbr)))
    if rbr > 0:
        c = (dx - rbr, dy - rbr)
        if chamfers[2]:
            path.append(("L", (dx - rbr, dy)))
        else:
            path.append(("A", (c[0] + rbr * _SQ, c[1] + rbr * _SQ), (dx - rbr, dy)))
    # bottom edge -> bottom-left corner
    path.append(("L", (-dx + rbl, dy)))
    if rbl > 0:
        c = (-dx + rbl, dy - rbl)
        if chamfers[3]:
            path.append(("L", (-dx, dy - rbl)))
        else:
            path.append(("A", (c[0] - rbl * _SQ, c[1] + rbl * _SQ), (-dx, dy - rbl)))
    # left edge -> top-left corner
    path.append(("L", (-dx, -dy + rtl)))
    if rtl > 0:
        c = (-dx + rtl, -dy + rtl)
        if chamfers[0]:
            path.append(("L", (-dx + rtl, -dy)))
        else:
            path.append(("A", (c[0] - rtl * _SQ, c[1] - rtl * _SQ), (-dx + rtl, -dy)))
    return path


def _face_from_wire(wire):
    try:
        face = Part.Face(wire)
        if face.isValid() and face.Area > 1e-12:
            return face
    except Exception:
        pass
    return wire


def _circle_face(cx, cy, r, z=0.0):
    circ = Part.Circle(vec(cx, cy, z), Vector(0, 0, 1), r).toShape()
    return Part.Face(Part.Wire([circ]))


def stroke(shape, width, closed):
    """Render a centre-line as a filled band of the given width.

    Falls back to the bare wire when OCC refuses to offset (rare, but a broken
    silkscreen outline should never abort a whole import).
    """
    if width is None or width <= 1e-6:
        return shape
    try:
        if closed:
            outer = shape.makeOffset2D(width / 2.0, 0, True, False)
            inner = shape.makeOffset2D(-width / 2.0, 0, True, False)
            band = outer.fuse(inner).removeSplitter()
        else:
            band = shape.makeOffset2D(width / 2.0, 0, True, False)
        if band.Faces:
            return band
    except Exception:
        pass
    return shape


# ---------------------------------------------------------------------------
# pad shapes
# ---------------------------------------------------------------------------
def _pad_outline(pad):
    """Face for one pad, centred on the origin, already in FreeCAD orientation."""
    w, h = max(pad.w, 1e-6), max(pad.h, 1e-6)
    shape = pad.shape

    if shape == "circle" or (shape == "oval" and abs(w - h) < 1e-9):
        return _circle_face(0, 0, w / 2.0)

    if shape == "oval":
        r = min(w, h) / 2.0
        return _face_from_wire(_wire_from_path(_corner_path(w, h, (r, r, r, r))))

    if shape == "roundrect":
        r = pad.rratio * min(w, h)
        cham = pad.chamfer_ratio * min(w, h)
        flags = (("top_left" in pad.chamfer), ("top_right" in pad.chamfer),
                 ("bottom_right" in pad.chamfer), ("bottom_left" in pad.chamfer))
        radii = [cham if flags[i] and cham > 0 else r for i in range(4)]
        return _face_from_wire(_wire_from_path(_corner_path(w, h, radii, flags)))

    if shape == "trapezoid":
        dx, dy = w / 2.0, h / 2.0
        ddx, ddy = pad.delta[0] / 2.0, pad.delta[1] / 2.0
        pts = [(-dx - ddy, dy + ddx), (-dx + ddy, -dy - ddx),
               (dx - ddy, -dy + ddx), (dx + ddy, dy - ddx)]
        path = [("M", pts[0])] + [("L", p) for p in pts[1:]]
        return _face_from_wire(_wire_from_path(path))

    if shape == "custom":
        return _custom_pad(pad)

    # default: plain rectangle
    return _face_from_wire(_wire_from_path(_corner_path(w, h, (0, 0, 0, 0))))


def _custom_pad(pad):
    """Anchor shape fused with the pad primitives."""
    if pad.anchor == "circle":
        base = _circle_face(0, 0, max(pad.w, pad.h) / 2.0)
    else:
        base = _face_from_wire(_wire_from_path(
            _corner_path(max(pad.w, 1e-6), max(pad.h, 1e-6), (0, 0, 0, 0))))

    pieces = []
    for prim in pad.primitives:
        cls = prim.__class__.__name__
        try:
            if cls == "Poly":
                path = [("M", prim.points[0])] + [("L", p) for p in prim.points[1:]]
                wire = _wire_from_path(path)
                pieces.append(_face_from_wire(wire) if prim.filled
                              else stroke(wire, prim.width, True))
            elif cls == "Circle":
                if prim.filled:
                    pieces.append(_circle_face(prim.cx, prim.cy, prim.r))
                else:
                    circ = Part.Wire([Part.Circle(vec(prim.cx, prim.cy),
                                                 Vector(0, 0, 1), prim.r).toShape()])
                    pieces.append(stroke(circ, prim.width, True))
            elif cls == "Rect":
                path = [("M", (prim.x1, prim.y1)), ("L", (prim.x2, prim.y1)),
                        ("L", (prim.x2, prim.y2)), ("L", (prim.x1, prim.y2))]
                wire = _wire_from_path(path)
                pieces.append(_face_from_wire(wire) if prim.filled
                              else stroke(wire, prim.width, True))
            elif cls == "Line":
                wire = Part.Wire([Part.LineSegment(vec(prim.x1, prim.y1),
                                                  vec(prim.x2, prim.y2)).toShape()])
                pieces.append(stroke(wire, prim.width, False))
            elif cls == "Arc":
                arc = Part.Arc(vec(*prim.start), vec(*prim.mid), vec(*prim.end)).toShape()
                pieces.append(stroke(Part.Wire([arc]), prim.width, False))
        except Exception:
            continue

    result = base
    for piece in pieces:
        try:
            result = result.fuse(piece)
        except Exception:
            continue
    try:
        return result.removeSplitter()
    except Exception:
        return result


def pad_shape(pad, z=0.0):
    """Complete pad shape: outline, drill cut, rotation and placement."""
    shape = _pad_outline(pad)

    if pad.drill is not None:
        try:
            if pad.drill.oval and abs(pad.drill.w - pad.drill.h) > 1e-9:
                r = min(pad.drill.w, pad.drill.h) / 2.0
                hole = _face_from_wire(_wire_from_path(
                    _corner_path(pad.drill.w, pad.drill.h, (r, r, r, r))))
                hole.translate(vec(pad.drill.offx, pad.drill.offy))
            else:
                hole = _circle_face(pad.drill.offx, pad.drill.offy, pad.drill.w / 2.0)
            shape = shape.cut(hole)
        except Exception:
            pass

    if abs(pad.rot) > 1e-9:
        shape.rotate(Vector(0, 0, 0), Vector(0, 0, 1), pad.rot)
    shape.translate(vec(pad.x, pad.y, z))
    return shape


# ---------------------------------------------------------------------------
# graphics
# ---------------------------------------------------------------------------
def graphic_shapes(fp, layer, options):
    """Every graphic item of *fp* on *layer*, as a list of shapes."""
    z = layermod.layer_z(layer, options.layer_z_step)
    out = []

    def add(shape):
        if shape is not None:
            out.append(shape)

    for item in fp.lines:
        if item.layer != layer:
            continue
        wire = Part.Wire([Part.LineSegment(vec(item.x1, item.y1, z),
                                          vec(item.x2, item.y2, z)).toShape()])
        add(stroke(wire, item.width, False) if options.stroke_faces else wire)

    for item in fp.rects:
        if item.layer != layer:
            continue
        path = [("M", (item.x1, item.y1)), ("L", (item.x2, item.y1)),
                ("L", (item.x2, item.y2)), ("L", (item.x1, item.y2))]
        wire = _wire_from_path(path, z)
        if item.filled:
            add(_face_from_wire(wire))
        else:
            add(stroke(wire, item.width, True) if options.stroke_faces else wire)

    for item in fp.circles:
        if item.layer != layer or item.r <= 1e-9:
            continue
        wire = Part.Wire([Part.Circle(vec(item.cx, item.cy, z),
                                      Vector(0, 0, 1), item.r).toShape()])
        if item.filled:
            add(_face_from_wire(wire))
        else:
            add(stroke(wire, item.width, True) if options.stroke_faces else wire)

    for item in fp.arcs:
        if item.layer != layer:
            continue
        try:
            arc = Part.Arc(vec(item.start[0], item.start[1], z),
                           vec(item.mid[0], item.mid[1], z),
                           vec(item.end[0], item.end[1], z)).toShape()
        except Exception:
            arc = Part.LineSegment(vec(item.start[0], item.start[1], z),
                                   vec(item.end[0], item.end[1], z)).toShape()
        wire = Part.Wire([arc])
        add(stroke(wire, item.width, False) if options.stroke_faces else wire)

    for item in fp.polys:
        if item.layer != layer or len(item.points) < 3:
            continue
        path = [("M", item.points[0])] + [("L", p) for p in item.points[1:]]
        wire = _wire_from_path(path, z)
        if item.filled:
            add(_face_from_wire(wire))
        else:
            add(stroke(wire, item.width, True) if options.stroke_faces else wire)

    return out


# ---------------------------------------------------------------------------
# document construction
# ---------------------------------------------------------------------------
_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _safe_name(text, fallback="Obj"):
    name = _SAFE.sub("_", str(text)).strip("_")
    if not name or name[0].isdigit():
        name = fallback + "_" + name if name else fallback
    return name


def _has_gui():
    return App.GuiUp


def _style(obj, rgb, options, line_width=None, point_size=1.0):
    if not _has_gui():
        return
    try:
        vo = obj.ViewObject
        vo.ShapeColor = rgb
        vo.LineColor = rgb
        vo.PointColor = rgb
        vo.LineWidth = float(line_width or options.line_width_px)
        vo.PointSize = point_size
        vo.Transparency = int(options.transparency)
        vo.DisplayMode = "Flat Lines"
        vo.Lighting = "Two side"
    except Exception:
        pass


def _add_property(obj, prop_type, name, group, doc, value):
    try:
        if not hasattr(obj, name):
            obj.addProperty(prop_type, name, group, doc)
        setattr(obj, name, value)
    except Exception:
        pass


def build_footprint(fp, doc=None, options=None):
    """Create the FreeCAD objects for *fp* and return the top level group."""
    options = options or ImportOptions()
    if doc is None:
        doc = App.ActiveDocument or App.newDocument("Elec3D")

    # App::Part is still a group/container, but unlike DocumentObjectGroup it has
    # a Placement.  Interactive 2D dragging therefore moves the complete
    # footprint (graphics, pads and texts) with one lightweight transform.
    root = doc.addObject("App::Part", _safe_name(fp.name, "Footprint"))
    root.Label = fp.name
    _add_property(root, "App::PropertyString", "FootprintName",
                  "KiCad", "Footprint name as stored in the file", fp.name)
    _add_property(root, "App::PropertyFile", "SourceFile",
                  "KiCad", "File this footprint was imported from", fp.source_file)
    _add_property(root, "App::PropertyString", "Description",
                  "KiCad", "descr field", fp.descr)
    _add_property(root, "App::PropertyString", "Tags", "KiCad", "tags field", fp.tags)
    _add_property(root, "App::PropertyString", "Attributes",
                  "KiCad", "attr field (smd / through_hole / ...)", fp.attr)

    # Metadata used by interaction2d.py.  Keep it on the root container so a
    # child picked in the viewer can be mapped back to one movable footprint.
    bbox = fp.bbox()
    _add_property(root, "App::PropertyBool", "KiCadInteractive2D",
                  "KiCad", "Footprint participates in the interactive 2D editor", True)
    _add_property(root, "App::PropertyVector", "KiCadBBoxMin",
                  "KiCad", "Local 2D selection bounds",
                  Vector(bbox[0], -bbox[3], 0.0))
    _add_property(root, "App::PropertyVector", "KiCadBBoxMax",
                  "KiCad", "Local 2D selection bounds",
                  Vector(bbox[2], -bbox[1], 0.0))
    try:
        root.setEditorMode("KiCadInteractive2D", 2)
        root.setEditorMode("KiCadBBoxMin", 2)
        root.setEditorMode("KiCadBBoxMax", 2)
    except Exception:
        pass
    if fp.model is not None:
        _add_property(root, "App::PropertyString", "Model3D",
                      "KiCad", "Path of the associated 3D model", fp.model.path)

    groups = {}

    def group_for(label):
        if not options.group_by_layer:
            return root
        if label not in groups:
            grp = doc.addObject("App::DocumentObjectGroup", _safe_name(label, "Layer"))
            grp.Label = label
            root.addObject(grp)
            groups[label] = grp
        return groups[label]

    # ---- graphics, one compound per layer -------------------------------
    for layer in fp.layers_used():
        shapes = graphic_shapes(fp, layer, options)
        if not shapes:
            continue
        obj = doc.addObject("Part::Feature", _safe_name(layer + "_graphics"))
        obj.Label = "%s graphics" % layer
        obj.Shape = Part.Compound(shapes)
        _add_property(obj, "App::PropertyString", "KiCadLayer",
                      "KiCad", "Source layer", layer)
        _style(obj, layermod.layer_color(layer, options.theme), options)
        group_for(layer).addObject(obj)

    # ---- pads -----------------------------------------------------------
    if fp.pads:
        pad_group = doc.addObject("App::DocumentObjectGroup", "Pads")
        pad_group.Label = "Pads"
        root.addObject(pad_group)
        for pad in fp.pads:
            z = layermod.layer_z(pad.copper_layer, options.layer_z_step)
            try:
                shape = pad_shape(pad, z)
            except Exception as exc:                       # keep importing
                fp.warnings.append("Pad %s could not be built: %s" % (pad.number, exc))
                continue
            obj = doc.addObject("Part::Feature", _safe_name("Pad_%s" % pad.number))
            obj.Label = "Pad %s" % pad.number
            obj.Shape = shape
            _add_property(obj, "App::PropertyString", "PadNumber",
                          "KiCad", "Pad number / name", str(pad.number))
            _add_property(obj, "App::PropertyString", "PadType",
                          "KiCad", "smd / thru_hole / np_thru_hole / connect", pad.pad_type)
            _add_property(obj, "App::PropertyString", "PadShape",
                          "KiCad", "Pad shape", pad.shape)
            _add_property(obj, "App::PropertyString", "PadLayers",
                          "KiCad", "Layers this pad lives on", " ".join(pad.layers))
            _add_property(obj, "App::PropertyVector", "PadPosition",
                          "KiCad", "Position in FreeCAD coordinates",
                          Vector(pad.x, -pad.y, 0))
            _add_property(obj, "App::PropertyFloat", "PadRotation",
                          "KiCad", "Rotation in degrees", float(pad.rot))
            if pad.drill is not None:
                _add_property(obj, "App::PropertyString", "Drill", "KiCad",
                              "Drill size in mm",
                              ("oval %.3f x %.3f" % (pad.drill.w, pad.drill.h))
                              if pad.drill.oval else "%.3f" % pad.drill.w)
            _style(obj, layermod.pad_color(pad, options.theme), options)
            pad_group.addObject(obj)

    # ---- texts ----------------------------------------------------------
    if options.import_texts and fp.texts and _HAS_DRAFT:
        text_group = doc.addObject("App::DocumentObjectGroup", "Texts")
        text_group.Label = "Texts"
        root.addObject(text_group)
        for txt in fp.texts:
            obj = _make_text(doc, txt, options)
            if obj is None:
                continue
            text_group.addObject(obj)
            if txt.hide and not options.show_hidden_texts:
                obj.Visibility = False

    doc.recompute()
    return root


def _make_text(doc, txt, options):
    """Draft text placed and rotated like the KiCad label."""
    try:
        z = layermod.layer_z(txt.layer, options.layer_z_step) + 0.001
        placement = App.Placement(
            vec(txt.x, txt.y, z),
            App.Rotation(Vector(0, 0, 1), txt.rot))
        obj = Draft.make_text(str(txt.text) if txt.text else " ", placement=placement)
        obj.Label = "%s: %s" % (txt.kind, txt.text)
        _add_property(obj, "App::PropertyString", "KiCadLayer",
                      "KiCad", "Source layer", txt.layer)
        _add_property(obj, "App::PropertyString", "KiCadTextType",
                      "KiCad", "reference / value / user", txt.kind)
        if _has_gui():
            vo = obj.ViewObject
            vo.FontSize = max(txt.size, 0.1)
            vo.TextColor = layermod.layer_color(txt.layer, options.theme)
            try:
                vo.Justification = "Center"
                vo.LineSpacing = 1.0
            except Exception:
                pass
        return obj
    except Exception:
        return None


# ---------------------------------------------------------------------------
# top level convenience
# ---------------------------------------------------------------------------
def import_footprint(path, doc=None, options=None):
    """Parse *path*, build it in *doc*, and return ``(container, footprint)``.

    The parsed model is handed back so callers can feed the 2D viewer without
    reading and parsing the file a second time.
    """
    from .footprint import parse_footprint_file

    fp = parse_footprint_file(path)
    root = build_footprint(fp, doc, options)
    App.Console.PrintMessage("KiCad footprint imported: %s\n" % fp.summary())
    for warn in fp.warnings:
        App.Console.PrintWarning("KiCad footprint: %s\n" % warn)
    return root, fp


def import_file(path, doc=None, options=None):
    """Parse *path* and build it in *doc*.  Returns the created container."""
    return import_footprint(path, doc, options)[0]
