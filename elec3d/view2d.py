# -*- coding: utf-8 -*-
"""The 2D footprint canvas.

A ``QGraphicsView`` that draws a parsed :class:`~elec3d.footprint.Footprint`
the way the KiCad footprint editor does.  It is deliberately independent of the
Part geometry built by :mod:`elec3d.builder`: nothing here needs OCC, so the
canvas stays fast and can be previewed outside FreeCAD
(``tools/preview_ui.py``).

Coordinates
-----------
KiCad and Qt both put +Y downwards, so scene units are *exactly* footprint
millimetres with no flip.  The flip to FreeCAD's Y-up convention happens only
during the explicit **Create FreeCAD** conversion.
"""

import math

from . import layers as layermod
from . import theme as thememod
from .routing import via_spans_layer
from .options import (DEFAULT_SMALL_CROSSHAIR_PX, MAX_SMALL_CROSSHAIR_PX,
                      MIN_SMALL_CROSSHAIR_PX, normalize_crosshair_size)
from .qt import Qt, QtCore, QtGui, QtWidgets, Signal

MIN_PEN = 0.02              # mm, so zero-width strokes stay visible
FIT_MARGIN = 0.12           # fraction of the bounding box
ZOOM_MIN, ZOOM_MAX = 2.0, 4000.0     # pixels per mm
SELECTION_PAD_PX = 5.0      # selection box margin stays visually constant
SELECTION_BOUND_PAD = SELECTION_PAD_PX / ZOOM_MIN + 0.5
PAN_WORKSPACE_HALF_SPAN = 10000.0  # mm; persistent free-pan area around the origin
PAD_LABEL = {"dark": "#FFFFFF", "light": "#FFFFFF"}   # pad numbers, both themes
BOOL_SCALE = 1000.0         # see _scaled_op()


# ---------------------------------------------------------------------------
# GPU-backed QGraphicsView viewport
# ---------------------------------------------------------------------------
# Qt can render a QGraphicsScene into a plain QOpenGLWidget.  This keeps the
# mature scene/item interaction code (selection, drag, rubber-band, outline
# handles, hit testing and undo/redo) while moving the actual 2D rasterisation
# and compositing onto the GPU.  This is deliberately *not* Pivy/Quarter; it is
# the same direct Qt OpenGL boundary used by the document-free 3D renderer.
#
# Scene traversal and edit hit-testing remain on the CPU, as they should, but
# pan/zoom/repaint no longer asks the CPU-backed QWidget paint engine to fill
# every path and grid line into the window surface.  If Qt cannot create a GL
# context we replace the viewport with a normal QWidget automatically.
_GPU2D_VIEWPORT_CLASS = None
try:
    import importlib

    for _root in ("PySide", "PySide6", "PySide2"):
        try:
            _mod = importlib.import_module(_root + ".QtOpenGLWidgets")
        except Exception:
            _mod = None
        candidate = getattr(_mod, "QOpenGLWidget", None) if _mod else None
        if candidate is None:
            candidate = getattr(QtWidgets, "QOpenGLWidget", None)
        if candidate is not None:
            _GPU2D_VIEWPORT_CLASS = candidate
            break
except Exception:
    _GPU2D_VIEWPORT_CLASS = None


if _GPU2D_VIEWPORT_CLASS is not None:
    class _Gpu2DViewport(_GPU2D_VIEWPORT_CLASS):
        """OpenGL paint device used as the QGraphicsView viewport.

        QGraphicsView owns the paint event; this widget merely supplies the
        framebuffer-backed paint device.  Keeping the class intentionally tiny
        avoids the custom native-event stack that caused the old Quarter crash.
        """

        def __init__(self, parent=None):
            super(_Gpu2DViewport, self).__init__(parent)
            self.setObjectName("elec3dGpu2DViewport")
            try:
                # Let Qt choose the compatible core/compatibility profile used
                # by FreeCAD, but request modest MSAA for footprint edges.
                fmt = QtGui.QSurfaceFormat(self.format())
                if fmt.samples() <= 0:
                    fmt.setSamples(4)
                self.setFormat(fmt)
            except Exception:
                pass
else:
    _Gpu2DViewport = None


# ---------------------------------------------------------------------------
# painter paths from footprint geometry (all in KiCad mm, Y down)
# ---------------------------------------------------------------------------
def _rounded_rect_path(w, h, r, chamfers=(False, False, False, False)):
    """Rounded / chamfered rectangle centred on the origin.

    Corner order is TL, TR, BR, BL, matching KiCad's ``chamfer`` keywords.
    """
    path = QtGui.QPainterPath()
    dx, dy = w / 2.0, h / 2.0
    r = max(0.0, min(r, dx, dy))
    if r <= 1e-9:
        path.addRect(-dx, -dy, w, h)
        return path

    tl, tr, br, bl = chamfers
    d = 2 * r
    path.moveTo(-dx + r, -dy)
    path.lineTo(dx - r, -dy)
    if tr:
        path.lineTo(dx, -dy + r)
    else:
        path.arcTo(dx - d, -dy, d, d, 90, -90)
    path.lineTo(dx, dy - r)
    if br:
        path.lineTo(dx - r, dy)
    else:
        path.arcTo(dx - d, dy - d, d, d, 0, -90)
    path.lineTo(-dx + r, dy)
    if bl:
        path.lineTo(-dx, dy - r)
    else:
        path.arcTo(-dx, dy - d, d, d, 270, -90)
    path.lineTo(-dx, -dy + r)
    if tl:
        path.lineTo(-dx + r, -dy)
    else:
        path.arcTo(-dx, -dy, d, d, 180, -90)
    path.closeSubpath()
    return path


def _scaled_op(a, b, op):
    """Boolean path op done in micrometres instead of millimetres.

    Qt flattens curves before united()/subtracted() with a tolerance expressed
    in path units.  With millimetre units a 0.9mm drill comes back as a visible
    octagon, so the operands are scaled up first and the result scaled back.
    """
    up = QtGui.QTransform()
    up.scale(BOOL_SCALE, BOOL_SCALE)
    down = QtGui.QTransform()
    down.scale(1.0 / BOOL_SCALE, 1.0 / BOOL_SCALE)
    return down.map(getattr(up.map(a), op)(up.map(b)))


def _punch(path, hole):
    """Remove *hole* from *path*, keeping the outline's exact curves.

    A drill always sits inside its pad, and an odd-even sub-path renders that
    as a hole without any boolean flattening at all.  The scaled boolean is
    only needed for the pathological case of a drill hanging over the edge.
    """
    if path.contains(hole):
        combined = QtGui.QPainterPath(path)
        combined.addPath(hole)
        combined.setFillRule(Qt.OddEvenFill)
        return combined
    return _scaled_op(path, hole, "subtracted")


def _polygon_path(points):
    path = QtGui.QPainterPath()
    path.moveTo(points[0][0], points[0][1])
    for x, y in points[1:]:
        path.lineTo(x, y)
    path.closeSubpath()
    return path


def _arc_path(start, mid, end):
    """Arc through three points, as a QPainterPath."""
    (ax, ay), (bx, by), (cx, cy) = start, mid, end
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    path = QtGui.QPainterPath()
    path.moveTo(ax, ay)
    if abs(d) < 1e-12:                       # collinear -> straight segment
        path.lineTo(cx, cy)
        return path

    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) +
          (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) +
          (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)

    # Qt angles are counter-clockwise on a Y-up axis; the scene is Y-down, so
    # the sign of every angle flips.
    a0 = -math.degrees(math.atan2(ay - uy, ax - ux))
    a1 = -math.degrees(math.atan2(cy - uy, cx - ux))
    am = -math.degrees(math.atan2(by - uy, bx - ux))

    def norm(a):
        return (a + 360.0) % 360.0

    sweep = norm(a1 - a0)
    if not _angle_between(norm(a0), norm(am), sweep):
        sweep -= 360.0
    path.arcTo(ux - r, uy - r, 2 * r, 2 * r, a0, sweep)
    return path


def _angle_between(a0, am, sweep):
    """Is *am* inside the CCW sweep starting at *a0*?"""
    return ((am - a0) + 360.0) % 360.0 <= sweep + 1e-9


def _text_path(text, height_mm, cx, cy, rotation=0.0, mirror=False):
    """Centred text as a path, sized by cap height in millimetres.

    KiCad sizes text by cap height, so scaling by the glyph bounding box would
    make "ic" bigger than "IC**".  Font metrics keep every string consistent.
    """
    font = QtGui.QFont("DejaVu Sans")
    font.setPointSizeF(100.0)
    font.setBold(False)

    probe = QtGui.QPainterPath()
    probe.addText(0, 0, font, text or " ")
    rect = probe.boundingRect()
    if rect.height() <= 0 or rect.width() <= 0:
        return QtGui.QPainterPath()

    metrics = QtGui.QFontMetricsF(font)
    cap = metrics.capHeight() if hasattr(metrics, "capHeight") else 0.0
    scale = height_mm / (cap if cap > 0 else rect.height())
    xform = QtGui.QTransform()
    xform.translate(cx, cy)
    xform.rotate(-rotation)                  # KiCad angles are CCW on screen
    if mirror:
        xform.scale(-scale, scale)
    else:
        xform.scale(scale, scale)
    xform.translate(-rect.center().x(), -rect.center().y())
    return xform.map(probe)


def pad_path(pad):
    """Pad outline (with its drill removed) centred on the pad origin."""
    w, h = max(pad.w, 1e-6), max(pad.h, 1e-6)
    shape = pad.shape

    if shape == "circle" or (shape == "oval" and abs(w - h) < 1e-9):
        path = QtGui.QPainterPath()
        path.addEllipse(QtCore.QPointF(0, 0), w / 2.0, h / 2.0)
    elif shape == "oval":
        path = _rounded_rect_path(w, h, min(w, h) / 2.0)
    elif shape == "roundrect":
        cham = pad.chamfer_ratio * min(w, h)
        flags = ("top_left" in pad.chamfer, "top_right" in pad.chamfer,
                 "bottom_right" in pad.chamfer, "bottom_left" in pad.chamfer)
        radius = cham if (any(flags) and cham > 0) else pad.rratio * min(w, h)
        path = _rounded_rect_path(w, h, radius, flags)
    elif shape == "trapezoid":
        dx, dy = w / 2.0, h / 2.0
        ddx, ddy = pad.delta[0] / 2.0, pad.delta[1] / 2.0
        path = _polygon_path([(-dx - ddy, dy + ddx), (-dx + ddy, -dy - ddx),
                              (dx - ddy, -dy + ddx), (dx + ddy, dy - ddx)])
    elif shape == "custom":
        path = _custom_pad_path(pad)
    else:
        path = _rounded_rect_path(w, h, 0.0)

    if pad.drill is not None:
        hole = QtGui.QPainterPath()
        ox, oy = pad.drill.offx, pad.drill.offy
        if pad.drill.oval and abs(pad.drill.w - pad.drill.h) > 1e-9:
            hole = _rounded_rect_path(pad.drill.w, pad.drill.h,
                                      min(pad.drill.w, pad.drill.h) / 2.0)
            hole.translate(ox, oy)
        else:
            hole.addEllipse(QtCore.QPointF(ox, oy),
                            pad.drill.w / 2.0, pad.drill.w / 2.0)
        path = _punch(path, hole)
    return path


def _custom_pad_path(pad):
    if pad.anchor == "circle":
        path = QtGui.QPainterPath()
        r = max(pad.w, pad.h) / 2.0
        path.addEllipse(QtCore.QPointF(0, 0), r, r)
    else:
        path = _rounded_rect_path(max(pad.w, 1e-6), max(pad.h, 1e-6), 0.0)

    for prim in pad.primitives:
        kind = prim.__class__.__name__
        try:
            if kind == "Poly" and len(prim.points) >= 3:
                piece = _polygon_path(prim.points)
            elif kind == "Circle":
                piece = QtGui.QPainterPath()
                piece.addEllipse(QtCore.QPointF(prim.cx, prim.cy), prim.r, prim.r)
            elif kind == "Rect":
                piece = QtGui.QPainterPath()
                piece.addRect(QtCore.QRectF(QtCore.QPointF(prim.x1, prim.y1),
                                            QtCore.QPointF(prim.x2, prim.y2)))
            elif kind == "Line":
                stroke = QtGui.QPainterPathStroker()
                stroke.setWidth(max(prim.width, MIN_PEN))
                stroke.setCapStyle(Qt.RoundCap)
                seg = QtGui.QPainterPath(QtCore.QPointF(prim.x1, prim.y1))
                seg.lineTo(prim.x2, prim.y2)
                piece = stroke.createStroke(seg)
            elif kind == "Arc":
                stroke = QtGui.QPainterPathStroker()
                stroke.setWidth(max(prim.width, MIN_PEN))
                stroke.setCapStyle(Qt.RoundCap)
                piece = stroke.createStroke(_arc_path(prim.start, prim.mid, prim.end))
            else:
                continue
            path = _scaled_op(path, piece, "united")
        except Exception:
            continue
    return path


# ---------------------------------------------------------------------------
# board-solid cross-section scene items (read-only)
# ---------------------------------------------------------------------------
def _loops_path(loops):
    path = QtGui.QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    for loop in loops or ():
        if len(loop) < 3:
            continue
        path.moveTo(QtCore.QPointF(float(loop[0][0]), float(loop[0][1])))
        for x, y in loop[1:]:
            path.lineTo(QtCore.QPointF(float(x), float(y)))
        path.closeSubpath()
    return path


class BoardEdgeItem(QtWidgets.QGraphicsPathItem):
    """Board boundary of one copper layer, derived from the board solid.

    The boundary is the cross-section of the imported solid at the layer's
    reference-plane height.  It is reference geometry only: it is never
    selectable, never movable and has an empty hit shape so it can not steal
    clicks from footprints, pads or routed copper underneath.
    """

    def __init__(self, layer_id, loops, color, parent=None):
        super(BoardEdgeItem, self).__init__(parent)
        self.layer_id = layer_id
        self.loops = [list(loop) for loop in (loops or [])]
        self.setPath(_loops_path(self.loops))
        self.setBrush(QtGui.QBrush(Qt.NoBrush))
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(False)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, False)
        self.setData(0, {"kind": "board_edge", "layer_id": layer_id})
        self.setZValue(26.0)
        self.set_style(color, active=True)

    def set_style(self, color, active=True):
        pen = QtGui.QPen(QtGui.QColor(color))
        pen.setCosmetic(True)
        pen.setWidthF(2.0 if active else 1.0)
        pen.setJoinStyle(Qt.RoundJoin)
        if not active:
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        self.setOpacity(1.0 if active else 0.38)

    def shape(self):
        return QtGui.QPainterPath()


class BoardMaskItem(QtWidgets.QGraphicsPathItem):
    """Soft shade over everything *outside* the active layer's board section.

    It makes the routable area obvious at a glance.  Like the edge it has an
    empty hit shape, so it never interferes with picking or rubber-banding.
    """

    def __init__(self, parent=None):
        super(BoardMaskItem, self).__init__(parent)
        self.setPen(QtGui.QPen(Qt.NoPen))
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(False)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, False)
        self.setData(0, {"kind": "board_mask"})
        self.setZValue(-20.0)

    def set_loops(self, loops, color, blocked=False):
        span = PAN_WORKSPACE_HALF_SPAN
        path = QtGui.QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        path.addRect(QtCore.QRectF(-span, -span, 2 * span, 2 * span))
        if not blocked:
            path.addPath(_loops_path(loops))
        self.setPath(path)
        self.setBrush(QtGui.QBrush(QtGui.QColor(color)))

    def shape(self):
        return QtGui.QPainterPath()


# ---------------------------------------------------------------------------
# routed copper scene items
# ---------------------------------------------------------------------------
class TrackGraphicsItem(QtWidgets.QGraphicsPathItem):
    """Selectable saved routed track, with segment-level editor selection.

    A Track model may contain several straight segments.  The graphics item
    remains one QGraphicsItem for efficient drawing/hit-testing, but remembers
    which segment the user clicked so selection and direct dragging can operate
    at KiCad-like segment granularity.  ``U`` can promote that to the complete
    connected copper island without changing the underlying board model.
    """

    def __init__(self, track, color="#4D7FC4"):
        super(TrackGraphicsItem, self).__init__()
        self.track_model = track
        self.base_color = str(color)
        self.base_opacity = 1.0
        self._highlighted_nets = set()
        self._net_highlight_enabled = False
        self.selected_segment_index = None
        self.select_whole_track = False
        # Copper uses custom editor-owned dragging; native scene translation stays off.
        # QGraphicsScene can never translate routed copper behind our model.
        self.setFlags(QtWidgets.QGraphicsItem.ItemIsSelectable |
                      QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(28.0)
        self.setData(0, {"kind": "track", "track": track,
                         "detail": "track %.3f mm" % float(track.width)})
        self.update_geometry()

    def update_geometry(self):
        path = QtGui.QPainterPath()
        pts = list(self.track_model.points or [])
        if pts:
            path.moveTo(float(pts[0][0]), float(pts[0][1]))
            for x, y in pts[1:]:
                path.lineTo(float(x), float(y))
        self.setPath(path)
        # Geometry edits can reduce the segment count.  Clamp stale selection.
        if self.selected_segment_index is not None:
            count = max(0, len(pts) - 1)
            if not (0 <= int(self.selected_segment_index) < count):
                self.selected_segment_index = None
        self.update_style()

    def segment_index_at(self, point):
        """Return the straight segment nearest *point* in scene coordinates."""
        pts = list(self.track_model.points or [])
        if len(pts) < 2:
            return -1
        px = float(point.x()) if hasattr(point, "x") else float(point[0])
        py = float(point.y()) if hasattr(point, "y") else float(point[1])
        best = None
        for i in range(len(pts) - 1):
            ax, ay = pts[i]; bx, by = pts[i + 1]
            vx, vy = float(bx) - float(ax), float(by) - float(ay)
            l2 = vx * vx + vy * vy
            t = (((px - float(ax)) * vx + (py - float(ay)) * vy) / l2) if l2 else 0.0
            t = max(0.0, min(1.0, t))
            qx, qy = float(ax) + t * vx, float(ay) + t * vy
            d2 = (px - qx) ** 2 + (py - qy) ** 2
            if best is None or d2 < best[0]:
                best = (d2, i)
        return best[1] if best is not None else -1

    def set_selected_segment(self, index=None, whole=False):
        self.select_whole_track = bool(whole)
        self.selected_segment_index = None if index is None else int(index)
        self.update()

    def selected_path(self):
        """Painter path for the selected segment, or the complete track for U."""
        if self.select_whole_track or self.selected_segment_index is None:
            return self.path()
        pts = list(self.track_model.points or [])
        i = int(self.selected_segment_index)
        if i < 0 or i + 1 >= len(pts):
            return self.path()
        path = QtGui.QPainterPath(QtCore.QPointF(float(pts[i][0]), float(pts[i][1])))
        path.lineTo(float(pts[i + 1][0]), float(pts[i + 1][1]))
        return path

    def update_style(self, color=None, opacity=None):
        if color is not None:
            self.base_color = str(color)
        if opacity is not None:
            self.base_opacity = float(opacity)
        net = getattr(self.track_model, "net", None)
        enabled = bool(self._net_highlight_enabled and self._highlighted_nets)
        match = bool(enabled and net in self._highlighted_nets)
        selected = self.isSelected()
        color_value = QtGui.QColor(self.base_color)
        if match:
            color_value = color_value.lighter(145)
        pen = QtGui.QPen(color_value)
        pen.setWidthF(max(MIN_PEN, float(self.track_model.width)))
        pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
        self.setPen(pen)
        self.setBrush(QtGui.QBrush(Qt.NoBrush))
        self.setOpacity(1.0 if selected or match else (0.14 if enabled else self.base_opacity))
        info = self.data(0) or {}
        info["net"] = net
        info["detail"] = "track %.3f mm" % float(self.track_model.width)
        self.setData(0, info)
        self.update()

    def set_net_highlight(self, nets=None, enabled=False):
        self._highlighted_nets = set(str(n) for n in (nets or []) if n not in (None, ""))
        self._net_highlight_enabled = bool(enabled and self._highlighted_nets)
        self.update_style()

    def shape(self):
        """Use the actual copper stroke as the selectable track geometry."""
        path = self.path()
        if path.isEmpty():
            return path
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(max(1e-6, float(self.track_model.width)))
        stroker.setCapStyle(Qt.RoundCap)
        stroker.setJoinStyle(Qt.RoundJoin)
        return stroker.createStroke(path)

    def paint(self, painter, option, widget=None):
        """Highlight the selected segment without replacing its layer colour.

        Selection used to paint the segment with the application's generic blue
        accent.  That made a selected In*.Cu/F.Cu track look as if it belonged
        to B.Cu.  Keep the owning copper layer's hue and use a brighter shade
        only as the selection emphasis; Qt's default bounding box remains
        suppressed.
        """
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawPath(self.path())
        if self.isSelected():
            selected_color = QtGui.QColor(self.base_color).lighter(140)
            pen = QtGui.QPen(selected_color)
            pen.setWidthF(max(MIN_PEN, float(self.track_model.width)))
            pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(Qt.NoBrush))
            painter.drawPath(self.selected_path())

    def itemChange(self, change, value):
        selected_change = getattr(QtWidgets.QGraphicsItem, "ItemSelectedHasChanged", None)
        if selected_change is None and hasattr(QtWidgets.QGraphicsItem, "GraphicsItemChange"):
            selected_change = QtWidgets.QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged
        result = super(TrackGraphicsItem, self).itemChange(change, value)
        if change == selected_change:
            if not bool(value):
                self.select_whole_track = False
                self.selected_segment_index = None
            self.update_style()
        return result


class ViaGraphicsItem(QtWidgets.QGraphicsPathItem):
    """Selectable via annulus; a zero hole is deliberately a solid disk."""

    def __init__(self, via, color="#4D7FC4"):
        super(ViaGraphicsItem, self).__init__()
        self.via_model = via
        self.base_color = str(color)
        self.base_opacity = 1.0
        self._highlighted_nets = set()
        self._net_highlight_enabled = False
        # Vias also move only through the explicit M-key interactive move tool.
        self.setFlags(QtWidgets.QGraphicsItem.ItemIsSelectable |
                      QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(29.0)
        self.setData(0, {"kind": "via", "via": via,
                         "detail": "via %.3f / %.3f mm" %
                                   (float(via.diameter), float(via.hole_diameter))})
        self.update_geometry()

    def update_geometry(self):
        v = self.via_model
        r = max(0.0005, float(v.diameter) / 2.0)
        path = QtGui.QPainterPath()
        path.addEllipse(QtCore.QPointF(float(v.x), float(v.y)), r, r)
        hole = max(0.0, min(float(v.hole_diameter), float(v.diameter)))
        if hole > 1e-9:
            inner = QtGui.QPainterPath()
            inner.addEllipse(QtCore.QPointF(float(v.x), float(v.y)), hole / 2.0, hole / 2.0)
            path.addPath(inner)
            path.setFillRule(Qt.OddEvenFill)
        self.setPath(path)
        self.update_style()

    def shape(self):
        # KiCad lets the via be picked by its drill opening too.  Keep the
        # visual path annular, but use the complete outer disk for hit testing.
        v = self.via_model
        r = max(0.0005, float(v.diameter) / 2.0)
        path = QtGui.QPainterPath()
        path.addEllipse(QtCore.QPointF(float(v.x), float(v.y)), r, r)
        return path

    def update_style(self, color=None, opacity=None):
        if color is not None:
            self.base_color = str(color)
        if opacity is not None:
            self.base_opacity = float(opacity)
        net = getattr(self.via_model, "net", None)
        enabled = bool(self._net_highlight_enabled and self._highlighted_nets)
        match = bool(enabled and net in self._highlighted_nets)
        selected = self.isSelected()
        color_value = QtGui.QColor(self.base_color)
        if match:
            color_value = color_value.lighter(145)
        if selected:
            # Selection keeps the via's owning/reference copper-layer hue.
            color_value = QtGui.QColor(self.base_color).lighter(140)
        pen = QtGui.QPen(color_value); pen.setCosmetic(True); pen.setWidth(1)
        self.setPen(pen)
        self.setBrush(QtGui.QBrush(color_value))
        self.setOpacity(1.0 if selected or match else (0.14 if enabled else self.base_opacity))
        info = self.data(0) or {}
        info["net"] = net
        info["detail"] = "via %.3f / %.3f mm" % (
            float(self.via_model.diameter), float(self.via_model.hole_diameter))
        self.setData(0, info)

    def set_net_highlight(self, nets=None, enabled=False):
        self._highlighted_nets = set(str(n) for n in (nets or []) if n not in (None, ""))
        self._net_highlight_enabled = bool(enabled and self._highlighted_nets)
        self.update_style()

    def paint(self, painter, option, widget=None):
        """Draw selection through copper colour only, without Qt's bbox frame."""
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawPath(self.path())

    def itemChange(self, change, value):
        selected_change = getattr(QtWidgets.QGraphicsItem, "ItemSelectedHasChanged", None)
        if selected_change is None and hasattr(QtWidgets.QGraphicsItem, "GraphicsItemChange"):
            selected_change = QtWidgets.QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged
        result = super(ViaGraphicsItem, self).itemChange(change, value)
        if change == selected_change:
            self.update_style()
        return result


# ---------------------------------------------------------------------------
# scene items
# ---------------------------------------------------------------------------
class FootprintItem(QtWidgets.QGraphicsItemGroup):
    """One footprint: a movable group of per-layer child items."""

    def __init__(self, fp, reference_override=""):
        super(FootprintItem, self).__init__()
        self.fp = fp
        self.reference_override = str(reference_override or "")
        self.layer_items = {}                 # layer name -> [items]
        self.pad_items = {}                   # pad number -> item
        self.pad_labels = []
        self.text_items = []
        self._theme = "dark"
        self._local_rect = QtCore.QRectF()
        self._shape_cache = None
        # Visibility belongs to the footprint item, not to the sidebar widgets.
        # Sidebar rows are rebuilt whenever selection/theme changes, so keeping
        # state here makes those checkboxes reflect the actual scene.
        self.layer_visibility = {}
        self._hidden_texts_visible = False
        # Design-layer assignment.  ``design_layer_id`` is owned by the panel's
        # LayerStack; the item only caches what it needs in order to paint:
        # the plane's colour, which replaces the footprint's own copper colour.
        self.design_layer_id = None
        self._copper_tint = None
        # Number of the pad currently highlighted on this footprint, if any.
        # Pads outrank footprints in the selection, so while one is highlighted
        # this item draws the pad's outline instead of its own.
        self.selected_pad = None
        self._highlighted_nets = set()
        self._net_highlight_enabled = False

        self.setFlags(QtWidgets.QGraphicsItem.ItemIsMovable |
                      QtWidgets.QGraphicsItem.ItemIsSelectable |
                      QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        # Footprint placement/rotation is defined around the KiCad footprint
        # origin, which is scene-local (0, 0), not around the bounding-box
        # centre.  R / Shift+R therefore behave like PCB-editor rotation.
        self.setTransformOriginPoint(QtCore.QPointF(0.0, 0.0))
        self.setHandlesChildEvents(False)
        self.build()

    # -- construction ----------------------------------------------------
    def build(self):
        for item in list(self.childItems()):
            self.removeFromGroup(item)
            if item.scene():
                item.scene().removeItem(item)
        self.layer_items = {}
        self.pad_items = {}
        self.pad_labels = []
        self.text_items = []
        self._shape_cache = None
        previous_visibility = dict(self.layer_visibility)
        self.layer_visibility = dict(
            (layer, previous_visibility.get(layer, False if layer == "F.Fab" else True))
            for layer in self.fp.layers_used())

        for layer in self.fp.layers_used():
            for item in self._graphics_for(layer):
                self._register(layer, item)

        for pad in self.fp.pads:
            self._add_pad(pad)

        for txt in self.fp.texts:
            self._add_text(txt)

        minx, miny, maxx, maxy = self.fp.bbox()
        self._local_rect = QtCore.QRectF(minx, miny, maxx - minx, maxy - miny)

        # build() may be called again; restore each layer's saved state after
        # recreating its child graphics.
        for layer, visible in self.layer_visibility.items():
            self._apply_layer_visibility(layer, visible)

    def _register(self, layer, item):
        item.setZValue(self._z(layer))
        self.addToGroup(item)
        self.layer_items.setdefault(layer, []).append(item)
        return item

    @staticmethod
    def _z(layer):
        try:
            return float(layermod.LAYER_ORDER.index(layer))
        except ValueError:
            return 0.0

    def layer_hex(self, layer):
        """Colour for one of this footprint's layers, honouring the design tint.

        Copper inside a footprint is drawn in the colour of the *board* plane
        the part sits on, so that a glance at the canvas says which design layer
        each footprint belongs to.  Everything else (silkscreen, courtyard,
        fabrication) keeps its normal KiCad colour.
        """
        if self._copper_tint and layer.endswith(".Cu"):
            return self._copper_tint
        return layermod.layer_hex(layer, self._theme)

    def set_copper_tint(self, color):
        """Recolour this footprint's copper, or pass ``None`` to clear."""
        color = color or None
        if color == self._copper_tint:
            return
        self._copper_tint = color
        self.apply_theme(self._theme)

    def _pen(self, layer, width):
        pen = QtGui.QPen(QtGui.QColor(self.layer_hex(layer)))
        pen.setWidthF(max(width, MIN_PEN))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def _graphics_for(self, layer):
        items = []
        fp = self.fp

        def path_item(path, width, filled, info):
            item = QtWidgets.QGraphicsPathItem(path)
            item.setPen(self._pen(layer, width))
            if filled:
                item.setBrush(QtGui.QColor(self.layer_hex(layer)))
            else:
                item.setBrush(QtGui.QBrush(Qt.NoBrush))
            item.setData(0, info)
            items.append(item)
            return item

        for l in fp.lines:
            if l.layer != layer:
                continue
            p = QtGui.QPainterPath(QtCore.QPointF(l.x1, l.y1))
            p.lineTo(l.x2, l.y2)
            path_item(p, l.width, False,
                      {"kind": "line", "layer": layer, "width": l.width,
                       "detail": "line %.3f,%.3f \u2192 %.3f,%.3f" % (l.x1, l.y1, l.x2, l.y2)})

        for r in fp.rects:
            if r.layer != layer:
                continue
            p = QtGui.QPainterPath()
            p.addRect(QtCore.QRectF(QtCore.QPointF(r.x1, r.y1), QtCore.QPointF(r.x2, r.y2)))
            path_item(p, r.width, r.filled,
                      {"kind": "rect", "layer": layer, "width": r.width,
                       "detail": "rect %.3f \u00d7 %.3f mm" % (abs(r.x2 - r.x1), abs(r.y2 - r.y1))})

        for c in fp.circles:
            if c.layer != layer:
                continue
            p = QtGui.QPainterPath()
            p.addEllipse(QtCore.QPointF(c.cx, c.cy), c.r, c.r)
            path_item(p, c.width, c.filled,
                      {"kind": "circle", "layer": layer, "width": c.width,
                       "detail": "circle r %.3f mm" % c.r})

        for a in fp.arcs:
            if a.layer != layer:
                continue
            path_item(_arc_path(a.start, a.mid, a.end), a.width, False,
                      {"kind": "arc", "layer": layer, "width": a.width,
                       "detail": "arc"})

        for poly in fp.polys:
            if poly.layer != layer or len(poly.points) < 3:
                continue
            path_item(_polygon_path(poly.points), poly.width, poly.filled,
                      {"kind": "poly", "layer": layer, "width": poly.width,
                       "detail": "polygon, %d points" % len(poly.points)})
        return items

    def _add_pad(self, pad):
        path = pad_path(pad)
        xform = QtGui.QTransform()
        xform.translate(pad.x, pad.y)
        xform.rotate(-pad.rot)                # KiCad angle is CCW on screen
        item = QtWidgets.QGraphicsPathItem(xform.map(path))
        color = QtGui.QColor(self.layer_hex(pad.copper_layer))
        item.setBrush(color)
        item.setPen(QtGui.QPen(Qt.NoPen))
        item.setData(0, {"kind": "pad", "pad": pad, "layer": pad.copper_layer,
                         "net": getattr(pad, "net_name", None),
                         "detail": "pad %s" % pad.number})
        item.setZValue(self._z(pad.copper_layer) + 0.5)
        self.addToGroup(item)
        self.pad_items[str(pad.number)] = item
        self.layer_items.setdefault(pad.copper_layer, []).append(item)

        label = QtWidgets.QGraphicsPathItem(
            _text_path(str(pad.number), min(pad.w, pad.h) * 0.42, pad.x, pad.y))
        label.setBrush(QtGui.QColor(PAD_LABEL[self._theme]))
        label.setPen(QtGui.QPen(Qt.NoPen))
        # Role 1 marks items whose colour is fixed, so apply_theme leaves them
        # alone instead of painting the number in the pad's own colour.
        label.setData(1, "label")
        label.setZValue(90.0)
        label.setData(0, {"kind": "pad", "pad": pad, "layer": pad.copper_layer,
                          "net": getattr(pad, "net_name", None),
                          "detail": "pad %s" % pad.number})
        self.addToGroup(label)
        self.pad_labels.append(label)
        self.layer_items.setdefault(pad.copper_layer, []).append(label)

    def _add_text(self, txt):
        display_text = (self.reference_override
                        if txt.kind == "reference" and self.reference_override
                        else txt.text)
        item = QtWidgets.QGraphicsPathItem(
            _text_path(display_text, max(txt.size, 0.1), txt.x, txt.y, txt.rot, txt.mirror))
        color = QtGui.QColor(self.layer_hex(txt.layer))
        item.setBrush(color)
        item.setPen(QtGui.QPen(Qt.NoPen))
        item.setZValue(self._z(txt.layer) + 0.6)
        item.setData(0, {"kind": "text", "text": txt, "layer": txt.layer,
                         "detail": "%s: %s" % (txt.kind, display_text)})
        item.setVisible(not txt.hide)
        self.addToGroup(item)
        self.text_items.append((txt, item))
        self.layer_items.setdefault(txt.layer, []).append(item)

    # -- appearance ------------------------------------------------------
    def apply_theme(self, theme):
        self._theme = theme
        for layer, items in self.layer_items.items():
            color = QtGui.QColor(self.layer_hex(layer))
            for item in items:
                if item.data(1) == "label":
                    continue                 # fixed-colour pad numbers
                pen = item.pen()
                if pen.style() != Qt.NoPen:
                    pen.setColor(color)
                    item.setPen(pen)
                if item.brush().style() != Qt.NoBrush:
                    item.setBrush(color)
        for item in self.pad_items.values():
            pad = (item.data(0) or {}).get("pad")
            if pad is not None:
                item.setBrush(QtGui.QColor(self.layer_hex(pad.copper_layer)))
        for label in self.pad_labels:
            label.setBrush(QtGui.QColor(PAD_LABEL[theme]))
        self._apply_pad_net_highlight()
        self.update()

    def _apply_pad_net_highlight(self):
        """Apply KiCad-style net emphasis to this footprint's copper pads."""
        enabled = bool(self._net_highlight_enabled and self._highlighted_nets)
        for number, item in self.pad_items.items():
            info = item.data(0) or {}
            pad = info.get("pad")
            net = getattr(pad, "net_name", None) if pad is not None else info.get("net")
            match = bool(enabled and net in self._highlighted_nets)
            color = QtGui.QColor(self.layer_hex(pad.copper_layer if pad is not None else info.get("layer", "F.Cu")))
            if match:
                color = color.lighter(145)
            item.setBrush(color)
            item.setOpacity(1.0 if (not enabled or match) else 0.16)
            info["net"] = net
            item.setData(0, info)
        for label in self.pad_labels:
            info = label.data(0) or {}
            pad = info.get("pad")
            net = getattr(pad, "net_name", None) if pad is not None else info.get("net")
            match = bool(enabled and net in self._highlighted_nets)
            label.setOpacity(1.0 if (not enabled or match) else 0.10)
            info["net"] = net
            label.setData(0, info)

    def set_net_highlight(self, nets=None, enabled=False):
        self._highlighted_nets = set(str(n) for n in (nets or []) if n not in (None, ""))
        self._net_highlight_enabled = bool(enabled and self._highlighted_nets)
        self._apply_pad_net_highlight()
        self.update()

    def is_layer_visible(self, layer):
        """Return the saved visibility state used by the sidebar checkbox."""
        return bool(self.layer_visibility.get(layer, True))

    def _apply_layer_visibility(self, layer, visible):
        """Apply a saved layer state to child items without changing geometry."""
        for item in self.layer_items.get(layer, []):
            info = item.data(0) or {}
            if info.get("kind") == "text" and info.get("text") is not None \
                    and info["text"].hide:
                # A KiCad-hidden text is visible only when both its layer and
                # the viewer's explicit "hidden text" option are enabled.
                item.setVisible(bool(visible) and self._hidden_texts_visible)
            else:
                item.setVisible(bool(visible))

    def set_layer_visible(self, layer, visible):
        visible = bool(visible)
        self.layer_visibility[layer] = visible

        # Child visibility changes alter shape()/visible_rect().  Tell the
        # graphics scene before mutating them so hit testing and selection
        # painting do not retain stale cached geometry.
        self.prepareGeometryChange()
        self._apply_layer_visibility(layer, visible)
        self._shape_cache = None
        self.update(self.boundingRect())

    def set_hidden_texts_visible(self, visible):
        self._hidden_texts_visible = bool(visible)
        self.prepareGeometryChange()
        for txt, item in self.text_items:
            if txt.hide:
                item.setVisible(self._hidden_texts_visible and
                                self.is_layer_visible(txt.layer))
        self._shape_cache = None
        self.update(self.boundingRect())

    # -- geometry --------------------------------------------------------
    def boundingRect(self):
        # Keep a stable, conservative geometry bound.  Selection painting uses
        # a pixel-sized margin that changes with zoom, so reserve enough room
        # for that margin at the minimum supported zoom level.
        rect = self._local_rect.united(self.childrenBoundingRect())
        return rect.adjusted(-SELECTION_BOUND_PAD, -SELECTION_BOUND_PAD,
                             SELECTION_BOUND_PAD, SELECTION_BOUND_PAD)

    def shape(self):
        """Use the displayed footprint bounding rectangle as the hit area.

        Selection should not require hitting a copper pad or a one-pixel silk
        stroke.  The whole rectangle represented by the selection outline is
        clickable, including its empty interior.  ``visible_rect()`` excludes
        KiCad-hidden text, so a hidden value string still cannot create a huge
        remote hit target.
        """
        if self._shape_cache is not None:
            return QtGui.QPainterPath(self._shape_cache)

        path = QtGui.QPainterPath()
        path.addRect(self.visible_rect())
        self._shape_cache = QtGui.QPainterPath(path)
        return path

    def visible_rect(self):
        """Bounds of what is actually drawn, in item coordinates.

        Qt's childrenBoundingRect()/itemsBoundingRect() include hidden items, so
        a footprint whose value text is hidden (the usual case) would otherwise
        zoom to fit a label nobody can see -- KiCad's own value strings are
        often three times wider than the part.
        """
        rect = QtCore.QRectF(self._local_rect)
        for child in self.childItems():
            if child.isVisible():
                rect = rect.united(child.mapToParent(child.boundingRect()).boundingRect())
        return rect

    def set_selected_pad(self, number):
        """Highlight one of this footprint's pads, or ``None`` to clear."""
        number = None if number is None else str(number)
        if number is not None and number not in self.pad_items:
            number = None
        if number == self.selected_pad:
            return
        self.selected_pad = number
        self.update(self.boundingRect())

    def paint(self, painter, option, widget=None):
        if not self.isSelected():
            return
        pen = QtGui.QPen(QtGui.QColor(thememod.palette(self._theme)["selection"]))
        pen.setCosmetic(True)
        pen.setWidth(1)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(QtGui.QBrush(Qt.NoBrush))
        # Keep the visual gap around the selection at roughly five screen
        # pixels instead of a fixed number of millimetres.  That prevents the
        # rectangle from looking detached when zoomed in or glued to the part
        # when zoomed far out.
        transform = painter.worldTransform()
        sx = math.hypot(transform.m11(), transform.m12())
        sy = math.hypot(transform.m21(), transform.m22())
        scale = max(sx, sy, 1e-9)
        pad = min(SELECTION_BOUND_PAD, SELECTION_PAD_PX / scale)

        pad_item = self.pad_items.get(self.selected_pad or "")
        if pad_item is not None:
            # A highlighted pad replaces the footprint outline rather than
            # adding to it: two nested rectangles read as one confusing
            # selection, and the pad is what the user actually picked.
            rect = pad_item.mapToParent(pad_item.boundingRect()).boundingRect()
            rect = rect.adjusted(-pad * 0.6, -pad * 0.6, pad * 0.6, pad * 0.6)
            # Stroke twice: a canvas-coloured halo underneath, then the
            # selection colour on top.  A single blue outline all but vanished
            # against blue copper, and pad colour follows the design layer, so
            # no one fixed colour can be relied on to contrast.
            halo = QtGui.QPen(QtGui.QColor(thememod.palette(self._theme)["canvas"]))
            halo.setCosmetic(True)
            halo.setWidth(4)
            painter.setPen(halo)
            painter.drawRect(rect)

            # A selected pad also follows its actual copper-layer colour.
            # The canvas-coloured halo provides contrast without suggesting
            # that the pad has moved to the generic accent/B.Cu colour.
            pad_color = QtGui.QColor(self.layer_hex((pad_item.data(0) or {}).get("layer", ""))).lighter(140)
            pen.setColor(pad_color)
            pen.setStyle(Qt.SolidLine)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(rect)
            return

        rect = self.visible_rect().adjusted(-pad, -pad, pad, pad)
        painter.drawRect(rect)

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionChange and self.scene():
            grid = getattr(self.scene(), "snap_step", 0.0)
            if grid:
                value = QtCore.QPointF(round(value.x() / grid) * grid,
                                       round(value.y() / grid) * grid)
            return value
        return super(FootprintItem, self).itemChange(change, value)


# ---------------------------------------------------------------------------
# the view
# ---------------------------------------------------------------------------
class FootprintCanvas(QtWidgets.QGraphicsView):
    """Pan / zoom / hover / select, plus the KiCad-style grid and crosshair."""

    cursorMoved = Signal(float, float)
    hoverChanged = Signal(object)
    selectionChanged = Signal(object)
    padSelectionChanged = Signal(object)
    itemClicked = Signal(object)
    footprintMoved = Signal(object)
    footprintsMoving = Signal(object)
    footprintsMoved = Signal(object)
    zoomChanged = Signal(float)
    rendererChanged = Signal(str)
    copperSelectionChanged = Signal(object)
    selectionMoved = Signal(object)
    routePointerMoved = Signal(object, object)
    routeClicked = Signal(object, object)
    routeDoubleClicked = Signal(object, object)
    movePointerMoved = Signal(object, object)
    moveClicked = Signal(object, object)
    moveCancelled = Signal()
    copperDragStarted = Signal(object, object, object)
    copperDragPointerMoved = Signal(object, object)
    copperDragFinished = Signal(object, object)

    def __init__(self, parent=None):
        super(FootprintCanvas, self).__init__(parent)
        self.renderer_name = "software (Qt QPainter)"
        self.gpu_accelerated = False
        self._gpu_validation_pending = False
        self._scene = QtWidgets.QGraphicsScene(self)
        self._scene.snap_step = 0.0
        self.setScene(self._scene)

        self.theme = "dark"
        self.grid_visible = True
        self.grid_step = 0.1
        self.snap_enabled = True
        self.crosshair = True
        self.crosshair_mode = "small"
        self.crosshair_size_px = DEFAULT_SMALL_CROSSHAIR_PX
        self.footprints = []
        self.track_items = []
        self.via_items = []
        self.routing_enabled = False
        self.interactive_move_enabled = False
        self._interactive_move_release_pending = False
        self._direct_copper_drag_active = False
        self._direct_copper_drag_item = None
        self._route_overlay_items = []
        # Board boundaries are derived from the imported board solid, one
        # read-only cross-section per copper layer.  They are reference
        # geometry, not editable scene objects.
        self.board_edge_items = {}
        self.board_sections = {}
        self.board_edge_active_id = None
        self.board_has_solid = False
        self._board_mask = None
        self._cursor_scene = QtCore.QPointF()
        self._panning = False
        self._pan_origin = QtCore.QPoint()
        # Scrollbar positions captured at pan start.  Panning is computed from
        # this fixed origin rather than incrementally from successive scene
        # mappings; that avoids sub-pixel/scrollbar rounding accumulating into
        # a diagonal drift when the mouse is moved back and forth.
        self._pan_scroll_origin = QtCore.QPoint()
        self._hover_info = None
        self._highlighted_nets = set()
        self._net_highlight_enabled = False
        self._press_pos = None
        self._selected_pad = None    # {"item": FootprintItem, "pad": Pad} or None
        # KiCad-style selection filter.  It controls what can be picked or
        # rubber-band selected without changing visibility.  Pads use the
        # canvas' finer-grained pad-selection path; all other categories map to
        # QGraphicsItem selection flags.
        self.selection_filter = {
            "footprints": True, "pads": True, "tracks": True, "vias": True,
        }
        # Mouse gestures keep two distinct transform snapshots:
        #
        # * _drag_origin / _drag_start_rotation are immutable mouse-down state.
        #   They are the only source for undo's "before" transform.
        # * _drag_rotation / _drag_rebase_* are the live continuation state.
        #   Keyboard transforms such as R may update these while the button is
        #   held, without destroying the original undo baseline.
        self._drag_origin = {}
        self._drag_start_rotation = {}
        self._drag_rotation = {}
        self._drag_rebase_positions = {}
        self._drag_rebase_cursor = None
        # Explicit gesture lifetime.  The transform dictionaries alone are not
        # sufficient because an interrupted mouse grab can leave stale snapshots
        # behind; keyboard R must only join a gesture while a real left-button
        # edit is active.
        self._left_drag_active = False
        # Routed copper stores geometry in absolute board coordinates. During
        # a native QGraphicsScene drag its item.pos() is only temporary; on
        # release that delta is folded back into Track/Via model coordinates.
        self._copper_drag_origin = {}
        self._copper_drag_before = {}
        self._last_mouse_view_pos = None
        self._auto_fit = True        # keep fitting on resize until the user zooms

        self._install_render_viewport()
        self.setRenderHints(QtGui.QPainter.Antialiasing |
                            QtGui.QPainter.TextAntialiasing |
                            QtGui.QPainter.SmoothPixmapTransform)
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setResizeAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scale(40.0, 40.0)               # sensible default: 40 px/mm
        # QGraphicsView recentres a scene smaller than the viewport.  Give an
        # empty editor a large virtual workspace so middle/right-button pan is
        # immediately useful, before the first footprint is imported.
        self._update_scene_rect()

        self._scene.selectionChanged.connect(self._on_selection)

    # -- renderer --------------------------------------------------------
    def _install_render_viewport(self):
        """Use a direct Qt OpenGL viewport when this FreeCAD/Qt build can.

        ``QGraphicsView`` continues to own the scene and all input semantics;
        the viewport only changes the paint device.  Set
        ``ELEC3D_DISABLE_GPU_2D=1`` for diagnostics or remote-desktop setups
        that deliberately want the software QWidget path.
        """
        import os

        disabled = str(os.environ.get("ELEC3D_DISABLE_GPU_2D", "")).strip().lower()
        disabled = disabled in ("1", "true", "yes", "on")
        if _Gpu2DViewport is None or disabled:
            self.renderer_name = ("software (GPU disabled)" if disabled
                                  else "software (Qt QPainter)")
            self.gpu_accelerated = False
            return
        try:
            viewport = _Gpu2DViewport(self)
            viewport.setMouseTracking(True)
            self.setViewport(viewport)
            self.renderer_name = "GPU 2D (Qt OpenGL)"
            self.gpu_accelerated = True
        except Exception:
            self.renderer_name = "software fallback"
            self.gpu_accelerated = False

    def _validate_gpu_viewport(self):
        """Fall back if QOpenGLWidget exists but could not make a context."""
        self._gpu_validation_pending = False
        if not self.gpu_accelerated:
            return
        viewport = self.viewport()
        if _Gpu2DViewport is None or not isinstance(viewport, _Gpu2DViewport):
            return
        try:
            valid = bool(viewport.isValid())
        except Exception:
            valid = False
        if valid:
            self.rendererChanged.emit(self.renderer_name)
            return

        # Do this after the GL widget's own show/initialise event has unwound.
        # Replacing a viewport synchronously from initialize/paint callbacks is
        # exactly the sort of native lifetime pattern we avoid in the 3D view.
        old = viewport
        fallback = QtWidgets.QWidget(self)
        fallback.setMouseTracking(True)
        self.setViewport(fallback)
        try:
            old.hide()
            old.deleteLater()
        except RuntimeError:
            pass
        self.renderer_name = "software fallback"
        self.gpu_accelerated = False
        self.rendererChanged.emit(self.renderer_name)
        self.viewport().update()

    def showEvent(self, event):
        super(FootprintCanvas, self).showEvent(event)
        if self.gpu_accelerated and not self._gpu_validation_pending:
            self._gpu_validation_pending = True
            # QOpenGLWidget obtains its context only after becoming visible.
            QtCore.QTimer.singleShot(0, self._validate_gpu_viewport)

    # -- selection filter ------------------------------------------------
    def selection_filter_enabled(self, kind):
        return bool(self.selection_filter.get(str(kind), True))

    def set_selection_filter(self, values=None):
        """Update the 2D selection filter and apply it to existing items.

        Filtering is deliberately presentation/editor state rather than PCB
        design data: it neither dirties the project nor changes visibility.
        """
        for key, value in dict(values or {}).items():
            if key in self.selection_filter:
                self.selection_filter[key] = bool(value)
        self._apply_selection_filter()
        return dict(self.selection_filter)

    def _apply_selection_filter(self):
        fp_on = self.selection_filter_enabled("footprints")
        tracks_on = self.selection_filter_enabled("tracks")
        vias_on = self.selection_filter_enabled("vias")
        self._scene.blockSignals(True)
        try:
            for item in self.footprints:
                item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, fp_on)
                item.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, fp_on)
                if not fp_on and item.isSelected():
                    item.setSelected(False)
            for item in self.track_items:
                item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, tracks_on)
                if not tracks_on and item.isSelected():
                    item.setSelected(False)
            for item in self.via_items:
                item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, vias_on)
                if not vias_on and item.isSelected():
                    item.setSelected(False)
            if not self.selection_filter_enabled("pads"):
                self.select_pad(None, None)
        finally:
            self._scene.blockSignals(False)
        self.viewport().update()
        self._on_selection()

    # -- content ---------------------------------------------------------
    def add_footprint(self, fp, position=None, reference_override=""):
        item = FootprintItem(fp, reference_override=reference_override)
        item.apply_theme(self.theme)
        item.set_net_highlight(self._highlighted_nets, self._net_highlight_enabled)
        self._scene.addItem(item)
        if position is not None:
            item.setPos(position)
        self.footprints.append(item)
        fp_on = self.selection_filter_enabled("footprints")
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, fp_on)
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, fp_on)
        self._update_scene_rect()
        return item

    def remove_footprint(self, item):
        if item in self.footprints:
            # A pad highlight pointing at a footprint that is going away would
            # keep the Properties panel describing a deleted object.
            if self._selected_pad and self._selected_pad["item"] is item:
                self.select_pad(None, None)
            self.footprints.remove(item)
            self._scene.removeItem(item)
            self._update_scene_rect()

    def clear_footprints(self):
        self.select_pad(None, None)
        for item in list(self.footprints):
            self._scene.removeItem(item)
        self.footprints = []
        self._update_scene_rect()

    # -- board solid sections ----------------------------------------------
    def set_board_sections(self, sections, active_id=None, visible=None, has_solid=None):
        """Show the board solid's cross-section for each copper layer.

        ``sections`` maps copper layer id -> loops in scene (Y-down)
        millimetres.  The active layer is drawn solid with the outside of the
        board softly shaded; other visible layers are shown as faint dashed
        references.  ``visible`` is an optional set of visible layer ids.
        """
        if has_solid is not None:
            self.board_has_solid = bool(has_solid)
        sections = dict(sections or {})
        visible = set(visible) if visible is not None else set(sections.keys())
        color = layermod.outline_hex(self.theme)
        for uid in list(self.board_edge_items.keys()):
            if uid not in sections or [list(l) for l in sections[uid]] != self.board_edge_items[uid].loops:
                item = self.board_edge_items.pop(uid)
                try:
                    self._scene.removeItem(item)
                except RuntimeError:
                    pass
        for uid, loops in sections.items():
            if uid not in self.board_edge_items and loops:
                item = BoardEdgeItem(uid, loops, color)
                self._scene.addItem(item)
                self.board_edge_items[uid] = item
        self.board_sections = sections
        self.board_edge_active_id = active_id
        for uid, item in self.board_edge_items.items():
            item.set_style(color, active=(uid == active_id))
            item.setVisible(uid in visible)
            item.setZValue(26.5 if uid == active_id else 26.0)

        if self.board_has_solid and active_id is not None:
            if self._board_mask is None:
                self._board_mask = BoardMaskItem()
                self._scene.addItem(self._board_mask)
            shade = QtGui.QColor(0, 0, 0, 70 if self.theme == "dark" else 28)
            loops = sections.get(active_id) or []
            self._board_mask.set_loops(loops, shade, blocked=not loops)
            self._board_mask.setVisible(active_id in visible)
        elif self._board_mask is not None:
            self._board_mask.setVisible(False)
        self._update_scene_rect()
        self.viewport().update()

    def clear_board_sections(self):
        self.set_board_sections({}, None, has_solid=False)

    def board_rect(self):
        """Scene rectangle of all board sections (empty when none)."""
        rect = QtCore.QRectF()
        for item in self.board_edge_items.values():
            br = item.path().boundingRect()
            rect = QtCore.QRectF(br) if rect.isNull() else rect.united(br)
        return rect

    def hover_info(self):
        return self._hover_info

    def set_net_highlight(self, nets=None, enabled=False):
        """Dim unrelated copper and brighten the requested schematic nets."""
        self._highlighted_nets = set(str(n) for n in (nets or []) if n not in (None, ""))
        self._net_highlight_enabled = bool(enabled and self._highlighted_nets)
        for item in self.footprints:
            item.set_net_highlight(self._highlighted_nets, self._net_highlight_enabled)
        for item in self.track_items + self.via_items:
            item.set_net_highlight(self._highlighted_nets, self._net_highlight_enabled)
        self.viewport().update()

    def refresh_copper_net_metadata(self):
        """Refresh hover/style data after topology assigns track/via net names."""
        for item in self.track_items + self.via_items:
            item.set_net_highlight(self._highlighted_nets, self._net_highlight_enabled)
        self.viewport().update()

    def content_rect(self):
        """Union of the visible parts of every footprint, in scene coordinates."""
        rect = QtCore.QRectF()
        for item in self.footprints:
            # A footprint whose design layer is switched off must not keep
            # pulling the fitted view towards empty space.
            if not item.isVisible():
                continue
            mapped = item.mapToScene(item.visible_rect()).boundingRect()
            rect = QtCore.QRectF(mapped) if rect.isNull() else rect.united(mapped)
        board = self.board_rect()
        if not board.isNull():
            rect = QtCore.QRectF(board) if rect.isNull() else rect.united(board)
        for item in self.track_items + self.via_items:
            if not item.isVisible():
                continue
            mapped = item.mapToScene(item.boundingRect()).boundingRect()
            rect = QtCore.QRectF(mapped) if rect.isNull() else rect.united(mapped)
        return rect

    def _update_scene_rect(self):
        """Keep a persistent pan workspace, enlarged only when content needs it.

        QGraphicsView clamps ``centerOn()`` to the scene rectangle.  Older
        versions replaced the large empty-workspace rectangle with a tight
        content rectangle as soon as the first footprint was imported.  That
        made panning appear unrestricted before import and suddenly limited
        afterwards.  The workspace is now always present; footprint extents
        are unioned into it rather than replacing it.
        """
        span = PAN_WORKSPACE_HALF_SPAN
        workspace = QtCore.QRectF(-span, -span, 2.0 * span, 2.0 * span)

        rect = self.content_rect()
        if rect.isEmpty():
            self._scene.setSceneRect(workspace)
            return

        # Keep some extra room around content that is moved/imported outside
        # the default workspace, while never shrinking the normal pan area.
        pad = max(rect.width(), rect.height()) * 2.0 + 10.0
        content_workspace = rect.adjusted(-pad, -pad, pad, pad)
        self._scene.setSceneRect(workspace.united(content_workspace))

    # -- routed copper ---------------------------------------------------
    def clear_routed_copper(self):
        for item in list(self.track_items) + list(self.via_items):
            try:
                self._scene.removeItem(item)
            except Exception:
                pass
        self.track_items = []
        self.via_items = []
        self.clear_route_preview()
        self._update_scene_rect()

    def set_routed_copper(self, tracks, vias, stack):
        """Rebuild saved tracks/vias from the plain routing models."""
        selected = set()
        for item in self.selected_copper_items():
            model = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if model is not None:
                selected.add(model.uid)
        self.clear_routed_copper()
        active = stack.active_id
        order = [layer.id for layer in stack.layers]
        for track in tracks or []:
            layer = stack.by_id(track.layer_id)
            if layer is None or not layer.is_copper:
                continue
            item = TrackGraphicsItem(track, layer.color)
            item.setVisible(bool(layer.visible))
            item.update_style(opacity=(1.0 if layer.id == active else 0.55))
            item.set_net_highlight(self._highlighted_nets, self._net_highlight_enabled)
            self._scene.addItem(item); self.track_items.append(item)
            item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable,
                         self.selection_filter_enabled("tracks"))
            if track.uid in selected and self.selection_filter_enabled("tracks"):
                item.setSelected(True)
        for via in vias or []:
            spanned = [layer for layer in stack.layers
                       if via_spans_layer(via, layer.id, order)]
            visible_layers = [layer for layer in spanned if layer.visible]
            color_layer = stack.by_id(active) if any(layer.id == active for layer in visible_layers) else (
                visible_layers[-1] if visible_layers else (spanned[-1] if spanned else stack.bottom()))
            item = ViaGraphicsItem(via, color_layer.color if color_layer else "#C9A227")
            item.setVisible(bool(visible_layers))
            item.update_style(opacity=(1.0 if any(layer.id == active for layer in spanned) else 0.65))
            item.set_net_highlight(self._highlighted_nets, self._net_highlight_enabled)
            self._scene.addItem(item); self.via_items.append(item)
            item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable,
                         self.selection_filter_enabled("vias"))
            if via.uid in selected and self.selection_filter_enabled("vias"):
                item.setSelected(True)
        self._update_scene_rect()
        self.viewport().update()

    def sync_routed_copper_style(self, stack, update_extent=False):
        """Update active/visible copper styling without rebuilding scene items.

        Design-layer activation changes only presentation.  Recreating every
        track/via for that operation made the entire canvas flash and also
        generated avoidable selectionChanged traffic.
        """
        active = stack.active_id
        order = [layer.id for layer in stack.layers]
        for item in self.track_items:
            track = item.track_model
            layer = stack.by_id(track.layer_id)
            if layer is None or not layer.is_copper:
                item.setVisible(False)
                continue
            item.setVisible(bool(layer.visible))
            item.update_style(color=layer.color,
                              opacity=(1.0 if layer.id == active else 0.55))

        for item in self.via_items:
            via = item.via_model
            spanned = [layer for layer in stack.layers
                       if via_spans_layer(via, layer.id, order)]
            visible_layers = [layer for layer in spanned if layer.visible]
            color_layer = stack.by_id(active) if any(layer.id == active for layer in visible_layers) else (
                visible_layers[-1] if visible_layers else (spanned[-1] if spanned else stack.bottom()))
            item.setVisible(bool(visible_layers))
            item.update_style(color=(color_layer.color if color_layer else "#C9A227"),
                              opacity=(1.0 if any(layer.id == active for layer in spanned) else 0.65))

        if update_extent:
            self._update_scene_rect()
        self.viewport().update()

    def selected_copper_items(self):
        selected = set(self._scene.selectedItems())
        return [item for item in self.track_items + self.via_items if item in selected]

    def select_copper_items(self, items):
        items = [i for i in (items or []) if i is not None]
        self._scene.blockSignals(True)
        try:
            self._scene.clearSelection()
            for item in items: item.setSelected(True)
        finally:
            self._scene.blockSignals(False)
        self._on_selection()

    def select_editor_items(self, footprints=None, copper=None):
        """Replace selection while allowing footprints and routed copper together."""
        footprints = [i for i in (footprints or []) if i in self.footprints]
        copper_pool = self.track_items + self.via_items
        copper = [i for i in (copper or []) if i in copper_pool]
        self.select_pad(None, None)
        self._scene.blockSignals(True)
        try:
            self._scene.clearSelection()
            for item in footprints + copper:
                item.setSelected(True)
                item.update(item.boundingRect())
        finally:
            self._scene.blockSignals(False)
        self.viewport().update()
        self._on_selection()

    def cancel_direct_copper_drag(self):
        """Abort canvas-side direct copper drag capture."""
        self._direct_copper_drag_active = False
        self._direct_copper_drag_item = None
        self._left_drag_active = False
        self._press_pos = None
        self._drag_origin = {}
        self._drag_start_rotation = {}
        self._drag_rotation = {}
        self._drag_rebase_positions = {}
        self._drag_rebase_cursor = None
        self._last_mouse_view_pos = None
        try:
            self.unsetCursor()
        except RuntimeError:
            pass

    def set_interactive_move_enabled(self, enabled):
        """Enter/leave the explicit M-key move tool.

        While active, native QGraphicsItem dragging is bypassed and pointer
        motion/clicks are forwarded to Viewer2D, which owns the model snapshots,
        DRC checks and undo command.
        """
        self.interactive_move_enabled = bool(enabled)
        if self.interactive_move_enabled:
            self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
            self.setCursor(Qt.CrossCursor)
        elif not self.routing_enabled:
            self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
            self.unsetCursor()
        self.viewport().update()

    def editor_cursor_scene(self):
        return QtCore.QPointF(self._cursor_scene)

    def set_routing_enabled(self, enabled):
        self.routing_enabled = bool(enabled)
        if self.routing_enabled:
            self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
            self.setCursor(Qt.CrossCursor)
        else:
            if not self.interactive_move_enabled:
                self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
                self.unsetCursor()
            self.clear_route_preview()

    def clear_route_preview(self):
        for item in self._route_overlay_items:
            try: self._scene.removeItem(item)
            except Exception: pass
        self._route_overlay_items = []
        self.viewport().update()

    def set_route_preview(self, points=None, width=0.25, color="#4D7FC4",
                          snap=None, via=None, airwires=None, blocked=False):
        """Redraw only the small routing overlay used during pointer motion."""
        self.clear_route_preview()
        pts = list(points or [])
        if len(pts) >= 2:
            path = QtGui.QPainterPath(QtCore.QPointF(pts[0][0], pts[0][1]))
            for x, y in pts[1:]: path.lineTo(float(x), float(y))
            item = QtWidgets.QGraphicsPathItem(path)
            pen = QtGui.QPen(QtGui.QColor(color)); pen.setWidthF(max(MIN_PEN, float(width)))
            pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
            item.setPen(pen); item.setOpacity(0.82); item.setZValue(80.0)
            item.setAcceptedMouseButtons(Qt.NoButton)
            self._scene.addItem(item); self._route_overlay_items.append(item)
        for a, b in (airwires or []):
            path = QtGui.QPainterPath(QtCore.QPointF(float(a[0]), float(a[1])))
            path.lineTo(float(b[0]), float(b[1]))
            item = QtWidgets.QGraphicsPathItem(path)
            pen = QtGui.QPen(QtGui.QColor("#8A8A8A")); pen.setCosmetic(True)
            pen.setStyle(Qt.DashLine); pen.setWidth(1)
            item.setPen(pen); item.setZValue(79.0); item.setAcceptedMouseButtons(Qt.NoButton)
            self._scene.addItem(item); self._route_overlay_items.append(item)
        if via is not None:
            item = ViaGraphicsItem(via, color)
            item.setOpacity(0.55); item.setZValue(82.0); item.setAcceptedMouseButtons(Qt.NoButton)
            self._scene.addItem(item); self._route_overlay_items.append(item)
        if snap is not None:
            radius = 0.35
            if snap.get("kind") == "pad" and snap.get("pad"):
                pad = snap["pad"]; radius = max(float(pad.get("w",0)), float(pad.get("h",0))) / 2.0 + 0.25
            path = QtGui.QPainterPath(); path.addEllipse(QtCore.QPointF(float(snap["x"]), float(snap["y"])), radius, radius)
            item = QtWidgets.QGraphicsPathItem(path)
            pen = QtGui.QPen(QtGui.QColor("#5C7CFA")); pen.setCosmetic(True); pen.setWidth(2)
            item.setPen(pen); item.setBrush(QtGui.QBrush(Qt.NoBrush)); item.setZValue(84.0)
            item.setAcceptedMouseButtons(Qt.NoButton)
            self._scene.addItem(item); self._route_overlay_items.append(item)
        self.viewport().update()

    # -- view state ------------------------------------------------------
    def px_per_mm(self):
        return self.transform().m11()

    def set_zoom(self, px_per_mm, anchor_view_pos=None):
        px_per_mm = max(ZOOM_MIN, min(ZOOM_MAX, px_per_mm))
        current = self.px_per_mm()
        if abs(px_per_mm - current) < 1e-9:
            return
        if anchor_view_pos is None:
            anchor_view_pos = QtCore.QPoint(self.viewport().width() // 2,
                                            self.viewport().height() // 2)
        before = self.mapToScene(anchor_view_pos)
        self.scale(px_per_mm / current, px_per_mm / current)
        after = self.mapToScene(anchor_view_pos)
        delta = after - before
        self.translate(delta.x(), delta.y())
        self.zoomChanged.emit(self.px_per_mm())
        self.viewport().update()

    def resizeEvent(self, event):
        super(FootprintCanvas, self).resizeEvent(event)
        if self._auto_fit:
            # The first fit() usually runs before the layout has settled, so the
            # view would keep whatever scale a 100px-tall viewport implied.
            self.fit()
            self._auto_fit = True
        # Reparenting between a top-level window and an MDI tab changes the
        # viewport geometry under a stationary mouse.  Re-map the cursor so
        # the crosshair/status coordinate stays aligned with the grid.
        self._refresh_cursor_from_global()

    def fit(self):
        rect = self.content_rect()
        if rect.isEmpty():
            # Nothing to frame yet: keep the zoom and put the origin in the
            # middle of the viewport.  resizeEvent() repeats this until the
            # user pans/zooms, so the origin stays centred while the MDI tab or
            # window settles into its final size.
            self._auto_fit = True
            self.centerOn(QtCore.QPointF(0.0, 0.0))
            self.viewport().update()
            return
        self._auto_fit = True
        margin = max(rect.width(), rect.height()) * FIT_MARGIN
        rect = rect.adjusted(-margin, -margin, margin, margin)
        self.fitInView(rect, Qt.KeepAspectRatio)
        self.zoomChanged.emit(self.px_per_mm())
        self.viewport().update()

    def set_theme(self, theme):
        self.theme = theme
        for item in self.footprints:
            item.apply_theme(theme)
        if self.board_edge_items or self._board_mask is not None:
            self.set_board_sections(self.board_sections, self.board_edge_active_id,
                                    visible=[uid for uid, item in self.board_edge_items.items()
                                             if item.isVisible()])
        self.viewport().update()

    def refresh_extent(self):
        """Call after visibility changes so fit() uses the new content."""
        self._update_scene_rect()
        if self._auto_fit:
            self.fit()

    def set_crosshair_mode(self, mode, size_px=None):
        """Choose the cursor style and compact crosshair screen-space length."""
        mode = str(mode or "small").lower()
        self.crosshair_mode = mode if mode in ("small", "full") else "small"
        if size_px is not None:
            self.crosshair_size_px = normalize_crosshair_size(size_px)
        self.viewport().update()

    def set_grid(self, visible=None, step=None, snap=None):
        if visible is not None:
            self.grid_visible = visible
        if step is not None:
            self.grid_step = step
        if snap is not None:
            self.snap_enabled = snap
        self._scene.snap_step = self.grid_step if self.snap_enabled else 0.0
        # The KiCad-style cursor follows the active snap grid too.  Refresh it
        # immediately when grid/snap settings change, even if the physical
        # mouse has not moved.
        self._refresh_cursor_from_global()
        self.viewport().update()

    def _snap_cursor_point(self, point):
        """Return *point* snapped to the visible grid when snap is enabled.

        Item hit-testing still uses the real mouse position.  Only the
        crosshair/status coordinate follows the snap grid, matching KiCad's
        editing cursor and preventing values such as 2.725 mm while a 0.1 mm
        snap grid is enabled.
        """
        if not self.snap_enabled or self.grid_step <= 0:
            return QtCore.QPointF(point)
        step = float(self.grid_step)
        return QtCore.QPointF(round(point.x() / step) * step,
                              round(point.y() / step) * step)

    def cursor_scene_position(self, view_pos):
        """Map a viewport position to the editor cursor scene position."""
        return self._snap_cursor_point(self.mapToScene(view_pos))

    def _update_cursor_position(self, view_pos):
        self._cursor_scene = self.cursor_scene_position(view_pos)
        self.cursorMoved.emit(self._cursor_scene.x(), self._cursor_scene.y())
        if self.crosshair:
            self.viewport().update()

    def _refresh_cursor_from_global(self):
        """Refresh the cursor after pan/zoom/reparent without mouse motion."""
        try:
            if not self.viewport().underMouse():
                return
            pos = self.viewport().mapFromGlobal(QtGui.QCursor.pos())
            if self.viewport().rect().contains(pos):
                self._update_cursor_position(pos)
        except (AttributeError, RuntimeError):
            pass

    def has_active_transform_gesture(self):
        """Return True only while a real left-button transform is active."""
        active = bool(self._left_drag_active and self._drag_origin
                      and self._drag_start_rotation)
        if not active:
            return False
        # The explicit flag protects normal event ordering; the application
        # button state also rejects a stale snapshot if Qt/FreeCAD ever loses a
        # release event while the editor is being rehosted or interrupted.
        try:
            if not (QtWidgets.QApplication.mouseButtons() & Qt.LeftButton):
                return False
        except (AttributeError, RuntimeError, TypeError):
            pass
        return True

    def has_active_interaction(self):
        """Whether file/rehost operations should wait for an edit to finish."""
        return bool(self._panning or self._left_drag_active
                    or self._direct_copper_drag_active
                    or self.interactive_move_enabled)

    def _restore_uncommitted_transform(self):
        """Restore the immutable mouse-down state without creating history.

        QGraphicsScene moves items continuously before mouse release.  If the
        gesture is interrupted (host rebuild, dialog, teardown), simply clearing
        the bookkeeping would strand those temporary positions in the scene and
        make the next undo start from the wrong layout.
        """
        old_snap = getattr(self._scene, "snap_step", 0.0)
        self._scene.snap_step = 0.0
        try:
            for item, origin in list(self._drag_origin.items()):
                if item not in self.footprints:
                    continue
                item.setPos(QtCore.QPointF(origin))
                if item in self._drag_start_rotation:
                    item.setRotation(float(self._drag_start_rotation[item]))
            # Copper geometry is not folded into the Track/Via models until
            # release, so resetting item.pos() is sufficient here.
            for item, origin in list(self._copper_drag_origin.items()):
                if item in self.track_items or item in self.via_items:
                    item.setPos(QtCore.QPointF(origin))
        finally:
            self._scene.snap_step = old_snap
        self._update_scene_rect()
        self.viewport().update()

    def cancel_interaction(self, restore=True):
        """Release transient mouse state before rehost/teardown.

        By default any uncommitted scene translation is rolled back to the true
        mouse-down transform before bookkeeping is cleared.  This guarantees
        that an interrupted gesture can never silently change the saved layout
        or poison the next undo command.
        """
        if restore and (self._drag_origin or self._copper_drag_origin):
            self._restore_uncommitted_transform()
        self._panning = False
        self._left_drag_active = False
        self._press_pos = None
        self._drag_origin = {}
        self._drag_start_rotation = {}
        self._drag_rotation = {}
        self._drag_rebase_positions = {}
        self._drag_rebase_cursor = None
        self._copper_drag_origin = {}
        self._copper_drag_before = {}
        self._direct_copper_drag_active = False
        self._direct_copper_drag_item = None
        self._last_mouse_view_pos = None
        try:
            self.unsetCursor()
        except RuntimeError:
            pass
        for widget in (self.viewport(), self):
            try:
                widget.releaseMouse()
            except RuntimeError:
                pass

    # -- painting --------------------------------------------------------
    def drawBackground(self, painter, rect):
        colors = thememod.palette(self.theme)
        painter.fillRect(rect, QtGui.QColor(colors["canvas"]))
        if not self.grid_visible:
            return

        scale = self.px_per_mm()
        # Draw the *selected* grid whenever it is at least two screen pixels
        # apart.  Older builds decimated below 6 px (0.1 mm became a visible
        # 0.5 mm grid at 40 px/mm), while the cursor still snapped to 0.1 mm.
        # That made a correctly snapped crosshair visibly fall between grid
        # lines.  Only decimate when the requested spacing is genuinely too
        # dense to render usefully.
        step = float(self.grid_step)
        while step * scale < 2.0:
            step *= 5.0 if step * 5.0 * scale >= 2.0 else 10.0
            if step > 1000:
                return

        minor = QtGui.QPen(QtGui.QColor(colors["grid"]))
        minor.setCosmetic(True)
        major = QtGui.QPen(QtGui.QColor(colors["grid_major"]))
        major.setCosmetic(True)

        # Generate positions from integer grid indices rather than repeatedly
        # adding a floating-point step.  The crosshair uses the same origin
        # (0, 0), so grid lines and snapped coordinates remain coincident even
        # after a long pan away from the origin.
        first_x = int(math.floor(rect.left() / step))
        last_x = int(math.ceil(rect.right() / step))
        first_y = int(math.floor(rect.top() / step))
        last_y = int(math.ceil(rect.bottom() / step))
        major_every = 10

        lines_minor, lines_major = [], []
        for i in range(first_x, last_x + 1):
            x = i * step
            line = QtCore.QLineF(x, rect.top(), x, rect.bottom())
            (lines_major if i % major_every == 0 else lines_minor).append(line)
        for j in range(first_y, last_y + 1):
            y = j * step
            line = QtCore.QLineF(rect.left(), y, rect.right(), y)
            (lines_major if j % major_every == 0 else lines_minor).append(line)

        painter.setPen(minor)
        painter.drawLines(lines_minor)
        painter.setPen(major)
        painter.drawLines(lines_major)

        axis = QtGui.QPen(QtGui.QColor(colors["axis"]))
        axis.setCosmetic(True)
        axis.setWidth(1)
        painter.setPen(axis)
        painter.drawLine(QtCore.QLineF(rect.left(), 0, rect.right(), 0))
        painter.drawLine(QtCore.QLineF(0, rect.top(), 0, rect.bottom()))

    def drawForeground(self, painter, rect):
        if not self.crosshair or not self.underMouse():
            return
        colors = thememod.palette(self.theme)
        p = self._cursor_scene

        if self.crosshair_mode == "full":
            style = Qt.DashLine
            lines = [
                QtCore.QLineF(rect.left(), p.y(), rect.right(), p.y()),
                QtCore.QLineF(p.x(), rect.top(), p.x(), rect.bottom()),
            ]
        else:
            # Default compact cursor: keep a constant screen-space size
            # regardless of zoom.  The configured value is the total pixel
            # span of each line; convert its half-span through the current
            # world transform.
            style = Qt.SolidLine
            transform = painter.worldTransform()
            sx = max(math.hypot(transform.m11(), transform.m12()), 1e-9)
            sy = max(math.hypot(transform.m21(), transform.m22()), 1e-9)
            half_span = 0.5 * float(self.crosshair_size_px)
            dx = half_span / sx
            dy = half_span / sy
            lines = [
                QtCore.QLineF(p.x() - dx, p.y(), p.x() + dx, p.y()),
                QtCore.QLineF(p.x(), p.y() - dy, p.x(), p.y() + dy),
            ]

        # Draw an opposite-tone halo first, then a high-contrast foreground.
        # The halo separates the cursor from copper/pads/graphics underneath,
        # while both pens remain cosmetic so visibility is stable at any zoom.
        halo = QtGui.QPen(QtGui.QColor(colors["crosshair_halo"]))
        halo.setCosmetic(True)
        halo.setWidthF(3.5)
        halo.setStyle(style)
        painter.setPen(halo)
        painter.drawLines(lines)

        pen = QtGui.QPen(QtGui.QColor(colors["crosshair"]))
        pen.setCosmetic(True)
        pen.setWidthF(1.5)
        pen.setStyle(style)
        painter.setPen(pen)
        painter.drawLines(lines)

    # -- interaction -----------------------------------------------------
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta:
            return
        factor = 1.0015 ** delta
        self._auto_fit = False
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        self.set_zoom(self.px_per_mm() * factor, pos)
        self._update_cursor_position(pos)

    def mousePressEvent(self, event):
        if self.interactive_move_enabled:
            if event.button() == Qt.LeftButton:
                raw = self.mapToScene(event.pos())
                self._interactive_move_release_pending = True
                self.moveClicked.emit(QtCore.QPointF(raw), event.modifiers())
                event.accept()
                return
            if event.button() in (Qt.RightButton, Qt.MiddleButton):
                self.moveCancelled.emit()
                event.accept()
                return
        if event.button() in (Qt.MiddleButton, Qt.RightButton):
            self._panning = True
            self._auto_fit = False
            self._pan_origin = event.pos()
            self._pan_scroll_origin = QtCore.QPoint(
                self.horizontalScrollBar().value(),
                self.verticalScrollBar().value(),
            )
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.routing_enabled:
            raw = self.mapToScene(event.pos())
            self.routeClicked.emit(QtCore.QPointF(raw), event.modifiers())
            event.accept()
            return
        self._left_drag_active = event.button() == Qt.LeftButton
        self._press_pos = event.pos()
        self._last_mouse_view_pos = QtCore.QPoint(event.pos())
        self._drag_origin = dict((item, QtCore.QPointF(item.pos())) for item in self.footprints)
        self._drag_start_rotation = dict((item, float(item.rotation())) for item in self.footprints)
        self._drag_rotation = dict(self._drag_start_rotation)
        self._drag_rebase_positions = {}
        self._drag_rebase_cursor = None
        # Routed copper is never translated by QGraphicsScene.  Direct mouse
        # dragging is an editor-owned *interactive* drag (stretch/reroute),
        # while the M hotkey is the separate physical translation operation.
        self._copper_drag_origin = {}
        self._copper_drag_before = {}
        extending = bool(event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier))
        ctrl_extend = bool(event.modifiers() & Qt.ControlModifier)
        owner, pad = self._pad_at(event.pos())
        copper_hit = None if pad is not None else self._copper_item_at(event.pos())
        footprint_hit = owner or (None if copper_hit is not None
                                  else self._footprint_at(event.pos()))
        raw_press = self.mapToScene(event.pos())
        if isinstance(copper_hit, TrackGraphicsItem):
            copper_hit.set_selected_segment(copper_hit.segment_index_at(raw_press), whole=False)

        # Ctrl+click is explicitly editor-owned so mixed footprint/track/via
        # selections behave consistently across Qt versions.  QGraphicsScene's
        # native Ctrl semantics differ subtly between bindings/styles and used
        # to make multi-selection unreliable.  A pad Ctrl+click promotes to its
        # owning footprint (KiCad-style); if footprint selection is filtered
        # out, the pad itself can still become the active fine-grained target
        # without clearing the existing scene selection.
        if event.button() == Qt.LeftButton and ctrl_extend:
            self._left_drag_active = False
            self._press_pos = None
            self._last_mouse_view_pos = None
            self._drag_origin = {}
            self._drag_start_rotation = {}
            self._drag_rotation = {}
            self._drag_rebase_positions = {}
            self._drag_rebase_cursor = None
            handled = False
            if pad is not None:
                if footprint_hit is not None and self.selection_filter_enabled("footprints"):
                    handled = self._ctrl_toggle_item(footprint_hit)
                else:
                    self.select_pad(owner, pad)
                    handled = True
            elif copper_hit is not None:
                handled = self._ctrl_toggle_item(copper_hit)
            elif footprint_hit is not None:
                handled = self._ctrl_toggle_item(footprint_hit)
            if handled:
                event.accept()
                return
            # Ctrl+clicking empty canvas intentionally leaves the selection as-is.
            event.accept()
            return

        # Routed copper uses an editor-owned drag from the first press rather
        # than relying on QGraphicsScene to select a non-movable item first.
        # This is especially important for vias: a first click-drag must move
        # the via and its attached route vertices immediately, KiCad-style.
        if event.button() == Qt.LeftButton and copper_hit is not None and not extending:
            self.select_pad(None, None)
            self._scene.blockSignals(True)
            try:
                self._scene.clearSelection()
                copper_hit.setSelected(True)
            finally:
                self._scene.blockSignals(False)
            self._on_selection()
            self._direct_copper_drag_active = True
            self._direct_copper_drag_item = copper_hit
            self._left_drag_active = False
            self._drag_origin = {}
            self._drag_start_rotation = {}
            self._drag_rotation = {}
            self._drag_rebase_positions = {}
            self._drag_rebase_cursor = None
            self.copperDragStarted.emit(copper_hit, QtCore.QPointF(raw_press), event.modifiers())
            event.accept()
            return

        # Footprints still use QGraphicsScene's native drag handling.
        super(FootprintCanvas, self).mousePressEvent(event)

        if pad is not None and not extending:
            # Pads outrank footprints. Emitting after the scene has settled lets
            # the pad readout win over the footprint one the selection change
            # would otherwise leave on screen.
            self.select_pad(owner, pad)
        else:
            # Anything else -- footprint body, silkscreen, a graphic, empty
            # canvas -- resolves to the footprint, or to nothing.
            self.select_pad(None, None)

    def mouseMoveEvent(self, event):
        if self._panning:
            # Treat the canvas like a grabbed sheet of paper.  Use the fixed
            # mouse/scrollbar state captured on press, not an incremental
            # mapToScene()/centerOn() loop.  centerOn() ultimately rounds via
            # integer scrollbars, so repeatedly mapping from the *already
            # rounded* previous frame can accumulate a small x/y error.  That
            # showed up as a slow bottom-right drift during left/right motion.
            delta = event.pos() - self._pan_origin
            self.horizontalScrollBar().setValue(
                self._pan_scroll_origin.x() - delta.x())
            self.verticalScrollBar().setValue(
                self._pan_scroll_origin.y() - delta.y())
            # Panning changes the scene coordinate underneath the stationary
            # mouse.  Update the cursor *after* the scrollbars move; otherwise
            # the crosshair/status remains at a stale scene point and appears
            # offset from the grid.
            self._update_cursor_position(event.pos())
            event.accept()
            return

        if self.interactive_move_enabled:
            raw = self.mapToScene(event.pos())
            self._last_mouse_view_pos = QtCore.QPoint(event.pos())
            self._update_cursor_position(event.pos())
            self.movePointerMoved.emit(QtCore.QPointF(raw), event.modifiers())
            event.accept()
            return

        if self._direct_copper_drag_active:
            raw = self.mapToScene(event.pos())
            self._last_mouse_view_pos = QtCore.QPoint(event.pos())
            self._update_cursor_position(event.pos())
            self.copperDragPointerMoved.emit(QtCore.QPointF(raw), event.modifiers())
            event.accept()
            return

        if self.routing_enabled:
            raw = self.mapToScene(event.pos())
            self.routePointerMoved.emit(QtCore.QPointF(raw), event.modifiers())
            event.accept()
            return

        self._last_mouse_view_pos = QtCore.QPoint(event.pos())
        self._update_cursor_position(event.pos())
        self._update_hover(event.pos())
        super(FootprintCanvas, self).mouseMoveEvent(event)
        if not self._continue_rebased_drag(event.pos()):
            self._preserve_drag_rotations()

        # QGraphicsItem positions change continuously while Qt performs a drag.
        # Publish that transient geometry as well as the final mouse-release
        # command so overlays derived from pad positions (notably the ratsnest)
        # stay attached to the moving footprints on every frame.  Undo history
        # is still recorded only by ``footprintsMoved`` on release.
        moving = [item for item, origin in self._drag_origin.items()
                  if item in self.footprints and item.pos() != origin]
        if moving:
            self.footprintsMoving.emit(moving)

    def _sync_copper_drag_to_selection(self):
        """Keep selected tracks/vias on the exact translation of the group.

        Qt moves every selected movable item, but each item can apply its own
        position constraints.  Footprints use grid snapping while routed copper
        stores absolute geometry, so a mixed selection could otherwise acquire
        a small relative offset.  Use the actual translated footprint delta as
        the authoritative group delta and mirror it onto every selected copper
        item.  A drag rebased by R/Shift+R is left alone because that transform
        currently applies to footprints only.
        """
        if self._drag_rebase_positions:
            return False
        selected_copper = [item for item in self.selected_copper_items()
                           if item in self._copper_drag_origin]
        if not selected_copper:
            return False
        delta = None
        for item in self.selected_footprints():
            origin = self._drag_origin.get(item)
            if origin is not None and item.pos() != origin:
                delta = item.pos() - origin
                break
        if delta is None:
            for item in selected_copper:
                origin = self._copper_drag_origin.get(item)
                if origin is not None and item.pos() != origin:
                    delta = item.pos() - origin
                    break
        if delta is None:
            return False
        old_snap = getattr(self._scene, "snap_step", 0.0)
        self._scene.snap_step = 0.0
        try:
            for item in selected_copper:
                origin = self._copper_drag_origin[item]
                item.setPos(QtCore.QPointF(origin.x() + delta.x(),
                                           origin.y() + delta.y()))
        finally:
            self._scene.snap_step = old_snap
        return True

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.routing_enabled:
            raw = self.mapToScene(event.pos())
            self.routeDoubleClicked.emit(QtCore.QPointF(raw), event.modifiers())
            event.accept()
            return
        super(FootprintCanvas, self).mouseDoubleClickEvent(event)

    def _preserve_drag_rotations(self):
        """Keep a translation gesture at its latest editor-owned angles."""
        if not self._drag_rotation:
            return
        for item, rotation in list(self._drag_rotation.items()):
            if item in self.footprints and abs(float(item.rotation()) - rotation) > 1e-9:
                item.setRotation(rotation)

    def _drag_cursor_scene(self, view_pos=None):
        """Return the snapped scene cursor used to continue a rebased drag."""
        pos = view_pos if view_pos is not None else self._last_mouse_view_pos
        if pos is None:
            return QtCore.QPointF(self._cursor_scene)
        return self.cursor_scene_position(pos)

    def _continue_rebased_drag(self, view_pos):
        """Continue a mouse move from the most recent keyboard transform.

        QGraphicsItem keeps its own private mouse-down translation baseline.
        After R changes a selected group's positions, a later native mouse move
        can therefore calculate from stale pre-R geometry.  We let Qt process
        the event for normal grab/selection semantics, then overwrite the
        selected footprint positions from our explicit post-R baseline.
        """
        if not self._drag_rebase_positions or self._drag_rebase_cursor is None:
            return False
        cursor = self._drag_cursor_scene(view_pos)
        delta = cursor - self._drag_rebase_cursor
        old_snap = getattr(self._scene, "snap_step", 0.0)
        self._scene.snap_step = 0.0
        try:
            for item, origin in list(self._drag_rebase_positions.items()):
                if item in self.footprints:
                    item.setPos(QtCore.QPointF(origin.x() + delta.x(),
                                               origin.y() + delta.y()))
        finally:
            self._scene.snap_step = old_snap
        self._preserve_drag_rotations()
        return True

    def rebase_drag_transform(self, items):
        """Continue an active mouse gesture from a keyboard transform.

        The immutable mouse-down snapshots are intentionally *not* changed:
        release/undo must still be able to restore the exact state from before
        the combined move/rotate gesture.  Only the live continuation baseline
        is replaced with the post-transform positions and angles.
        """
        if not self.has_active_transform_gesture():
            return False
        rebased = {}
        changed = False
        for item in list(items or []):
            if item not in self.footprints or item not in self._drag_origin:
                continue
            rebased[item] = QtCore.QPointF(item.pos())
            self._drag_rotation[item] = float(item.rotation())
            changed = True
        if changed:
            self._drag_rebase_positions = rebased
            self._drag_rebase_cursor = self._drag_cursor_scene()
        return changed

    def _commit_copper_drag(self):
        """Fold temporary track/via item translation into absolute model geometry."""
        changes = []
        old_snap = getattr(self._scene, "snap_step", 0.0)
        self._scene.snap_step = 0.0
        try:
            for item, origin in list(self._copper_drag_origin.items()):
                if item not in self.track_items and item not in self.via_items:
                    continue
                pos = QtCore.QPointF(item.pos())
                dx = float(pos.x() - origin.x())
                dy = float(pos.y() - origin.y())
                if abs(dx) < 1e-12 and abs(dy) < 1e-12:
                    continue
                before = dict(self._copper_drag_before.get(item, {}))
                track = getattr(item, "track_model", None)
                via = getattr(item, "via_model", None)
                if track is not None:
                    track.points = [(float(x) + dx, float(y) + dy)
                                    for x, y in track.points]
                elif via is not None:
                    via.x = float(via.x) + dx
                    via.y = float(via.y) + dy
                else:
                    continue
                item.setPos(origin)
                item.update_geometry()
                model = track if track is not None else via
                changes.append((item, before, model.snapshot()))
        finally:
            self._scene.snap_step = old_snap
            self._copper_drag_origin = {}
            self._copper_drag_before = {}
        if changes:
            self._update_scene_rect()
            self.viewport().update()
        return changes

    def mouseReleaseEvent(self, event):
        if self._interactive_move_release_pending and event.button() == Qt.LeftButton:
            self._interactive_move_release_pending = False
            event.accept()
            return
        if self.interactive_move_enabled:
            event.accept()
            return
        if self._direct_copper_drag_active and event.button() == Qt.LeftButton:
            raw = self.mapToScene(event.pos())
            self.copperDragFinished.emit(QtCore.QPointF(raw), event.modifiers())
            self._direct_copper_drag_active = False
            self._direct_copper_drag_item = None
            self._left_drag_active = False
            self._press_pos = None
            self._drag_origin = {}
            self._drag_start_rotation = {}
            self._drag_rotation = {}
            self._drag_rebase_positions = {}
            self._drag_rebase_cursor = None
            self._last_mouse_view_pos = None
            event.accept()
            return
        if self._panning and event.button() in (Qt.MiddleButton, Qt.RightButton):
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        super(FootprintCanvas, self).mouseReleaseEvent(event)
        self._last_mouse_view_pos = QtCore.QPoint(event.pos())
        if not self._continue_rebased_drag(event.pos()):
            self._preserve_drag_rotations()
        # Tracks/vias are non-movable in normal mouse gestures; only M-key
        # interactive movement is allowed to change their model geometry.
        copper_moved = []
        self._copper_drag_origin = {}
        self._copper_drag_before = {}
        moved = []
        for item, origin in self._drag_origin.items():
            if item not in self.footprints:
                continue
            old_rotation = float(self._drag_start_rotation.get(item, item.rotation()))
            new_rotation = float(item.rotation())
            position_changed = item.pos() != origin
            rotation_changed = abs(new_rotation - old_rotation) > 1e-9
            if position_changed or rotation_changed:
                self.footprintMoved.emit(item)
                moved.append((item, QtCore.QPointF(origin), old_rotation,
                              QtCore.QPointF(item.pos()), new_rotation))
        self._left_drag_active = False
        self._press_pos = None
        self._drag_origin = {}
        self._drag_start_rotation = {}
        self._drag_rotation = {}
        self._drag_rebase_positions = {}
        self._drag_rebase_cursor = None
        self._last_mouse_view_pos = None
        if copper_moved:
            # A mixed footprint/copper drag is one editor gesture.  Emit one
            # compound payload so undo cannot restore tracks/vias separately
            # from the footprints that moved with them.
            self.selectionMoved.emit({"footprints": moved, "copper": copper_moved})
        elif moved:
            # A mouse-held move + R/Shift+R is one compound transform gesture.
            # Report the immutable mouse-down state and final state together so
            # one Ctrl+Z restores the exact original positions and rotations.
            self.footprintsMoved.emit(moved)

    def leaveEvent(self, event):
        self._hover_info = None
        self.hoverChanged.emit(None)
        self.viewport().update()
        super(FootprintCanvas, self).leaveEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_F:
            self.fit()
        elif key == Qt.Key_G:
            self.set_grid(visible=not self.grid_visible)
        elif key == Qt.Key_A and (event.modifiers() & Qt.ControlModifier):
            self.select_all_filtered()
        elif key == Qt.Key_Escape:
            self.select_pad(None, None)
            self._scene.clearSelection()
        else:
            super(FootprintCanvas, self).keyPressEvent(event)

    def _info_at(self, view_pos):
        """Topmost interactive item under the cursor that carries metadata.

        Board-solid edges and the outside-board shade have empty hit shapes,
        so they never mask footprints or routed copper beneath them.
        """
        for item in self.items(view_pos):
            data = item.data(0)
            if not data or data.get("kind") in ("board_edge", "board_mask"):
                continue
            return data
        return None

    def _owner_of(self, item):
        """The FootprintItem a child graphic belongs to."""
        while item is not None and not isinstance(item, FootprintItem):
            item = item.parentItem()
        return item

    def _pad_at(self, view_pos):
        """The (footprint, pad) under the cursor, or ``(None, None)``.

        Pads outrank footprints, so this is checked before falling back to a
        whole-footprint hit.  Only the pad shape counts -- the number label
        painted on top of it belongs to the same pad, so it hits too.
        """
        if not self.selection_filter_enabled("pads"):
            return None, None
        for item in self.items(view_pos):
            data = item.data(0)
            if not data or data.get("kind") != "pad":
                continue
            owner = self._owner_of(item)
            if owner is not None:
                return owner, data["pad"]
        return None, None

    def _copper_item_at(self, view_pos):
        """Topmost filter-enabled routed-copper editor item under *view_pos*."""
        tracks_on = self.selection_filter_enabled("tracks")
        vias_on = self.selection_filter_enabled("vias")
        for item in self.items(view_pos):
            if tracks_on and isinstance(item, TrackGraphicsItem):
                return item
            if vias_on and isinstance(item, ViaGraphicsItem):
                return item
        return None

    def _footprint_at(self, view_pos):
        if not self.selection_filter_enabled("footprints"):
            return None
        for item in self.items(view_pos):
            owner = self._owner_of(item)
            if owner is not None:
                return owner
        return None

    def _ctrl_toggle_item(self, item):
        """Toggle one scene item while preserving every other selection."""
        if item is None:
            return False
        self.select_pad(None, None)
        self._scene.blockSignals(True)
        try:
            item.setSelected(not item.isSelected())
        finally:
            self._scene.blockSignals(False)
        self.viewport().update()
        self._on_selection()
        return True

    def selected_pad(self):
        return self._selected_pad

    def select_pad(self, item, pad):
        """Highlight ``pad`` on ``item``; pass ``None`` to clear."""
        if item is None or pad is None:
            if self._selected_pad is None:
                return
            self._selected_pad = None
            for footprint in self.footprints:
                footprint.set_selected_pad(None)
            self.viewport().update()
            self.padSelectionChanged.emit(None)
            return

        current = self._selected_pad
        if current and current["item"] is item and current["pad"] is pad:
            return
        self._selected_pad = {"item": item, "pad": pad}
        for footprint in self.footprints:
            footprint.set_selected_pad(pad.number if footprint is item else None)
        self.viewport().update()
        self.padSelectionChanged.emit(dict(self._selected_pad))

    def _update_hover(self, view_pos):
        info = self._info_at(view_pos)
        if info is not self._hover_info:
            self._hover_info = info
            self.hoverChanged.emit(info)

    def selected_footprints(self):
        """Every selected footprint, in the scene's stacking order."""
        return [item for item in self.footprints if item.isSelected()]

    def _on_selection(self):
        # The panel needs the whole footprint set, not just the topmost item.
        self.selectionChanged.emit(self.selected_footprints())
        self.copperSelectionChanged.emit(self.selected_copper_items())

    # -- helpers used by the panel ---------------------------------------
    def select_footprint(self, item):
        self.select_footprints([] if item is None else [item])

    def select_footprints(self, items):
        """Replace the selection with ``items`` (an iterable of footprint items)."""
        items = [i for i in (items or []) if i is not None]
        # Selecting footprints outright supersedes any pad highlight.
        self.select_pad(None, None)
        current = self.selected_footprints()
        if items and current == items:
            # Preserve selection while still forcing a fresh outline repaint.
            for item in items:
                item.update(item.boundingRect())
            self.viewport().update()
            return
        # QGraphicsScene emits selectionChanged once per item touched.  Letting
        # those intermediate states through means the panel briefly sees a
        # one-item selection and reacts to it -- which, among other things,
        # moved the active footprint to whichever item happened to be first.
        # Mutate quietly, then announce the finished selection once.
        self._scene.blockSignals(True)
        try:
            self._scene.clearSelection()
            for item in items:
                item.setSelected(True)
                item.update(item.boundingRect())
        finally:
            self._scene.blockSignals(False)
        self.viewport().update()
        self._on_selection()

    def select_all_footprints(self):
        self.select_footprints(list(self.footprints)
                               if self.selection_filter_enabled("footprints") else [])

    def select_all_filtered(self):
        """Select every scene object admitted by the current selection filter."""
        footprints = list(self.footprints) if self.selection_filter_enabled("footprints") else []
        copper = []
        if self.selection_filter_enabled("tracks"):
            copper.extend(self.track_items)
        if self.selection_filter_enabled("vias"):
            copper.extend(self.via_items)
        self.select_editor_items(footprints, copper)

    def highlight_pad(self, footprint_item, pad_number):
        """Select a pad from outside the canvas and centre the view on it."""
        item = footprint_item.pad_items.get(str(pad_number))
        if item is None:
            return
        pad = (item.data(0) or {}).get("pad")
        if pad is not None:
            self.select_pad(footprint_item, pad)
        self.centerOn(item)
        self.viewport().update()
