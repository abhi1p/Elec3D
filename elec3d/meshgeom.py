# -*- coding: utf-8 -*-
"""Pure-Python 2D polygon helpers shared by the board solid, 3D view and export.

Nothing here imports Qt or FreeCAD, so the board-solid slicing and the legacy
Outline migration can be unit-tested without a display or OpenCascade.

The ``_outline_*`` helpers understand the rectangle/circle/ellipse/line
primitives that boards drew before v0.17.  They are kept only so old
``.e3d_pcb`` files can be migrated into an equivalent board solid.
"""

import math

__all__ = [
    "polygon_area", "triangulate_polygon", "resample_loop", "aligned_loop_pair",
    "sorted_loops", "interpolate_loop_sets", "outline_loops",
    "outline_loops_by_layer", "loops_bounds", "point_in_loops",
]


def _outline_bounds(shape):
    data = shape.snapshot() if hasattr(shape, "snapshot") else dict(shape or {})
    kind = data.get("kind", "")
    if kind in ("rectangle", "ellipse"):
        cx = float(data.get("x", 0.0)); cy = float(data.get("y", 0.0))
        w = abs(float(data.get("width", 0.0))); h = abs(float(data.get("height", 0.0)))
        return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
    if kind == "circle":
        cx = float(data.get("x", 0.0)); cy = float(data.get("y", 0.0))
        r = abs(float(data.get("radius", 0.0)))
        return (cx - r, cy - r, cx + r, cy + r)
    if kind == "line":
        xs = [float(data.get("x1", 0.0)), float(data.get("x2", 0.0))]
        ys = [float(data.get("y1", 0.0)), float(data.get("y2", 0.0))]
        return (min(xs), min(ys), max(xs), max(ys))
    return None




def _outline_loop(shape, segments=96):
    """Return one closed board-space loop for a closed Outline primitive.

    Points use the editor/KiCad coordinate convention (Y down) and the closing
    point is intentionally omitted.  Open line primitives are handled by
    :func:`_outline_loops`, which can chain multiple lines into a polygon.
    """
    data = shape.snapshot() if hasattr(shape, "snapshot") else dict(shape or {})
    kind = data.get("kind", "")
    if kind == "rectangle":
        cx = float(data.get("x", 0.0)); cy = float(data.get("y", 0.0))
        w = abs(float(data.get("width", 0.0))); h = abs(float(data.get("height", 0.0)))
        if w <= 1e-9 or h <= 1e-9:
            return None
        return [(cx-w/2.0, cy-h/2.0), (cx+w/2.0, cy-h/2.0),
                (cx+w/2.0, cy+h/2.0), (cx-w/2.0, cy+h/2.0)]
    if kind in ("circle", "ellipse"):
        cx = float(data.get("x", 0.0)); cy = float(data.get("y", 0.0))
        if kind == "circle":
            rx = ry = abs(float(data.get("radius", 0.0)))
        else:
            rx = abs(float(data.get("width", 0.0))) / 2.0
            ry = abs(float(data.get("height", 0.0))) / 2.0
        if rx <= 1e-9 or ry <= 1e-9:
            return None
        count = max(24, int(segments))
        return [(cx + rx * math.cos(2.0 * math.pi * i / count),
                 cy + ry * math.sin(2.0 * math.pi * i / count))
                for i in range(count)]
    return None


def _same_point(a, b, tol=1e-4):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _line_outline_loops(shapes, tol=1e-4):
    """Chain Outline line primitives into closed loops when possible.

    This makes a board drawn as four (or more) line segments behave like the
    equivalent rectangle/polygon in 3D instead of falling back to its bounding
    box.  Unclosed line chains remain visual outline strokes only.
    """
    segments = []
    for shape in shapes or []:
        data = shape.snapshot() if hasattr(shape, "snapshot") else dict(shape or {})
        if data.get("kind") != "line":
            continue
        a = (float(data.get("x1", 0.0)), float(data.get("y1", 0.0)))
        b = (float(data.get("x2", 0.0)), float(data.get("y2", 0.0)))
        if not _same_point(a, b, tol):
            segments.append((a, b))

    loops = []
    unused = list(segments)
    while unused:
        a, b = unused.pop(0)
        chain = [a, b]
        progressed = True
        while progressed and unused:
            progressed = False
            end = chain[-1]
            for i, (p, q) in enumerate(unused):
                if _same_point(end, p, tol):
                    chain.append(q); unused.pop(i); progressed = True; break
                if _same_point(end, q, tol):
                    chain.append(p); unused.pop(i); progressed = True; break
        if len(chain) >= 4 and _same_point(chain[-1], chain[0], tol):
            chain.pop()
            loops.append(chain)
    return loops


def _outline_loops(outlines, segments=96):
    """Return every closed board boundary represented by Outline primitives."""
    loops = []
    for shape in outlines or []:
        loop = _outline_loop(shape, segments)
        if loop:
            loops.append(loop)
    loops.extend(_line_outline_loops(outlines))
    return loops


def _outline_layer_id(shape):
    if hasattr(shape, "layer_id"):
        return getattr(shape, "layer_id", None)
    try:
        return (shape or {}).get("layer_id")
    except AttributeError:
        return None


def _outline_loops_by_layer(outlines, fallback_layer_id=None, segments=96):
    """Group closed Outline boundaries by owning copper reference layer."""
    grouped = {}
    for shape in outlines or []:
        layer_id = _outline_layer_id(shape)
        if layer_id is None:
            layer_id = fallback_layer_id
        grouped.setdefault(layer_id, []).append(shape)
    return dict((layer_id, _outline_loops(shapes, segments))
                for layer_id, shapes in grouped.items())


def _loop_perimeter(loop):
    pts = list(loop or [])
    if len(pts) < 2:
        return 0.0
    return sum(math.hypot(pts[(i + 1) % len(pts)][0] - pts[i][0],
                          pts[(i + 1) % len(pts)][1] - pts[i][1])
               for i in range(len(pts)))


def _resample_loop(loop, count):
    """Resample a closed polygon at equal perimeter intervals."""
    pts = list(loop or [])
    count = max(3, int(count))
    if len(pts) < 3:
        return pts
    edges = []
    total = 0.0
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        edges.append((a, b, length, total))
        total += length
    if total <= 1e-12:
        return [pts[0]] * count
    result = []
    edge_index = 0
    for k in range(count):
        target = total * float(k) / float(count)
        while edge_index + 1 < len(edges) and target > edges[edge_index][3] + edges[edge_index][2]:
            edge_index += 1
        a, b, length, start = edges[edge_index]
        t = 0.0 if length <= 1e-12 else (target - start) / length
        result.append((a[0] + (b[0] - a[0]) * t,
                       a[1] + (b[1] - a[1]) * t))
    return result


def _aligned_loop_pair(first, second, count=None):
    """Return equally sampled/oriented loops with corresponding vertices."""
    n = int(count or max(24, len(first or []), len(second or [])))
    n = min(160, max(3, n))
    a = _resample_loop(first, n)
    b = _resample_loop(second, n)
    if len(a) < 3 or len(b) < 3:
        return a, b
    if _polygon_area(a) * _polygon_area(b) < 0.0:
        b.reverse()
    # Choose the cyclic phase that produces the shortest side-wall connections.
    best_shift, best_cost = 0, None
    for shift in range(n):
        cost = 0.0
        for i in range(n):
            q = b[(i + shift) % n]
            dx = a[i][0] - q[0]; dy = a[i][1] - q[1]
            cost += dx * dx + dy * dy
        if best_cost is None or cost < best_cost:
            best_cost, best_shift = cost, shift
    if best_shift:
        b = [b[(i + best_shift) % n] for i in range(n)]
    return a, b


def _sorted_loops(loops):
    return sorted((list(loop) for loop in (loops or []) if len(loop) >= 3),
                  key=lambda loop: abs(_polygon_area(loop)), reverse=True)


def _interpolate_loop_sets(first, second, fraction):
    """Interpolate corresponding closed boundaries for an unoutlined layer."""
    a_sets, b_sets = _sorted_loops(first), _sorted_loops(second)
    out = []
    count = max(len(a_sets), len(b_sets))
    t = max(0.0, min(1.0, float(fraction)))
    for i in range(count):
        if i < len(a_sets) and i < len(b_sets):
            a, b = _aligned_loop_pair(a_sets[i], b_sets[i])
            out.append([(a[j][0] + (b[j][0] - a[j][0]) * t,
                         a[j][1] + (b[j][1] - a[j][1]) * t)
                        for j in range(min(len(a), len(b)))])
        elif i < len(a_sets):
            out.append(list(a_sets[i]))
        else:
            out.append(list(b_sets[i]))
    return out


def _polygon_area(points):
    return 0.5 * sum(points[i][0] * points[(i + 1) % len(points)][1] -
                     points[(i + 1) % len(points)][0] * points[i][1]
                     for i in range(len(points)))


def _point_in_triangle_2d(p, a, b, c, eps=1e-10):
    def cross(u, v, w):
        return (v[0]-u[0])*(w[1]-u[1]) - (v[1]-u[1])*(w[0]-u[0])
    c1, c2, c3 = cross(a, b, p), cross(b, c, p), cross(c, a, p)
    return ((c1 >= -eps and c2 >= -eps and c3 >= -eps) or
            (c1 <= eps and c2 <= eps and c3 <= eps))


def _triangulate_polygon(points):
    """Ear-clip a simple polygon; returns triples of vertex indices.

    Rectangle/circle/ellipse loops are convex, but supporting ear clipping here
    also lets a closed chain of Outline line segments define a concave board.
    """
    pts = list(points or [])
    if len(pts) < 3:
        return []
    if _polygon_area(pts) < 0.0:
        pts.reverse()
        reverse_map = list(reversed(range(len(points))))
    else:
        reverse_map = list(range(len(points)))
    idx = list(range(len(pts)))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < len(pts) * len(pts):
        guard += 1
        clipped = False
        for k in range(len(idx)):
            ia, ib, ic = idx[k-1], idx[k], idx[(k+1) % len(idx)]
            a, b, c = pts[ia], pts[ib], pts[ic]
            cross = (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
            if cross <= 1e-12:
                continue
            if any(_point_in_triangle_2d(pts[j], a, b, c)
                   for j in idx if j not in (ia, ib, ic)):
                continue
            tris.append((reverse_map[ia], reverse_map[ib], reverse_map[ic]))
            del idx[k]
            clipped = True
            break
        if not clipped:
            # Degenerate/self-intersecting input: use a fan rather than dropping
            # the board entirely.  Normal Outline primitives never hit this.
            return [(0, i, i + 1) for i in range(1, len(points) - 1)]
    if len(idx) == 3:
        tris.append(tuple(reverse_map[i] for i in idx))
    return tris


def loops_bounds(loops):
    """Return ``(minx, miny, maxx, maxy)`` for a set of loops, or ``None``."""
    xs = [p[0] for loop in (loops or ()) for p in loop]
    ys = [p[1] for loop in (loops or ()) for p in loop]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _point_in_polygon(x, y, polygon):
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-30) + xi:
                inside = not inside
        j = i
    return inside


def point_in_loops(point, loops):
    """Even/odd containment: nested loops are holes, disjoint loops islands."""
    x, y = float(point[0]), float(point[1])
    return sum(1 for loop in (loops or ()) if len(loop) >= 3
               and _point_in_polygon(x, y, loop)) % 2 == 1


# Public, underscore-free aliases.
polygon_area = _polygon_area
triangulate_polygon = _triangulate_polygon
resample_loop = _resample_loop
aligned_loop_pair = _aligned_loop_pair
sorted_loops = _sorted_loops
interpolate_loop_sets = _interpolate_loop_sets
outline_loops = _outline_loops
outline_loops_by_layer = _outline_loops_by_layer
