# -*- coding: utf-8 -*-
"""Document-free 3D preview for the custom footprint editor.

FreeCAD 1.1.3 on Windows can crash inside Pivy/Quarter's ``QOpenGLWidget``
while the add-on is hosted in FreeCAD's MDI area.  A Python exception cannot
recover from that native access violation, so the preview intentionally avoids
Pivy/Quarter and its viewer stack.

The current renderer uses a **direct Qt OpenGL widget** when FreeCAD exposes
QtOpenGL/QtOpenGLWidgets.  It deliberately does not use Pivy/Quarter: the old
Quarter path was the native crash boundary.  Mesh projection, depth testing and
rasterisation therefore run on the GPU, while a QPainter software canvas remains
as an automatic compatibility fallback.  STEP models are still read through
FreeCAD's ``Part`` module and ``Shape.tessellate`` without creating an
``App::Document``.
"""

import math
import os

from .qt import Qt, QtCore, QtGui, QtWidgets, Signal, enum
from .layers import layer_color, outline_hex, hex_to_rgb
from . import stepcolor
from . import theme as thememod

__all__ = ["Board3DView", "board_bounds", "layer_heights"]

SIZE_IGNORED = enum(QtWidgets.QSizePolicy, "Policy", "Ignored")
SIZE_PREFERRED = enum(QtWidgets.QSizePolicy, "Policy", "Preferred")
SIZE_EXPANDING = enum(QtWidgets.QSizePolicy, "Policy", "Expanding")
SIZE_FIXED = enum(QtWidgets.QSizePolicy, "Policy", "Fixed")

# Materials, matching the reference web viewer's three.js scene.
BOARD_BLOCK = "#BFC7D2"        # the translucent block the design sits in
BOARD_BLOCK_ALPHA = 0.14
PLANE_EDGE = "#8894A6"         # thin outline marking each reference plane
MODEL_NEUTRAL = "#BFC5CD"      # model face with no usable STEP colour
MODEL_BOX_EDGE = "#3A4354"     # edges of a component drawn as its bounding box
COPPER_METAL = "#D4AF37"       # one physical copper finish for every 3D conductor
PAD_GOLD = COPPER_METAL          # compatibility name used by the pad builder


def _rgb01_to_hex(rgb):
    """Convert a 0..1 RGB triple from a STEP file to a #rrggbb string."""
    return "#%02X%02X%02X" % tuple(
        max(0, min(255, int(round(float(v) * 255.0)))) for v in rgb[:3])


# ---------------------------------------------------------------------------
# Small vector helpers -- tuples are faster/cheaper than introducing numpy.
# ---------------------------------------------------------------------------

def _vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vmul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length(v):
    return math.sqrt(max(0.0, _dot(v, v)))


def _norm(v, fallback=(0.0, 0.0, 1.0)):
    n = _length(v)
    if n < 1e-12:
        return fallback
    return (v[0] / n, v[1] / n, v[2] / n)


def _rot_z(p, angle_deg):
    a = math.radians(float(angle_deg or 0.0))
    c, s = math.cos(a), math.sin(a)
    return (p[0] * c - p[1] * s,
            p[0] * s + p[1] * c,
            p[2])


def _rot_xyz(p, rx, ry, rz):
    """Rotate around X, then Y, then Z (degrees)."""
    x, y, z = p
    ax, ay, az = map(math.radians, (float(rx or 0.0), float(ry or 0.0), float(rz or 0.0)))
    cx, sx = math.cos(ax), math.sin(ax)
    y, z = y * cx - z * sx, y * sx + z * cx
    cy, sy = math.cos(ay), math.sin(ay)
    x, z = x * cy + z * sy, -x * sy + z * cy
    cz, sz = math.cos(az), math.sin(az)
    x, y = x * cz - y * sz, x * sz + y * cz
    return (x, y, z)


def _rotate_about_axis(v, axis, angle):
    """Rotate vector ``v`` around ``axis`` by ``angle`` radians.

    Rodrigues rotation keeps the orbit camera entirely in vector space, so it
    can cross the old +/-89 degree pitch boundary without a singularity or a
    sudden change of the camera's up direction.
    """
    axis = _norm(axis, (0.0, 0.0, 1.0))
    c, sn = math.cos(float(angle)), math.sin(float(angle))
    return _vadd(_vadd(_vmul(v, c), _vmul(_cross(axis, v), sn)),
                 _vmul(axis, _dot(axis, v) * (1.0 - c)))


def _camera_up_for_direction(direction, preferred=(0.0, 0.0, 1.0)):
    """Return a stable screen-up vector perpendicular to ``direction``."""
    d = _norm(direction)
    up = _vsub(preferred, _vmul(d, _dot(preferred, d)))
    if _length(up) < 1e-8:
        fallback = (0.0, 1.0, 0.0)
        up = _vsub(fallback, _vmul(d, _dot(fallback, d)))
    return _norm(up, (0.0, 1.0, 0.0))


# KiCad's 3D viewer uses the classic SGI/Gavin Bell virtual trackball.  Keep
# the same screen normalisation and 0.8 trackball radius so left-drag has the
# same "grab the whole board and roll it" feel instead of behaving like two
# independent yaw/pitch sliders.  See KiCad TRACK_BALL::Drag().
_KICAD_TRACKBALL_SIZE = 0.8


def _trackball_project_to_sphere(radius, x, y):
    """Project a normalised screen point onto the SGI virtual trackball."""
    d = math.sqrt(x * x + y * y)
    threshold = float(radius) * 0.7071067811865476  # radius / sqrt(2)
    if d < threshold:
        return math.sqrt(max(0.0, radius * radius - d * d))
    t = radius / 1.4142135623730951
    return (t * t) / max(d, 1e-15)


def _kicad_trackball_delta(p1x, p1y, p2x, p2y, radius=_KICAD_TRACKBALL_SIZE):
    """Return ``(axis, angle)`` for a KiCad/SGI trackball mouse increment.

    Coordinates are in KiCad's normalised screen space (-1..+1), with +Y up.
    The returned axis is in camera/screen coordinates: +X right, +Y up and
    +Z towards the viewer.
    """
    if abs(p1x - p2x) < 1e-15 and abs(p1y - p2y) < 1e-15:
        return (0.0, 0.0, 1.0), 0.0
    p1 = (float(p1x), float(p1y),
          _trackball_project_to_sphere(radius, float(p1x), float(p1y)))
    p2 = (float(p2x), float(p2y),
          _trackball_project_to_sphere(radius, float(p2x), float(p2y)))
    # This cross-product order is the one used by KiCad's inherited SGI
    # trackball implementation (vcross(a, p2, p1)).
    axis = _cross(p2, p1)
    if _length(axis) < 1e-15:
        return (0.0, 0.0, 1.0), 0.0
    d = _vsub(p1, p2)
    t = _length(d) / (2.0 * float(radius))
    t = max(-1.0, min(1.0, t))
    return _norm(axis), 2.0 * math.asin(t)


def _normalise_trackball_point(point, width, height):
    """Normalise a Qt mouse point exactly like KiCad TRACK_BALL::Drag()."""
    w = max(1.0, float(width)); h = max(1.0, float(height))
    return ((2.0 * float(point.x()) - w) / w,
            (h - 2.0 * float(point.y())) / h)


def _rotated_points(bbox, angle_deg):
    x0, y0, x1, y1 = bbox
    a = math.radians(float(angle_deg or 0.0))
    c, s = math.cos(a), math.sin(a)
    result = []
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        result.append((x * c - y * s, x * s + y * c))
    return result


from .meshgeom import (  # noqa: F401  (re-exported for compatibility)
    _polygon_area, _point_in_triangle_2d, _triangulate_polygon, _loop_perimeter,
    _resample_loop, _aligned_loop_pair, _sorted_loops, _interpolate_loop_sets,
)


def board_bounds(entries, solid=None, margin=2.0):
    """Return 2D (Y-down) board bounds of the board solid or the footprints."""
    if solid is not None and hasattr(solid, "board_bounds"):
        try:
            return tuple(solid.board_bounds())
        except Exception:
            pass
    boxes = []
    for entry in entries or []:
        fp = entry.get("fp")
        if fp is None:
            continue
        pos = entry.get("pos", (0.0, 0.0))
        if hasattr(pos, "x"):
            px, py = float(pos.x()), float(pos.y())
        else:
            px, py = float(pos[0]), float(pos[1])
        pts = _rotated_points(fp.bbox(), entry.get("rotation", 0.0))
        xs = [px + p[0] for p in pts]
        ys = [py + p[1] for p in pts]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    if boxes:
        return (min(b[0] for b in boxes) - margin,
                min(b[1] for b in boxes) - margin,
                max(b[2] for b in boxes) + margin,
                max(b[3] for b in boxes) + margin)
    return (-10.0, -10.0, 10.0, 10.0)


def layer_heights(stack):
    """Map copper reference layer id -> reference-plane Z, plus top plane Z.

    ``z = 0`` is the XY plane through the board solid's lowest point.  All
    design copper layers are reference planes; copper, pads and routed tracks
    are etched downward from their plane, so copper thickness never changes
    the distance between planes.  The second value is the height of the top
    reference plane (there is no top margin any more — the solid decides
    where the board ends).
    """
    heights = stack.reference_heights()
    top = max(heights.values()) if heights else float(stack.bottom_margin)
    return heights, top


def _rgb(color):
    if isinstance(color, str):
        return hex_to_rgb(color)
    return tuple(color)


def _circle_points(cx, cy, radius, count=48):
    return [(cx + radius * math.cos(2.0 * math.pi * i / count),
             cy + radius * math.sin(2.0 * math.pi * i / count))
            for i in range(count + 1)]


def _arc_points(arc, count=32):
    (ax, ay), (bx, by), (cx, cy) = arc.start, arc.mid, arc.end
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [(ax, ay), (cx, cy)]
    ux = (((ax * ax + ay * ay) * (by - cy) +
           (bx * bx + by * by) * (cy - ay) +
           (cx * cx + cy * cy) * (ay - by)) / d)
    uy = (((ax * ax + ay * ay) * (cx - bx) +
           (bx * bx + by * by) * (ax - cx) +
           (cx * cx + cy * cy) * (bx - ax)) / d)
    r = math.hypot(ax - ux, ay - uy)
    a0 = math.atan2(ay - uy, ax - ux)
    am = math.atan2(by - uy, bx - ux)
    a1 = math.atan2(cy - uy, cx - ux)

    def norm(a):
        while a < 0:
            a += 2 * math.pi
        while a >= 2 * math.pi:
            a -= 2 * math.pi
        return a

    a0, am, a1 = norm(a0), norm(am), norm(a1)
    ccw_end = a1
    while ccw_end < a0:
        ccw_end += 2 * math.pi
    ccw_mid = am
    while ccw_mid < a0:
        ccw_mid += 2 * math.pi
    if ccw_mid <= ccw_end + 1e-9:
        sweep = ccw_end - a0
    else:
        sweep = -(2 * math.pi - (ccw_end - a0))
    return [(ux + r * math.cos(a0 + sweep * i / count),
             uy + r * math.sin(a0 + sweep * i / count))
            for i in range(count + 1)]


# ---------------------------------------------------------------------------
# Lighting
# ---------------------------------------------------------------------------
# The same rig the reference web viewer sets up in three.js, re-expressed for a
# Z-up world: a sky/ground hemisphere for ambient fill, a bright key from above,
# a cool side fill, and a dim upward fill so the underside of the board is not
# solid black when you orbit beneath it.  Directions point *towards* the light.
_HEMI_SKY = (0.867, 0.894, 0.941)      # 0xdde4f0
_HEMI_GROUND = (0.604, 0.643, 0.706)   # 0x9AA4B4
_HEMI_INTENSITY = 0.95

_DIR_LIGHTS = (
    (_norm((30.0, -40.0, 60.0)), (1.0, 1.0, 1.0), 1.45),        # key, above
    (_norm((-40.0, 30.0, 25.0)), (0.667, 0.733, 0.867), 0.45),  # cool side fill
    (_norm((-20.0, 25.0, -50.0)), (1.0, 1.0, 1.0), 0.55),     # up-fill from below
)

# Scales the rig so a fully key-lit surface lands just above 1.0 and a
# downward-facing one near 0.54.  Higher values blow every up-facing surface out
# to pure white, which flattens the board and the tops of the packages into one
# silhouette; lower values lose the sense of a light from above.
_EXPOSURE = 0.52


def _shade_rgb(normal):
    """Per-channel light multiplier for a surface with this normal.

    Returns a triple rather than a scalar so the tinted fills colour the
    surface, which is what stops every model reading as the same flat grey.
    """
    # Hemisphere: sky above, ground below, blended by how far the normal tilts.
    t = 0.5 * (normal[2] + 1.0)
    r = (_HEMI_GROUND[0] + (_HEMI_SKY[0] - _HEMI_GROUND[0]) * t) * _HEMI_INTENSITY
    g = (_HEMI_GROUND[1] + (_HEMI_SKY[1] - _HEMI_GROUND[1]) * t) * _HEMI_INTENSITY
    b = (_HEMI_GROUND[2] + (_HEMI_SKY[2] - _HEMI_GROUND[2]) * t) * _HEMI_INTENSITY
    for direction, color, intensity in _DIR_LIGHTS:
        lambert = _dot(normal, direction)
        if lambert <= 0.0:
            continue
        k = lambert * intensity
        r += color[0] * k
        g += color[1] * k
        b += color[2] * k
    return (r * _EXPOSURE, g * _EXPOSURE, b * _EXPOSURE)


def _srgb_to_linear(v):
    v = max(0.0, min(1.0, float(v)))
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(v):
    v = max(0.0, float(v))
    return 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1.0 / 2.4)) - 0.055


def _lit_rgba(color, alpha, normal):
    """Return an sRGB RGBA tuple after the reference viewer's light rig.

    three.js does lighting in linear colour space and converts to sRGB for the
    display.  The old software preview multiplied already-gamma-encoded QColor
    channels, which made dark package bodies too dark and shifted layer colours.
    """
    r, g, b = _rgb(color)
    if max(abs(r), abs(g), abs(b)) > 1.000001:
        r, g, b = r / 255.0, g / 255.0, b / 255.0
    light = _shade_rgb(_norm(normal))
    out = []
    for channel, mul in zip((r, g, b), light):
        out.append(max(0.0, min(1.0, _linear_to_srgb(_srgb_to_linear(channel) * mul))))
    return (out[0], out[1], out[2], max(0.0, min(1.0, float(alpha))))


def _base_rgba(color, alpha=1.0):
    """Return an unlit sRGB colour for LineBasicMaterial-style overlays."""
    r, g, b = _rgb(color)
    if max(abs(r), abs(g), abs(b)) > 1.000001:
        r, g, b = r / 255.0, g / 255.0, b / 255.0
    return (max(0.0, min(1.0, r)), max(0.0, min(1.0, g)),
            max(0.0, min(1.0, b)), max(0.0, min(1.0, float(alpha))))


# ---------------------------------------------------------------------------
# QPainter software 3D canvas
# ---------------------------------------------------------------------------

class _Software3DCanvas(QtWidgets.QWidget):
    """Perspective fallback canvas used when direct Qt OpenGL is unavailable."""

    selectionRequested = Signal(object)
    cameraChanged = Signal()

    def __init__(self, parent=None, theme="light"):
        super(_Software3DCanvas, self).__init__(parent)
        self.theme = theme
        self.triangles = []       # (p0,p1,p2,color,alpha)
        self.lines = []           # ([points], color, width)
        self.scene_bounds = ((-1, -1, -1), (1, 1, 1))
        self.target = (0.0, 0.0, 0.0)
        self.direction = _norm((1.0, -1.0, 0.85))  # target -> camera
        self.camera_up = _camera_up_for_direction(self.direction)
        self.distance = 10.0
        self.fov_deg = 42.0
        self._drag_mode = None
        self._last_mouse = None
        self._press_mouse = None
        self._drag_distance = 0.0
        self.owner_bounds = {}
        self.owner_triangles = {}
        self.selected_uid = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        try:
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        except Exception:
            pass

    def set_theme(self, theme):
        self.theme = theme if theme in ("dark", "light") else "light"
        self.update()

    def set_scene(self, triangles, lines, bounds, owner_bounds=None, owner_triangles=None):
        self.triangles = triangles or []
        self.lines = lines or []
        self.scene_bounds = bounds
        self.owner_bounds = dict(owner_bounds or {})
        self.owner_triangles = dict(owner_triangles or {})
        self.update()

    def set_selected(self, uid):
        self.selected_uid = uid
        self.update()

    @staticmethod
    def _box_edges(bounds):
        lo, hi = bounds
        p = [(lo[0], lo[1], lo[2]), (hi[0], lo[1], lo[2]),
             (hi[0], hi[1], lo[2]), (lo[0], hi[1], lo[2]),
             (lo[0], lo[1], hi[2]), (hi[0], lo[1], hi[2]),
             (hi[0], hi[1], hi[2]), (lo[0], hi[1], hi[2])]
        pairs = ((0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),
                 (0,4),(1,5),(2,6),(3,7))
        return [(p[a], p[b]) for a, b in pairs]

    def _screen_ray(self, point):
        if self.width() <= 1 or self.height() <= 1:
            return None
        eye, right, up, forward = self._camera_basis()
        x = (2.0 * float(point.x()) / float(self.width()) - 1.0)
        y = (1.0 - 2.0 * float(point.y()) / float(self.height()))
        tanv = math.tan(math.radians(self.fov_deg) * 0.5)
        aspect = float(self.width()) / max(1.0, float(self.height()))
        direction = _norm(_vadd(forward, _vadd(_vmul(right, x * tanv * aspect),
                                                _vmul(up, y * tanv))))
        return eye, direction

    @staticmethod
    def _ray_box(origin, direction, bounds):
        lo, hi = bounds
        tmin, tmax = -float("inf"), float("inf")
        for i in range(3):
            o, d = origin[i], direction[i]
            if abs(d) < 1e-12:
                if o < lo[i] or o > hi[i]:
                    return None
                continue
            a, b = (lo[i] - o) / d, (hi[i] - o) / d
            if a > b:
                a, b = b, a
            tmin, tmax = max(tmin, a), min(tmax, b)
            if tmax < tmin:
                return None
        if tmax < 0.0:
            return None
        return tmin if tmin >= 0.0 else tmax

    @staticmethod
    def _ray_triangle(origin, direction, tri, eps=1e-9):
        """Moller-Trumbore ray/triangle intersection; return distance or None."""
        a, b, c = tri
        edge1 = _vsub(b, a); edge2 = _vsub(c, a)
        h = _cross(direction, edge2)
        det = _dot(edge1, h)
        if abs(det) < eps:
            return None
        inv = 1.0 / det
        q = _vsub(origin, a)
        u = inv * _dot(q, h)
        if u < -eps or u > 1.0 + eps:
            return None
        r = _cross(q, edge1)
        v = inv * _dot(direction, r)
        if v < -eps or u + v > 1.0 + eps:
            return None
        t = inv * _dot(edge2, r)
        return t if t >= 0.0 else None

    def pick_uid(self, point):
        """Pick actual footprint/model surfaces, then fall back to its 3D box.

        The web reference raycasts the meshes rather than selecting any empty
        space inside an AABB.  We do the same for every footprint that has
        triangles; the box fallback keeps line-only/model-less footprints
        selectable without making normal packages feel overly generous.
        """
        ray = self._screen_ray(point)
        if ray is None:
            return None
        origin, direction = ray
        candidates = []
        for uid, bounds in self.owner_bounds.items():
            box_t = self._ray_box(origin, direction, bounds)
            if box_t is not None:
                candidates.append((box_t, uid))
        candidates.sort()
        best_uid, best_t = None, float("inf")
        for box_t, uid in candidates:
            if box_t > best_t:
                break
            tris = self.owner_triangles.get(uid) or []
            if tris:
                for tri in tris:
                    t = self._ray_triangle(origin, direction, tri)
                    if t is not None and t < best_t:
                        best_uid, best_t = uid, t
            elif box_t < best_t:
                best_uid, best_t = uid, box_t
        return best_uid

    def _camera_basis(self):
        d = _norm(self.direction)           # target -> camera
        eye = _vadd(self.target, _vmul(d, self.distance))
        forward = _vmul(d, -1.0)            # camera -> target

        # Keep a persistent camera-up vector.  The old implementation switched
        # between world Z and world Y when the camera approached a pole.  That
        # discontinuous basis is why the first orbit after Top/Bottom appeared
        # to jump to a different view.  Re-orthogonalising the stored up vector
        # is continuous through every orientation, including upside-down views.
        up = getattr(self, "camera_up", None) or _camera_up_for_direction(d)
        up = _vsub(up, _vmul(d, _dot(up, d)))
        if _length(up) < 1e-8:
            up = _camera_up_for_direction(d)
        up = _norm(up, (0.0, 1.0, 0.0))
        right = _norm(_cross(forward, up), (1.0, 0.0, 0.0))
        up = _norm(_cross(right, forward), up)
        return eye, right, up, forward

    def _project(self, p, basis, focal):
        eye, right, up, forward = basis
        v = _vsub(p, eye)
        z = _dot(v, forward)
        if z <= max(1e-4, self.distance * 1e-5):
            return None
        x = _dot(v, right)
        y = _dot(v, up)
        return (self.width() * 0.5 + focal * x / z,
                self.height() * 0.5 - focal * y / z,
                z)

    @staticmethod
    def _qcolor(color, alpha=1.0, shade=1.0):
        """Return a real QColor without going through QVariant conversion.

        FreeCAD 1.1's ``PySide`` compatibility layer is stricter than stock
        PySide6 here: ``QColor((r, g, b))`` is treated as a QVariant overload
        and raises ``QVariant must be holding a QColor``.  Layer colours in the
        viewer are float RGB tuples, so convert every supported representation
        explicitly to integer channels before constructing QColor.

        ``shade`` is either a scalar or an (r, g, b) triple.  The triple form
        lets tinted lights colour a surface the way the reference viewer's
        hemisphere light does, instead of only brightening it.
        """
        try:
            if isinstance(color, QtGui.QColor):
                r, g, b, source_alpha = color.getRgb()
            elif isinstance(color, str):
                text = color.strip()
                if len(text) == 7 and text.startswith("#"):
                    r, g, b = int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
                    source_alpha = 255
                elif len(text) == 9 and text.startswith("#"):
                    # Qt's #AARRGGBB notation.
                    source_alpha = int(text[1:3], 16)
                    r, g, b = int(text[3:5], 16), int(text[5:7], 16), int(text[7:9], 16)
                else:
                    c = QtGui.QColor()
                    c.setNamedColor(text)
                    if hasattr(c, "isValid") and not c.isValid():
                        c = QtGui.QColor(128, 128, 128)
                    r, g, b, source_alpha = c.getRgb()
            elif isinstance(color, (tuple, list)) and len(color) >= 3:
                vals = [float(color[0]), float(color[1]), float(color[2])]
                # layer_color() returns 0..1 floats; also accept 0..255 tuples.
                if max(abs(v) for v in vals) <= 1.000001:
                    vals = [v * 255.0 for v in vals]
                r, g, b = [max(0, min(255, int(round(v)))) for v in vals]
                source_alpha = 255
            else:
                # Avoid QColor(color): on FreeCAD's shim an arbitrary Python
                # value can select the QVariant overload and fail at paint time.
                c = QtGui.QColor(str(color))
                if hasattr(c, "isValid") and not c.isValid():
                    c = QtGui.QColor(128, 128, 128)
                r, g, b, source_alpha = c.getRgb()
        except Exception:
            r, g, b, source_alpha = 128, 128, 128, 255

        if isinstance(shade, (tuple, list)):
            sr, sg, sb = (float(shade[0]), float(shade[1]), float(shade[2]))
        else:
            sr = sg = sb = float(shade)
        r = max(0, min(255, int(round(_linear_to_srgb(_srgb_to_linear(r / 255.0) * sr) * 255.0))))
        g = max(0, min(255, int(round(_linear_to_srgb(_srgb_to_linear(g / 255.0) * sg) * 255.0))))
        b = max(0, min(255, int(round(_linear_to_srgb(_srgb_to_linear(b / 255.0) * sb) * 255.0))))
        a = max(0, min(255, int(round(source_alpha * float(alpha)))))
        return QtGui.QColor(r, g, b, a)

    @classmethod
    def _qpen(cls, color, width):
        """Construct QPen through setters, avoiding fragile overload dispatch."""
        pen = QtGui.QPen()
        pen.setColor(cls._qcolor(color))
        try:
            pen.setWidthF(float(width))
        except Exception:
            pen.setWidth(max(1, int(round(float(width)))))
        pen.setCosmetic(True)
        return pen

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            bg = thememod.palette(self.theme)["canvas"]
            painter.fillRect(self.rect(), self._qcolor(bg))
            if self.width() <= 1 or self.height() <= 1:
                return

            focal = 0.5 * max(1, self.height()) / math.tan(math.radians(self.fov_deg) * 0.5)
            basis = self._camera_basis()
            eye = basis[0]
            opaque = []
            blended = []
            for p0, p1, p2, color, alpha in self.triangles:
                q0 = self._project(p0, basis, focal)
                q1 = self._project(p1, basis, focal)
                q2 = self._project(p2, basis, focal)
                if q0 is None or q1 is None or q2 is None:
                    continue
                n = _norm(_cross(_vsub(p1, p0), _vsub(p2, p0)))
                # Two-sided lighting rather than back-face culling: the box and
                # cylinder builders do not guarantee outward winding, so culling
                # would punch holes in solids.  Flipping the normal towards the
                # camera shades either winding correctly.
                if _dot(n, _vsub(p0, eye)) > 0.0:
                    n = (-n[0], -n[1], -n[2])
                depth = (q0[2] + q1[2] + q2[2]) / 3.0
                entry = (depth, (q0, q1, q2), color, alpha, _shade_rgb(n))
                (opaque if alpha >= 0.999 else blended).append(entry)

            # The reference web viewer gives the translucent board block
            # renderOrder=-1 and depthWrite=false: it is laid down first, then
            # opaque pads/models retain their real colours on top.  Drawing the
            # block last in the old QPainter path washed black packages pale.
            opaque.sort(key=lambda item: item[0], reverse=True)
            blended.sort(key=lambda item: item[0], reverse=True)
            painter.setPen(Qt.NoPen)
            for _depth, pts, color, alpha, shade in blended + opaque:
                poly = QtGui.QPolygonF([QtCore.QPointF(pts[0][0], pts[0][1]),
                                        QtCore.QPointF(pts[1][0], pts[1][1]),
                                        QtCore.QPointF(pts[2][0], pts[2][1])])
                painter.setBrush(QtGui.QBrush(self._qcolor(color, alpha, shade)))
                painter.drawPolygon(poly)

            # Lines are intentionally painted after surfaces: footprint graphics
            # and Outline stay readable like the reference viewer's overlays.
            for points, color, width in self.lines:
                projected = []
                for p in points:
                    q = self._project(p, basis, focal)
                    if q is None:
                        if len(projected) >= 2:
                            painter.setPen(self._qpen(color, width))
                            painter.setBrush(Qt.NoBrush)
                            painter.drawPolyline(QtGui.QPolygonF(projected))
                        projected = []
                        continue
                    projected.append(QtCore.QPointF(q[0], q[1]))
                if len(projected) >= 2:
                    painter.setPen(self._qpen(color, width))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawPolyline(QtGui.QPolygonF(projected))

            bounds = self.owner_bounds.get(self.selected_uid)
            if bounds is not None:
                painter.setPen(self._qpen("#5C7CFA", 1.5))
                painter.setBrush(Qt.NoBrush)
                for a, b in self._box_edges(bounds):
                    qa = self._project(a, basis, focal); qb = self._project(b, basis, focal)
                    if qa is not None and qb is not None:
                        painter.drawLine(QtCore.QPointF(qa[0], qa[1]),
                                         QtCore.QPointF(qb[0], qb[1]))
        finally:
            # An exception from a Qt paint overload must never leave a live
            # painter attached to the backing store.  That was the source of the
            # secondary QBackingStore::endPaint() warnings in 0.6.2.
            if painter.isActive():
                painter.end()

    def fit_scene(self):
        lo, hi = self.scene_bounds
        self.target = ((lo[0] + hi[0]) * 0.5,
                       (lo[1] + hi[1]) * 0.5,
                       (lo[2] + hi[2]) * 0.5)
        dx, dy, dz = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
        radius = max(0.5, 0.5 * math.sqrt(dx * dx + dy * dy + dz * dz))
        self.distance = max(1.0, radius / math.tan(math.radians(self.fov_deg) * 0.5) * 1.22)
        self.update()

    def set_view(self, name):
        # Every preset carries an explicit screen-up direction.  Orbiting starts
        # from that exact camera frame instead of reconstructing an up vector
        # from the direction on the next mouse move.
        views = {
            "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
            "bottom": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
            "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
            "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            "iso": ((1.0, -1.0, 0.85), (0.0, 0.0, 1.0)),
        }
        direction, preferred_up = views.get(name, views["iso"])
        self.direction = _norm(direction)
        self.camera_up = _camera_up_for_direction(self.direction, preferred_up)
        self.fit_scene()
        self.cameraChanged.emit()

    def mousePressEvent(self, event):
        button = event.button()
        if button == Qt.LeftButton:
            self._drag_mode = "orbit"
        elif button in (Qt.RightButton, Qt.MiddleButton):
            self._drag_mode = "pan"
        else:
            self._drag_mode = None
        if self._drag_mode:
            self._last_mouse = event.position() if hasattr(event, "position") else event.pos()
            self._press_mouse = QtCore.QPointF(self._last_mouse)
            self._drag_distance = 0.0
            event.accept(); return
        QtWidgets.QWidget.mousePressEvent(self, event)

    def mouseMoveEvent(self, event):
        if not self._drag_mode or self._last_mouse is None:
            QtWidgets.QWidget.mouseMoveEvent(self, event); return
        now = event.position() if hasattr(event, "position") else event.pos()
        previous = self._last_mouse
        dx = float(now.x() - previous.x())
        dy = float(now.y() - previous.y())
        self._drag_distance += math.hypot(dx, dy)
        self._last_mouse = now
        if self._drag_mode == "orbit":
            # Match KiCad's TRACK_BALL::Drag(): map the previous/current mouse
            # positions onto a virtual sphere/hyperbola and apply that single
            # screen-space rotation to the current camera frame.  This makes the
            # complete slab feel as if it is directly under the mouse rather
            # than decomposing the gesture into yaw and pitch.  The trackball
            # remains unclamped for a full 360-degree orbit.
            p1x, p1y = _normalise_trackball_point(previous, self.width(), self.height())
            p2x, p2y = _normalise_trackball_point(now, self.width(), self.height())
            axis_screen, angle = _kicad_trackball_delta(p1x, p1y, p2x, p2y)
            if abs(angle) > 1e-15:
                eye, right, up, _forward = self._camera_basis()
                d = _norm(self.direction)
                # SGI trackball +Z points out of the screen, i.e. from the
                # target towards the camera.  Convert that camera-space axis to
                # a world-space axis before rotating the complete camera frame.
                axis_world = _norm(_vadd(_vadd(_vmul(right, axis_screen[0]),
                                                _vmul(up, axis_screen[1])),
                                         _vmul(d, axis_screen[2])),
                                   (0.0, 0.0, 1.0))
                d = _norm(_rotate_about_axis(d, axis_world, angle))
                up = _norm(_rotate_about_axis(up, axis_world, angle), up)
                up = _norm(_vsub(up, _vmul(d, _dot(up, d))),
                           _camera_up_for_direction(d))
                self.direction = d
                self.camera_up = up
                self.cameraChanged.emit()
        else:
            _eye, right, up, _forward = self._camera_basis()
            scale = self.distance * 2.0 * math.tan(math.radians(self.fov_deg) * 0.5) / max(1.0, float(self.height()))
            self.target = _vadd(self.target,
                                _vadd(_vmul(right, -dx * scale), _vmul(up, dy * scale)))
        self.update(); event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_mode:
            was_left = event.button() == Qt.LeftButton
            point = event.position() if hasattr(event, "position") else event.pos()
            click = was_left and self._drag_distance <= 4.0
            self._drag_mode = None
            self._last_mouse = None
            self._press_mouse = None
            if click:
                self.selectionRequested.emit(self.pick_uid(point))
            event.accept(); return
        QtWidgets.QWidget.mouseReleaseEvent(self, event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() if hasattr(event, "angleDelta") else event.delta()
        if delta:
            self.distance *= math.exp(-float(delta) / 120.0 * 0.14)
            self.distance = max(0.05, min(1e6, self.distance))
            self.update(); event.accept(); return
        QtWidgets.QWidget.wheelEvent(self, event)


# ---------------------------------------------------------------------------
# Direct Qt OpenGL renderer
# ---------------------------------------------------------------------------
# Pivy/Quarter is intentionally not involved.  This is a plain QOpenGLWidget
# owned by the add-on.  If QtOpenGL is missing the viewer keeps using the
# QPainter fallback above.
_GL_AVAILABLE = False
_QOpenGLWidget = None
_QOpenGLShaderProgram = None
_QOpenGLShader = None
_QOpenGLBuffer = None
try:
    # FreeCAD 1.1 uses a PySide compatibility package around Qt6.  Qt6 places
    # QOpenGLWidget in QtOpenGLWidgets and the buffer/shader helpers in
    # QtOpenGL.  The alternate lookups keep the development PySide2/Qt5 path
    # working too without loading Pivy.
    import importlib
    _binding_roots = ("PySide", "PySide6", "PySide2")
    _ogl_mod = None
    _oglw_mod = None
    for _root in _binding_roots:
        try:
            _oglw_mod = importlib.import_module(_root + ".QtOpenGLWidgets")
        except Exception:
            _oglw_mod = None
        try:
            _ogl_mod = importlib.import_module(_root + ".QtOpenGL")
        except Exception:
            _ogl_mod = None
        candidate = getattr(_oglw_mod, "QOpenGLWidget", None) if _oglw_mod else None
        if candidate is None:
            candidate = getattr(QtWidgets, "QOpenGLWidget", None)
        program = getattr(_ogl_mod, "QOpenGLShaderProgram", None) if _ogl_mod else None
        shader = getattr(_ogl_mod, "QOpenGLShader", None) if _ogl_mod else None
        buffer = getattr(_ogl_mod, "QOpenGLBuffer", None) if _ogl_mod else None
        program = program or getattr(QtGui, "QOpenGLShaderProgram", None)
        shader = shader or getattr(QtGui, "QOpenGLShader", None)
        buffer = buffer or getattr(QtGui, "QOpenGLBuffer", None)
        if candidate and program and shader and buffer:
            _QOpenGLWidget = candidate
            _QOpenGLShaderProgram = program
            _QOpenGLShader = shader
            _QOpenGLBuffer = buffer
            _GL_AVAILABLE = True
            break
except Exception:
    _GL_AVAILABLE = False


def _shader_enum(name):
    value = getattr(_QOpenGLShader, name, None) if _QOpenGLShader is not None else None
    if value is not None:
        return value
    scope = getattr(_QOpenGLShader, "ShaderTypeBit", None)
    return getattr(scope, name) if scope is not None else None


def _buffer_enum(name):
    value = getattr(_QOpenGLBuffer, name, None) if _QOpenGLBuffer is not None else None
    if value is not None:
        return value
    scope = getattr(_QOpenGLBuffer, "Type", None)
    return getattr(scope, name) if scope is not None else None


if _GL_AVAILABLE:
    class _Gpu3DCanvas(_QOpenGLWidget):
        """GPU-backed mesh viewer using Qt's OpenGL classes directly.

        Geometry is uploaded only when the board changes.  Orbit/pan/zoom then
        changes one MVP matrix, so the CPU no longer re-projects and paints
        every STEP triangle on every mouse move.
        """

        selectionRequested = Signal(object)
        cameraChanged = Signal()
        gpuFailed = Signal(str)

        # Reuse camera interaction and CPU picking from the fallback canvas.
        _camera_basis = _Software3DCanvas._camera_basis
        _screen_ray = _Software3DCanvas._screen_ray
        _ray_box = staticmethod(_Software3DCanvas._ray_box)
        _ray_triangle = staticmethod(_Software3DCanvas._ray_triangle)
        pick_uid = _Software3DCanvas.pick_uid
        _box_edges = staticmethod(_Software3DCanvas._box_edges)
        fit_scene = _Software3DCanvas.fit_scene
        set_view = _Software3DCanvas.set_view
        mousePressEvent = _Software3DCanvas.mousePressEvent
        mouseMoveEvent = _Software3DCanvas.mouseMoveEvent
        mouseReleaseEvent = _Software3DCanvas.mouseReleaseEvent
        wheelEvent = _Software3DCanvas.wheelEvent

        def __init__(self, parent=None, theme="light"):
            super(_Gpu3DCanvas, self).__init__(parent)
            self.theme = theme if theme in ("dark", "light") else "light"
            self.triangles = []
            self.lines = []
            self.scene_bounds = ((-1, -1, -1), (1, 1, 1))
            self.owner_bounds = {}
            self.owner_triangles = {}
            self.selected_uid = None
            self.target = (0.0, 0.0, 0.0)
            self.direction = _norm((1.0, -1.0, 0.85))
            self.camera_up = _camera_up_for_direction(self.direction)
            self.distance = 10.0
            self.fov_deg = 42.0
            self._drag_mode = None
            self._last_mouse = None
            self._press_mouse = None
            self._drag_distance = 0.0
            self._program = None
            self._vbo_tri = None
            self._vbo_lines = None
            self._tri_opaque_count = 0
            self._tri_blend_count = 0
            self._line_count = 0
            self._gpu_dirty = True
            self._gl_failed = False
            self.setMouseTracking(True)
            self.setFocusPolicy(Qt.StrongFocus)

        def set_theme(self, theme):
            self.theme = theme if theme in ("dark", "light") else "light"
            self._gpu_dirty = True
            self.update()

        def set_scene(self, triangles, lines, bounds, owner_bounds=None, owner_triangles=None):
            self.triangles = triangles or []
            self.lines = lines or []
            self.scene_bounds = bounds
            self.owner_bounds = dict(owner_bounds or {})
            self.owner_triangles = dict(owner_triangles or {})
            self._gpu_dirty = True
            self.update()

        def set_selected(self, uid):
            self.selected_uid = uid
            self._gpu_dirty = True
            self.update()

        @staticmethod
        def _pack_vertices(vertices):
            import struct
            flat = []
            for p, rgba in vertices:
                flat.extend((float(p[0]), float(p[1]), float(p[2]),
                             float(rgba[0]), float(rgba[1]), float(rgba[2]), float(rgba[3])))
            return struct.pack("%sf" % len(flat), *flat) if flat else b""

        @staticmethod
        def _normal(a, b, c):
            return _norm(_cross(_vsub(b, a), _vsub(c, a)))

        def _mesh_streams(self):
            opaque, blended = [], []
            for p0, p1, p2, color, alpha in self.triangles:
                rgba = _lit_rgba(color, alpha, self._normal(p0, p1, p2))
                target = opaque if alpha >= 0.999 else blended
                target.extend(((p0, rgba), (p1, rgba), (p2, rgba)))

            line_vertices = []
            for points, color, _width in self.lines:
                rgba = _base_rgba(color, 1.0)
                for i in range(1, len(points)):
                    line_vertices.extend(((points[i - 1], rgba), (points[i], rgba)))

            bounds = self.owner_bounds.get(self.selected_uid)
            if bounds is not None:
                rgba = (0.361, 0.486, 0.980, 1.0)  # #5C7CFA
                for a, b in self._box_edges(bounds):
                    line_vertices.extend(((a, rgba), (b, rgba)))
            return opaque, blended, line_vertices

        def initializeGL(self):
            try:
                fmt = self.context().format()
                major = int(fmt.majorVersion()) if fmt is not None else 2
                core = False
                try:
                    core = int(fmt.profile()) == int(fmt.CoreProfile)
                except Exception:
                    pass
                if major >= 3 and core:
                    vs = """#version 330 core\nin vec3 a_position; in vec4 a_color; uniform mat4 u_mvp; out vec4 v_color; void main(){ gl_Position=u_mvp*vec4(a_position,1.0); v_color=a_color; }"""
                    fs = """#version 330 core\nin vec4 v_color; out vec4 fragColor; void main(){ fragColor=v_color; }"""
                else:
                    vs = """attribute vec3 a_position; attribute vec4 a_color; uniform mat4 u_mvp; varying vec4 v_color; void main(){ gl_Position=u_mvp*vec4(a_position,1.0); v_color=a_color; }"""
                    fs = """varying vec4 v_color; void main(){ gl_FragColor=v_color; }"""
                self._program = _QOpenGLShaderProgram(self)
                if not self._program.addShaderFromSourceCode(_shader_enum("Vertex"), vs):
                    raise RuntimeError(self._program.log())
                if not self._program.addShaderFromSourceCode(_shader_enum("Fragment"), fs):
                    raise RuntimeError(self._program.log())
                if not self._program.link():
                    raise RuntimeError(self._program.log())
                self._vbo_tri = _QOpenGLBuffer(_buffer_enum("VertexBuffer"))
                self._vbo_tri.create()
                self._vbo_lines = _QOpenGLBuffer(_buffer_enum("VertexBuffer"))
                self._vbo_lines.create()
                self._gpu_dirty = True
            except Exception as exc:
                self._gl_failed = True
                self.gpuFailed.emit(str(exc))

        def _upload(self):
            if self._gl_failed or self._program is None:
                return
            opaque, blended, lines = self._mesh_streams()
            all_tri = opaque + blended
            tri_data = self._pack_vertices(all_tri)
            line_data = self._pack_vertices(lines)
            self._tri_opaque_count = len(opaque)
            self._tri_blend_count = len(blended)
            self._line_count = len(lines)
            self._vbo_tri.bind(); self._vbo_tri.allocate(tri_data, len(tri_data)); self._vbo_tri.release()
            self._vbo_lines.bind(); self._vbo_lines.allocate(line_data, len(line_data)); self._vbo_lines.release()
            self._gpu_dirty = False

        def _mvp(self):
            eye, _right, up, _forward = self._camera_basis()
            lo, hi = self.scene_bounds
            diag = max(1.0, math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3))))
            near = max(0.001, min(self.distance * 0.25, diag) / 1000.0)
            far = max(near + 10.0, self.distance + diag * 8.0)
            projection = QtGui.QMatrix4x4()
            projection.perspective(float(self.fov_deg),
                                   float(self.width()) / max(1.0, float(self.height())),
                                   float(near), float(far))
            view = QtGui.QMatrix4x4()
            view.lookAt(QtGui.QVector3D(*eye), QtGui.QVector3D(*self.target),
                        QtGui.QVector3D(*up))
            return projection * view

        def _bind_stream(self, vbo):
            if not vbo.bind():
                return False
            stride = 7 * 4
            self._program.enableAttributeArray("a_position")
            self._program.setAttributeBuffer("a_position", 0x1406, 0, 3, stride)  # GL_FLOAT
            self._program.enableAttributeArray("a_color")
            self._program.setAttributeBuffer("a_color", 0x1406, 3 * 4, 4, stride)
            return True

        def paintGL(self):
            if self._gl_failed or self._program is None:
                return
            try:
                if self._gpu_dirty:
                    self._upload()
                f = self.context().functions()
                bg = _Software3DCanvas._qcolor(thememod.palette(self.theme)["canvas"])
                f.glClearColor(bg.redF(), bg.greenF(), bg.blueF(), 1.0)
                f.glClear(0x4000 | 0x0100)  # COLOR | DEPTH
                f.glEnable(0x0B71)          # DEPTH_TEST
                f.glEnable(0x0BE2)          # BLEND
                f.glBlendFunc(0x0302, 0x0303)
                self._program.bind()
                self._program.setUniformValue("u_mvp", self._mvp())

                if self._tri_opaque_count or self._tri_blend_count:
                    if self._bind_stream(self._vbo_tri):
                        # Match the reference's board material: translucent
                        # block first, depthWrite=false, then opaque geometry.
                        # This is what keeps STEP body/lead colours from being
                        # washed by the substrate overlay.
                        if self._tri_blend_count:
                            if hasattr(f, "glDepthMask"):
                                f.glDepthMask(False)
                            f.glDrawArrays(0x0004, self._tri_opaque_count, self._tri_blend_count)
                        if hasattr(f, "glDepthMask"):
                            f.glDepthMask(True)
                        if self._tri_opaque_count:
                            f.glDrawArrays(0x0004, 0, self._tri_opaque_count)  # TRIANGLES
                        self._vbo_tri.release()

                if self._line_count and self._bind_stream(self._vbo_lines):
                    try:
                        f.glLineWidth(1.5)
                    except Exception:
                        pass
                    f.glDrawArrays(0x0001, 0, self._line_count)  # LINES
                    self._vbo_lines.release()
                self._program.release()
            except Exception as exc:
                self._gl_failed = True
                self.gpuFailed.emit(str(exc))

else:
    _Gpu3DCanvas = None


# ---------------------------------------------------------------------------
# Geometry builder
# ---------------------------------------------------------------------------

class _SceneBuilder(object):
    def __init__(self):
        self.triangles = []
        self.lines = []
        self._lo = [float("inf")] * 3
        self._hi = [float("-inf")] * 3
        self._owner = None
        self.owner_bounds = {}
        self.owner_triangles = {}

    def begin_owner(self, uid):
        self._owner = uid
        if uid is not None and uid not in self.owner_bounds:
            self.owner_bounds[uid] = ([float("inf")] * 3, [float("-inf")] * 3)
            self.owner_triangles[uid] = []

    def end_owner(self):
        self._owner = None

    def _touch(self, p):
        for i in range(3):
            self._lo[i] = min(self._lo[i], p[i])
            self._hi[i] = max(self._hi[i], p[i])
        if self._owner is not None:
            lo, hi = self.owner_bounds[self._owner]
            for i in range(3):
                lo[i] = min(lo[i], p[i]); hi[i] = max(hi[i], p[i])

    def triangle(self, a, b, c, color, alpha=1.0):
        self.triangles.append((a, b, c, color, float(alpha)))
        if self._owner is not None:
            self.owner_triangles.setdefault(self._owner, []).append((a, b, c))
        self._touch(a); self._touch(b); self._touch(c)

    def quad(self, a, b, c, d, color, alpha=1.0):
        self.triangle(a, b, c, color, alpha)
        self.triangle(a, c, d, color, alpha)

    def line(self, points, color, width=1.5):
        pts = list(points)
        if len(pts) < 2:
            return
        self.lines.append((pts, color, float(width)))
        for p in pts:
            self._touch(p)

    def box(self, width, depth, height, center, color, alpha=1.0, angle_deg=0.0):
        """Box dimensions are X width, Y depth, Z height."""
        hx, hy, hz = abs(width) / 2.0, abs(depth) / 2.0, abs(height) / 2.0
        cx, cy, cz = center
        pts = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
               (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
        if abs(angle_deg) > 1e-12:
            pts = [_rot_z(p, angle_deg) for p in pts]
        pts = [(p[0] + cx, p[1] + cy, p[2] + cz) for p in pts]
        faces = ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
        for a, b, c, d in faces:
            self.quad(pts[a], pts[b], pts[c], pts[d], color, alpha)

    def cylinder(self, radius, height, center, color, alpha=1.0, segments=24):
        r = max(1e-6, abs(float(radius))); h = max(1e-6, abs(float(height)))
        cx, cy, cz = center; z0, z1 = cz - h / 2.0, cz + h / 2.0
        bottom = []; top = []
        for i in range(segments):
            a = 2.0 * math.pi * i / segments
            bottom.append((cx + r * math.cos(a), cy + r * math.sin(a), z0))
            top.append((cx + r * math.cos(a), cy + r * math.sin(a), z1))
        cb, ct = (cx, cy, z0), (cx, cy, z1)
        for i in range(segments):
            j = (i + 1) % segments
            self.quad(bottom[i], bottom[j], top[j], top[i], color, alpha)
            self.triangle(cb, bottom[j], bottom[i], color, alpha)
            self.triangle(ct, top[i], top[j], color, alpha)

    def tube(self, outer_radius, inner_radius, height, center, color, alpha=1.0, segments=32):
        """Closed annular cylinder.  A zero inner radius becomes a solid cylinder."""
        ro = max(1e-6, abs(float(outer_radius)))
        ri = max(0.0, min(abs(float(inner_radius)), ro - 1e-6))
        if ri <= 1e-9:
            return self.cylinder(ro, height, center, color, alpha, segments)
        h = max(1e-6, abs(float(height))); cx, cy, cz = center
        z0, z1 = cz-h/2.0, cz+h/2.0
        ob=[]; ot=[]; ib=[]; it=[]
        for i in range(segments):
            a=2.0*math.pi*i/segments; ca,sa=math.cos(a),math.sin(a)
            ob.append((cx+ro*ca,cy+ro*sa,z0)); ot.append((cx+ro*ca,cy+ro*sa,z1))
            ib.append((cx+ri*ca,cy+ri*sa,z0)); it.append((cx+ri*ca,cy+ri*sa,z1))
        for i in range(segments):
            j=(i+1)%segments
            self.quad(ob[i],ob[j],ot[j],ot[i],color,alpha)
            self.quad(ib[j],ib[i],it[i],it[j],color,alpha)
            self.quad(ot[i],ot[j],it[j],it[i],color,alpha)
            self.quad(ob[j],ob[i],ib[i],ib[j],color,alpha)

    def prism(self, points, z0, z1, color, alpha=1.0):
        """Extrude a simple XY polygon between ``z0`` and ``z1``.

        ``points`` are world-space XY coordinates.  Caps are triangulated and
        side walls follow the actual outline, so circle/ellipse/polygon boards
        no longer become rectangular bounding-box slabs in 3D.
        """
        pts2 = list(points or [])
        if len(pts2) < 3:
            return
        if _polygon_area(pts2) < 0.0:
            pts2.reverse()
        triangles = _triangulate_polygon(pts2)
        bottom = [(x, y, float(z0)) for x, y in pts2]
        top = [(x, y, float(z1)) for x, y in pts2]
        for a, b, c in triangles:
            self.triangle(top[a], top[b], top[c], color, alpha)
            self.triangle(bottom[c], bottom[b], bottom[a], color, alpha)
        for i in range(len(pts2)):
            j = (i + 1) % len(pts2)
            self.quad(bottom[i], bottom[j], top[j], top[i], color, alpha)

    def polygon_cap(self, points, z, color, alpha=1.0, upward=True):
        """Cap one closed XY polygon at ``z``."""
        pts = list(points or [])
        if len(pts) < 3:
            return
        triangles = _triangulate_polygon(pts)
        world = [(x, y, float(z)) for x, y in pts]
        for a, b, c in triangles:
            if upward:
                self.triangle(world[a], world[b], world[c], color, alpha)
            else:
                self.triangle(world[c], world[b], world[a], color, alpha)

    def loft(self, lower_points, z0, upper_points, z1, color, alpha=1.0):
        """Connect two closed reference-plane boundaries with side walls."""
        lower, upper = _aligned_loop_pair(lower_points, upper_points)
        n = min(len(lower), len(upper))
        if n < 3:
            return
        bottom = [(lower[i][0], lower[i][1], float(z0)) for i in range(n)]
        top = [(upper[i][0], upper[i][1], float(z1)) for i in range(n)]
        for i in range(n):
            j = (i + 1) % n
            self.quad(bottom[i], bottom[j], top[j], top[i], color, alpha)

    def bounds(self):
        if not math.isfinite(self._lo[0]):
            return ((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0))
        return (tuple(self._lo), tuple(self._hi))


class _AxisTriad(QtWidgets.QWidget):
    """Small camera-aware XYZ indicator overlaid in the 3D viewport."""

    def __init__(self, view, parent=None):
        super(_AxisTriad, self).__init__(parent)
        self._view = view
        self.setFixedSize(92, 92)
        try:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        except Exception:
            pass

    @staticmethod
    def _pen(color, width=2.0):
        pen = QtGui.QPen()
        pen.setColor(_Software3DCanvas._qcolor(color))
        try:
            pen.setWidthF(float(width))
        except Exception:
            pen.setWidth(max(1, int(round(width))))
        pen.setCosmetic(True)
        return pen

    def paintEvent(self, _event):
        canvas = getattr(self._view, "canvas", None)
        if canvas is None or not hasattr(canvas, "_camera_basis"):
            return
        try:
            _eye, right, up, forward = canvas._camera_basis()
        except Exception:
            return

        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            origin = QtCore.QPointF(45.0, 48.0)
            length = 28.0
            axes = [
                ("X", (1.0, 0.0, 0.0), "#D43B32"),
                ("Y", (0.0, 1.0, 0.0), "#2AA84A"),
                ("Z", (0.0, 0.0, 1.0), "#356AE6"),
            ]

            # Draw the axis pointing furthest away first so the near one reads
            # naturally when several projections overlap.
            axes.sort(key=lambda item: _dot(item[1], forward))
            for label, axis_vec, color in axes:
                sx = _dot(axis_vec, right)
                sy = _dot(axis_vec, up)
                end = QtCore.QPointF(origin.x() + sx * length,
                                     origin.y() - sy * length)
                dx, dy = end.x() - origin.x(), end.y() - origin.y()
                screen_len = math.hypot(dx, dy)
                painter.setPen(self._pen(color, 2.2))
                painter.setBrush(QtGui.QBrush(_Software3DCanvas._qcolor(color)))
                painter.drawLine(origin, end)

                # Arrow head.  A world axis viewed end-on is intentionally a
                # small coloured dot rather than an unstable giant arrow head.
                if screen_len > 3.0:
                    ux, uy = dx / screen_len, dy / screen_len
                    px, py = -uy, ux
                    tip = end
                    base_x, base_y = end.x() - ux * 7.0, end.y() - uy * 7.0
                    poly = QtGui.QPolygonF([
                        tip,
                        QtCore.QPointF(base_x + px * 3.3, base_y + py * 3.3),
                        QtCore.QPointF(base_x - px * 3.3, base_y - py * 3.3),
                    ])
                    painter.drawPolygon(poly)
                    label_pos = QtCore.QPointF(end.x() + ux * 6.0 - 4.0,
                                               end.y() + uy * 6.0 + 4.0)
                else:
                    painter.drawEllipse(end, 3.0, 3.0)
                    label_pos = QtCore.QPointF(end.x() + 5.0, end.y() - 4.0)
                painter.setPen(self._pen(color, 1.0))
                painter.drawText(label_pos, label)

            painter.setPen(Qt.NoPen)
            fg = "#50555E" if getattr(self._view, "theme", "light") == "light" else "#D0D4DC"
            painter.setBrush(QtGui.QBrush(_Software3DCanvas._qcolor(fg)))
            painter.drawEllipse(origin, 2.2, 2.2)
        finally:
            if painter.isActive():
                painter.end()


class _CanvasHost(QtWidgets.QWidget):
    """Viewport wrapper that keeps the axis triad above either renderer."""

    def __init__(self, parent=None):
        super(_CanvasHost, self).__init__(parent)
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.axis = None

    def set_canvas(self, canvas):
        while self._layout.count():
            item = self._layout.takeAt(0)
            old = item.widget()
            if old is not None and old is not canvas:
                old.hide()
        self._layout.addWidget(canvas)
        canvas.show()
        if self.axis is not None:
            self.axis.raise_()

    def resizeEvent(self, event):
        super(_CanvasHost, self).resizeEvent(event)
        if self.axis is not None:
            margin = 10
            self.axis.move(max(0, self.width() - self.axis.width() - margin),
                           max(0, self.height() - self.axis.height() - margin))
            self.axis.raise_()


class Board3DView(QtWidgets.QWidget):
    """Document-free 3D board preview with GPU rendering and software fallback."""

    footprintSelected = Signal(object)
    #: "box" or "model" for every component (toolbar *Boxes* toggle).
    modelDisplayRequested = Signal(str)

    def __init__(self, parent=None, theme="light"):
        super(Board3DView, self).__init__(parent)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(SIZE_IGNORED, SIZE_EXPANDING)
        self.theme = theme
        self._last_signature = None
        self._model_dir = ""
        self._model_index = None
        self._mesh_cache = {}
        self._did_fit = False
        self._missing_models = []
        self._model_bounds = {}
        self._entries_by_uid = {}
        self._zmap = {}
        self._selected_uid = None
        self._gpu_failure_pending = False
        self.renderer_name = "software"

        root = QtWidgets.QVBoxLayout(self)
        self._root_layout = root
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.toolbar = QtWidgets.QWidget()
        self.toolbar.setMinimumWidth(0)
        self.toolbar.setSizePolicy(SIZE_IGNORED, SIZE_PREFERRED)
        bar = QtWidgets.QHBoxLayout(self.toolbar)
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(4)
        self.view_buttons = []
        for label, name in (("Fit", "fit"), ("Top", "top"), ("Bottom", "bottom"),
                            ("Front", "front"), ("Back", "back"),
                            ("Left", "left"), ("Right", "right"), ("Iso", "iso")):
            btn = QtWidgets.QPushButton(label)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(SIZE_IGNORED, SIZE_FIXED)
            btn.setToolTip("3D %s view" % label.lower())
            btn.clicked.connect(lambda _checked=False, n=name: self.set_view(n))
            self.view_buttons.append(btn)
            # Equal stretch lets the row compress with the MDI viewport rather
            # than contributing eight fixed native button size hints.
            bar.addWidget(btn, 1)
        self.btn_boxes = QtWidgets.QPushButton("Boxes")
        self.btn_boxes.setCheckable(True)
        self.btn_boxes.setMinimumWidth(0)
        self.btn_boxes.setSizePolicy(SIZE_IGNORED, SIZE_FIXED)
        self.btn_boxes.setToolTip("Show every component as its bounding box instead of its "
                                  "3D model. Exports follow the same choice.")
        self.btn_boxes.clicked.connect(lambda checked: self.modelDisplayRequested.emit(
            "box" if checked else "model"))
        bar.addWidget(self.btn_boxes, 1)
        self.hint = QtWidgets.QLabel("")
        self.hint.setObjectName("faint")
        self.hint.setMinimumWidth(0)
        self.hint.setSizePolicy(SIZE_IGNORED, SIZE_PREFERRED)
        bar.addWidget(self.hint, 2)
        root.addWidget(self.toolbar)

        if _Gpu3DCanvas is not None:
            try:
                self.canvas = _Gpu3DCanvas(theme=theme)
                self.renderer_name = "GPU (Qt OpenGL)"
                self.canvas.gpuFailed.connect(self._on_gpu_failed)
            except Exception:
                self.canvas = _Software3DCanvas(theme=theme)
                self.renderer_name = "software"
        else:
            self.canvas = _Software3DCanvas(theme=theme)
        self.canvas.selectionRequested.connect(self.footprintSelected.emit)

        self.canvas_host = _CanvasHost()
        self.canvas_host.set_canvas(self.canvas)
        self.axis_triad = _AxisTriad(self, self.canvas_host)
        self.canvas_host.axis = self.axis_triad
        self.axis_triad.show(); self.axis_triad.raise_()
        self.canvas.cameraChanged.connect(self.axis_triad.update)
        root.addWidget(self.canvas_host, 1)
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("faint")
        self.status.setWordWrap(True)
        self.status.setContentsMargins(8, 2, 8, 4)
        self.status.hide()
        root.addWidget(self.status)
        self._sync_renderer_hint()

    def minimumSizeHint(self):
        # The 3D viewport is intentionally responsive.  Eight preset buttons
        # and the help label must never dictate the FreeCAD MDI document width.
        return QtCore.QSize(0, 0)

    def _sync_renderer_hint(self):
        # Keep the toolbar informative without letting a long QLabel define the
        # minimum width of the whole MDI document.  The compact form is used in
        # narrower tab layouts; the complete wording remains a tooltip.
        full = "%s · left-drag KiCad trackball · middle/right-drag to pan · wheel to zoom · press 2 for 2D" % self.renderer_name
        compact = "%s · drag orbit · M/R pan · wheel zoom" % self.renderer_name
        self.hint.setToolTip(full)
        self.hint.setText(full if self.width() >= 1250 else compact)

    def resizeEvent(self, event):
        super(Board3DView, self).resizeEvent(event)
        self._sync_renderer_hint()

    def _on_gpu_failed(self, message):
        """Queue a QPainter fallback outside QOpenGLWidget's paint/init stack."""
        if isinstance(self.canvas, _Software3DCanvas) or self._gpu_failure_pending:
            return
        self._gpu_failure_pending = True
        QtCore.QTimer.singleShot(0, lambda m=str(message): self._install_software_fallback(m))

    def _install_software_fallback(self, message):
        self._gpu_failure_pending = False
        if isinstance(self.canvas, _Software3DCanvas):
            return
        old = self.canvas
        fallback = _Software3DCanvas(theme=self.theme)
        fallback.selectionRequested.connect(self.footprintSelected.emit)
        fallback.set_scene(getattr(old, "triangles", []), getattr(old, "lines", []),
                           getattr(old, "scene_bounds", ((-1,-1,-1),(1,1,1))),
                           getattr(old, "owner_bounds", {}),
                           getattr(old, "owner_triangles", {}))
        fallback.target = tuple(getattr(old, "target", (0.0, 0.0, 0.0)))
        fallback.direction = tuple(getattr(old, "direction", (1.0, -1.0, 0.85)))
        fallback.camera_up = tuple(getattr(old, "camera_up",
                                           _camera_up_for_direction(fallback.direction)))
        fallback.distance = float(getattr(old, "distance", 10.0))
        fallback.set_selected(self._selected_uid)
        fallback.cameraChanged.connect(self.axis_triad.update)
        self.canvas_host.set_canvas(fallback)
        old.hide(); old.deleteLater()
        self.canvas = fallback
        self.axis_triad.update(); self.axis_triad.raise_()
        self.renderer_name = "software fallback"
        self._sync_renderer_hint()
        self.status.setText("GPU preview unavailable; using software fallback: %s" % message)
        self.status.show()

    def set_selected(self, uid):
        self._selected_uid = uid
        self.canvas.set_selected(uid)

    def set_theme(self, theme):
        self.theme = theme if theme in ("dark", "light") else "light"
        self.canvas.set_theme(self.theme)
        self.axis_triad.update()

    def set_models_folder(self, path):
        path = os.path.abspath(path) if path else ""
        if path != self._model_dir:
            self._model_dir = path
            self._model_index = None
            self._last_signature = None

    def _index_models(self):
        if self._model_index is not None:
            return self._model_index
        self._model_index = {}
        root = self._model_dir
        if not root or not os.path.isdir(root):
            return self._model_index
        base_depth = root.rstrip(os.sep).count(os.sep)
        for current, dirs, files in os.walk(root):
            depth = current.rstrip(os.sep).count(os.sep) - base_depth
            if depth >= 5:
                dirs[:] = []
            for name in files:
                if name.lower().endswith((".stp", ".step", ".wrl")):
                    self._model_index.setdefault(name.lower(), os.path.join(current, name))
        return self._model_index

    def _resolved_model(self, model):
        if model is None or not model.path:
            return None
        raw = os.path.expandvars(model.path)
        if os.path.isfile(raw):
            return os.path.abspath(raw)
        base = os.path.basename(raw.replace("\\", os.sep))
        return self._index_models().get(base.lower())

    def _model_mesh(self, path):
        """Tessellate a STEP model per face while preserving STEP materials.

        Returns ``(verts, facets, color)`` groups.  ``stepcolor`` has already
        expanded whole-solid/shell styles onto their descendant faces and then
        applied any direct face overrides, so this same path handles both STEP
        presentation conventions without creating a FreeCAD document.
        ``color`` is ``None`` only when the file carries no usable style.
        """
        if not path or path.lower().endswith(".wrl"):
            return None
        try:
            stamp = (os.path.getmtime(path), os.path.getsize(path))
        except OSError:
            return None
        cached = self._mesh_cache.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            import Part
            try:
                shape = Part.read(path)
            except Exception:
                shape = Part.Shape()
                shape.read(path)

            colors = stepcolor.read_step_colors(path)
            faces = list(getattr(shape, "Faces", []) or [])
            groups = []
            # 0.30 mm is detailed enough for an interactive preview and avoids
            # turning common IC packages into hundreds of thousands of painter
            # polygons.  STEP export/conversion is unaffected by this preview mesh.
            if faces:
                # Face order from OpenCascade matches the order the faces appear
                # in the STEP text, which is what stepcolor indexes by.  If that
                # ever fails to line up the worst case is the dominant colour,
                # not a crash or a hole.
                for index, face in enumerate(faces):
                    try:
                        verts, facets = face.tessellate(0.30)
                    except Exception:
                        continue
                    if not verts or not facets:
                        continue
                    groups.append((
                        [(float(v.x), float(v.y), float(v.z)) for v in verts],
                        [tuple(int(i) for i in facet) for facet in facets],
                        colors.color_for(index)))
            if not groups:
                # A shape with no faces (or a tessellation that produced
                # nothing) still renders as a single uncoloured mesh.
                verts, facets = shape.tessellate(0.30)
                groups = [([(float(v.x), float(v.y), float(v.z)) for v in verts],
                           [tuple(int(i) for i in facet) for facet in facets],
                           colors.dominant)]
            self._mesh_cache[path] = (stamp, groups)
            return groups
        except Exception:
            return None

    @staticmethod
    def _entry_xy(entry):
        pos = entry.get("pos", (0.0, 0.0))
        return ((float(pos.x()), float(pos.y())) if hasattr(pos, "x")
                else (float(pos[0]), float(pos[1])))

    def _transform_fp_point(self, entry, x, y, z=0.0):
        # 2D scene is Y-down; software 3D is Z-up with world Y-up.
        p = (float(x), -float(y), float(z))
        p = _rot_z(p, -float(entry.get("rotation", 0.0)))
        px, py = self._entry_xy(entry)
        return (p[0] + px, p[1] - py, p[2])

    def _add_pad(self, scene, entry, pad, plane_z, copper_t):
        center = self._transform_fp_point(entry, pad.x, pad.y, plane_z - copper_t / 2.0)
        rot = -float(entry.get("rotation", 0.0)) - float(getattr(pad, "rot", 0.0) or 0.0)
        if pad.shape == "circle":
            scene.cylinder(float(pad.w) / 2.0, copper_t, center, PAD_GOLD)
        elif pad.shape == "oval" and abs(float(pad.w) - float(pad.h)) < 1e-6:
            scene.cylinder(float(pad.w) / 2.0, copper_t, center, PAD_GOLD)
        else:
            scene.box(max(0.001, float(pad.w)), max(0.001, float(pad.h)), copper_t,
                      center, PAD_GOLD, 1.0, rot)

    def _add_footprint_graphics(self, scene, entry, plane_z):
        fp = entry["fp"]
        vis = entry.get("layer_visibility", {})
        z = plane_z + 0.05
        def world_pts(points):
            return [self._transform_fp_point(entry, x, y, z) for x, y in points]
        for line in fp.lines:
            if vis.get(line.layer, True):
                scene.line(world_pts([(line.x1, line.y1), (line.x2, line.y2)]),
                           layer_color(line.layer, self.theme), 1.4)
        for rect in fp.rects:
            if vis.get(rect.layer, True):
                pts = [(rect.x1, rect.y1), (rect.x2, rect.y1),
                       (rect.x2, rect.y2), (rect.x1, rect.y2), (rect.x1, rect.y1)]
                scene.line(world_pts(pts), layer_color(rect.layer, self.theme), 1.4)
        for circle in fp.circles:
            if vis.get(circle.layer, True):
                scene.line(world_pts(_circle_points(circle.cx, circle.cy, circle.r)),
                           layer_color(circle.layer, self.theme), 1.4)
        for arc in fp.arcs:
            if vis.get(arc.layer, True):
                scene.line(world_pts(_arc_points(arc)), layer_color(arc.layer, self.theme), 1.4)
        for poly in fp.polys:
            if vis.get(poly.layer, True):
                pts = list(poly.points)
                if pts and pts[0] != pts[-1]:
                    pts.append(pts[0])
                scene.line(world_pts(pts), layer_color(poly.layer, self.theme), 1.4)

    def _add_model(self, scene, entry, plane_z):
        fp = entry.get("fp")
        model = getattr(fp, "model", None)
        path = self._resolved_model(model)
        if not path:
            if model is not None and getattr(model, "path", ""):
                self._missing_models.append(os.path.basename(model.path.replace("\\", os.sep)))
            return
        if path.lower().endswith(".wrl"):
            # The old Quarter path delegated WRL parsing to Coin.  Keeping that
            # dependency would reintroduce the native GL crash.  STEP is rendered
            # fully; WRL remains listed so users know why a package body is absent.
            self._missing_models.append(os.path.basename(path) + " (WRL preview not supported in software mode)")
            return
        groups = self._model_mesh(path)
        if not groups:
            self._missing_models.append(os.path.basename(path) + " (could not tessellate)")
            return
        off = tuple(model.offset or (0.0, 0.0, 0.0))
        scale = tuple(model.scale or (1.0, 1.0, 1.0))
        rotate = tuple(model.rotate or (0.0, 0.0, 0.0))
        fp_rot = -float(entry.get("rotation", 0.0))
        px, py = self._entry_xy(entry)
        adjust = entry.get("model_adjust") or {"x": 0.0, "y": 0.0, "z": 0.0}
        ax = float(adjust.get("x", 0.0)); ay = float(adjust.get("y", 0.0)); az = float(adjust.get("z", 0.0))
        model_lo = [float("inf")] * 3
        model_hi = [float("-inf")] * 3
        as_box = str(entry.get("model_display", "model")) == "box"
        local_lo = [float("inf")] * 3
        local_hi = [float("-inf")] * 3
        dominant, dominant_size = None, -1

        def place(p):
            p = _rot_z(p, fp_rot)
            return (p[0] + px + ax, p[1] - py - ay, p[2] + plane_z + az)

        for verts, facets, color in groups:
            body = _rgb01_to_hex(color) if color else MODEL_NEUTRAL
            if len(facets) > dominant_size:
                dominant, dominant_size = body, len(facets)
            transformed = []
            for x, y, z in verts:
                p = (x * float(scale[0]), y * float(scale[1]), z * float(scale[2]))
                p = _rot_xyz(p, -float(rotate[0]), -float(rotate[1]), -float(rotate[2]))
                # Match the reference viewer's model transform exactly.  It
                # rotates model Z-up into its Three.js Y-up scene first; when
                # mapped back into our Z-up/Y-up world this leaves model-local
                # X/Y unchanged.  Only the *board* translation below flips Y.
                p = (p[0] + float(off[0]), p[1] + float(off[1]), p[2] + float(off[2]))
                for ii in range(3):
                    local_lo[ii] = min(local_lo[ii], p[ii]); local_hi[ii] = max(local_hi[ii], p[ii])
                q = place(p)
                transformed.append(q)
                for ii in range(3):
                    model_lo[ii] = min(model_lo[ii], q[ii]); model_hi[ii] = max(model_hi[ii], q[ii])
            if as_box:
                continue
            for face in facets:
                if len(face) < 3:
                    continue
                a = transformed[face[0]]
                for i in range(1, len(face) - 1):
                    scene.triangle(a, transformed[face[i]], transformed[face[i + 1]],
                                   body, 1.0)
        if as_box and math.isfinite(local_lo[0]):
            self._add_model_box(scene, local_lo, local_hi, place, dominant or MODEL_NEUTRAL)
        # Bounds always come from the real model so Auto alignment and the
        # offset readout are identical in both display modes.
        if math.isfinite(model_lo[0]):
            self._model_bounds[entry.get("uid")] = (tuple(model_lo), tuple(model_hi))

    @staticmethod
    def _add_model_box(scene, lo, hi, place, color):
        """Model-aligned bounding box (in the footprint's frame), placed like the model."""
        x0, y0, z0 = lo
        x1, y1, z1 = hi
        c = [place(p) for p in ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                                (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))]
        for a, b, cc, d in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                            (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
            scene.quad(c[a], c[b], c[cc], c[d], color, 1.0)
        for i, j in ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
                     (0, 4), (1, 5), (2, 6), (3, 7)):
            scene.line([c[i], c[j]], MODEL_BOX_EDGE, 1.0)

    def _add_routed_copper(self, scene, routed, stack, zmap):
        if not routed:
            return
        tracks = routed.get("tracks", []) if isinstance(routed, dict) else []
        vias = routed.get("vias", []) if isinstance(routed, dict) else []
        order = [layer.id for layer in stack.layers]
        for track in tracks:
            layer = stack.by_id(getattr(track, "layer_id", None))
            if layer is None or not layer.is_copper or not layer.visible:
                continue
            pts = list(getattr(track, "points", []) or [])
            if len(pts) < 2:
                continue
            z = float(zmap.get(layer.id, 0.0))
            cu = max(0.001, float(stack.copper_thickness(layer.id)))
            # 3D shows the physical copper finish, not the editor layer colour.
            # Layer colours remain useful in 2D for routing/stack identification,
            # but pads, tracks and vias are all the same conductive material in 3D.
            color = COPPER_METAL
            # Etched below the reference plane: top face == z, bottom == z-cu.
            for i in range(1, len(pts)):
                ax, ay = pts[i-1]; bx, by = pts[i]
                dx, dy = bx-ax, by-ay; length = math.hypot(dx,dy)
                if length <= 1e-9: continue
                scene.box(length, float(track.width), cu,
                          ((ax+bx)/2.0, -(ay+by)/2.0, z-cu/2.0),
                          color, 1.0, -math.degrees(math.atan2(dy,dx)))
            for x,y in pts:
                scene.cylinder(float(track.width)/2.0, cu, (x,-y,z-cu/2.0), color, 1.0, 20)

        def spans(v, layer_id):
            try:
                a=order.index(v.start_layer_id); b=order.index(v.end_layer_id); i=order.index(layer_id)
            except ValueError:
                return False
            lo,hi=sorted((a,b)); return lo<=i<=hi

        for via in vias:
            if not order or via.start_layer_id not in order or via.end_layer_id not in order:
                continue
            ai,bi=order.index(via.start_layer_id),order.index(via.end_layer_id)
            lo_i,hi_i=sorted((ai,bi)); lower=stack.layers[lo_i]; upper=stack.layers[hi_i]
            if not any(layer.visible for layer in stack.layers[lo_i:hi_i+1]):
                continue
            # The conductive barrel spans from the underside of the lower
            # plane's etched copper to the upper reference plane.  For a
            # same-layer solid via this naturally becomes that layer's copper
            # thickness.
            z0=float(zmap[lower.id])-float(stack.copper_thickness(lower.id))
            z1=float(zmap[upper.id])
            if abs(z1-z0)<1e-9: z1=z0+max(0.001,float(stack.copper_thickness(lower.id)))
            color = COPPER_METAL
            scene.tube(float(via.diameter)/2.0, float(via.hole_diameter)/2.0,
                       abs(z1-z0), (float(via.x),-float(via.y),(z0+z1)/2.0),
                       color, 1.0, 32)

    def rebuild(self, entries, stack, solid=None, model_search_dir="", force=False, routed=None):
        self.set_models_folder(model_search_dir)
        signature = (
            tuple((e.get("uid"),
                   round(float(e.get("pos").x() if hasattr(e.get("pos"), "x") else 0.0), 5),
                   round(float(e.get("pos").y() if hasattr(e.get("pos"), "y") else 0.0), 5),
                   round(float(e.get("rotation", 0.0)), 4), id(e.get("fp")),
                   e.get("design_layer_id"), tuple(sorted(e.get("layer_visibility", {}).items())),
                   tuple(sorted((e.get("model_adjust") or {}).items())),
                   e.get("model_adjust_mode", "auto"),
                   e.get("model_display", "model")) for e in entries),
            repr(stack.snapshot()),
            solid.signature() if solid is not None else None,
            repr({"tracks": [t.snapshot() for t in (routed or {}).get("tracks", [])],
                  "vias": [v.snapshot() for v in (routed or {}).get("vias", [])]} if isinstance(routed, dict) else routed),
            self._model_dir, self.theme,
        )
        if signature == self._last_signature and not force:
            return True
        self._last_signature = signature
        self._missing_models = []
        self._model_bounds = {}
        self._entries_by_uid = dict((e.get("uid"), e) for e in entries)
        scene = _SceneBuilder()

        zmap, top_z = layer_heights(stack)
        self._zmap = dict(zmap)
        layers = list(stack.layers)
        layer_loops = {}

        if solid is not None:
            # The imported solid *is* the board.  Render its own mesh as the
            # translucent substrate and draw its crease edges so its shape
            # stays readable through the translucency.
            world = solid.world_vertices()
            for a, b, c in solid.triangles:
                scene.triangle(world[a], world[b], world[c], BOARD_BLOCK, BOARD_BLOCK_ALPHA)
            edge_color = PLANE_EDGE
            for i, j in solid.feature_edges():
                scene.line([world[i], world[j]], edge_color, 1.0)
            for layer in layers:
                layer_loops[layer.id] = solid.section(zmap.get(layer.id, 0.0))
        else:
            # No solid yet: keep a footprint-bounds block so 3D stays useful
            # while the board body has not been imported.
            minx, miny, maxx, maxy = board_bounds(entries, None)
            width = max(1.0, maxx - minx); depth = max(1.0, maxy - miny)
            cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
            block_h = max(top_z, 0.05)
            scene.box(width, depth, block_h,
                      (cx, -cy, block_h / 2.0), BOARD_BLOCK, BOARD_BLOCK_ALPHA)
            half_w, half_d = width / 2.0, depth / 2.0
            fallback_loop = [(cx - half_w, -cy - half_d), (cx + half_w, -cy - half_d),
                             (cx + half_w, -cy + half_d), (cx - half_w, -cy + half_d)]
            for layer in layers:
                layer_loops[layer.id] = [fallback_loop]

        # Each reference plane is marked by its own derived cross-section, so
        # it is easy to see where every copper layer meets the solid.  The
        # active layer uses the board-edge colour, the rest a neutral tone.
        active_id = getattr(stack, "active_id", None)
        edge_hex = outline_hex(self.theme)
        for layer in layers:
            if not layer.visible:
                continue
            z = zmap.get(layer.id, 0.0)
            color = edge_hex if (solid is not None and layer.id == active_id) else PLANE_EDGE
            width = 2.0 if color == edge_hex else 1.0
            for loop in layer_loops.get(layer.id, []):
                scene.line([(x, y, z) for x, y in loop] +
                           [(loop[0][0], loop[0][1], z)], color, width)

        self._add_routed_copper(scene, routed, stack, zmap)

        for entry in entries:
            layer = stack.by_id(entry.get("design_layer_id"))
            if layer is None or not layer.is_copper or not layer.visible:
                continue
            uid = entry.get("uid")
            scene.begin_owner(uid)
            z = zmap.get(layer.id, 0.0)
            copper_t = max(0.005, float(stack.copper_thickness(layer.id)))
            for pad in entry["fp"].pads:
                self._add_pad(scene, entry, pad, z, copper_t)
            self._add_footprint_graphics(scene, entry, z)
            self._add_model(scene, entry, z)
            scene.end_owner()

        owner_bounds = {}
        for uid, pair in scene.owner_bounds.items():
            lo, hi = pair
            if uid is not None and math.isfinite(lo[0]):
                # A small world-space pad makes thin footprints/model-less parts easy to click.
                pad = 0.15
                owner_bounds[uid] = (tuple(v - pad for v in lo), tuple(v + pad for v in hi))
        self.canvas.set_scene(scene.triangles, scene.lines, scene.bounds(), owner_bounds,
                              scene.owner_triangles)
        self.canvas.set_selected(self._selected_uid)
        if not self._did_fit:
            self.canvas.fit_scene()
            self._did_fit = True

        notes = []
        if solid is None:
            notes.append("No board solid imported — File ▸ Import board solid… "
                         "(showing a block around the footprints)")
        else:
            outside = [layer.name for layer in layers if not layer_loops.get(layer.id)]
            if outside:
                notes.append("Outside the board solid: " + ", ".join(outside))
        if self._missing_models:
            unique = []
            for name in self._missing_models:
                if name not in unique:
                    unique.append(name)
            notes.append("3D preview: " + "; ".join(unique[:6]) +
                         (" …" if len(unique) > 6 else ""))
        if notes:
            self.status.setText("\n".join(notes))
            self.status.show()
        else:
            self.status.hide()
        return True

    def measure_model(self, uid):
        """Measure placed-model alignment using the same rules as the web viewer.

        Deltas are returned in the add-on's model-adjust coordinates: +X right,
        +Y down in board space, +Z up.
        """
        bounds = self._model_bounds.get(uid)
        entry = self._entries_by_uid.get(uid)
        if bounds is None or entry is None:
            return None
        lo, hi = bounds
        fp = entry.get("fp")
        plane_z = self._zmap.get(entry.get("design_layer_id"), 0.0)
        pad_points = [self._transform_fp_point(entry, p.x, p.y, plane_z)
                      for p in getattr(fp, "pads", [])]
        if pad_points:
            target_x = (min(p[0] for p in pad_points) + max(p[0] for p in pad_points)) / 2.0
            target_y = (min(p[1] for p in pad_points) + max(p[1] for p in pad_points)) / 2.0
        else:
            px, py = self._entry_xy(entry)
            target_x, target_y = px, -py
        cx = (lo[0] + hi[0]) / 2.0
        cy = (lo[1] + hi[1]) / 2.0
        gap = lo[2] - plane_z
        return {
            "gap": gap,
            "dx": target_x - cx,
            # world +Y is board -Y, so convert the correction back to board coordinates
            "dy": -(target_y - cy),
            "dz": plane_z - lo[2],
            "below": lo[2] < plane_z - 1e-6,
            "height": hi[2] - lo[2],
        }

    def fit(self):
        self.canvas.fit_scene()
        self._did_fit = True

    def set_view(self, name):
        if name == "fit":
            self.fit()
        else:
            self.canvas.set_view(name)
