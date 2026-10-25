# -*- coding: utf-8 -*-
"""Document-free PCB routing primitives and geometry helpers.

The custom editor keeps routed copper as plain Python data.  This module is
Qt/FreeCAD independent so snapping, via spans and ratsnest topology can be
regression-tested without a GUI.

Coordinates are board/editor millimetres with +Y down, matching the 2D scene.
"""

import math
import uuid

__all__ = [
    "Track", "Via", "route45", "closest_point_on_segment", "dist_to_segment",
    "via_spans_layer", "via_spans_overlap", "route_snap", "build_copper_topology",
    "routing_context", "assign_connection_nets", "ratsnest_segments", "pad_key",
    "pad_contains_point", "point_pad_distance", "track_touches_pad",
    "via_touches_pad", "automatic_via_target_layer",
    "route_conflicts", "via_conflicts", "route_outline_conflicts",
    "via_outline_conflicts", "via_layer_outline_conflicts", "clip_polyline_before_conflict",
    "drag_track_segment", "drag_track_corner", "drag_via_attached_points",
    "normalize_track_points", "delete_track_segment", "join_track_endpoints",
    "snap_direction_45", "walkaround_route", "conflict_boxes",
    "SpatialGrid", "ObstacleIndex", "BoardOutline", "board_outline",
    "track_bbox", "via_bbox", "pad_bbox", "bbox_overlap",
]

EPS = 1e-9


def _uid(value=None):
    return str(value or uuid.uuid4().hex)


class Track(object):
    def __init__(self, uid=None, layer_id=None, width=0.25, points=None, net=None):
        self.uid = _uid(uid)
        self.layer_id = layer_id
        self.width = max(0.001, float(width))
        # Canonicalize routed geometry at the model boundary: consecutive
        # collinear pieces in the same direction are one logical straight segment.
        # This keeps selection/dragging segment-aware instead of exposing routing
        # click vertices as artificial sub-segments.
        self.points = normalize_track_points(points or [])
        self.net = str(net) if net not in (None, "") else None

    def snapshot(self):
        return {
            "uid": self.uid,
            "layer_id": self.layer_id,
            "width": self.width,
            "points": [list(p) for p in self.points],
            "net": self.net,
        }

    @classmethod
    def restore(cls, data):
        data = data or {}
        return cls(data.get("uid"), data.get("layer_id"), data.get("width", 0.25),
                   data.get("points", []), data.get("net"))


class Via(object):
    def __init__(self, uid=None, x=0.0, y=0.0, diameter=0.8, hole_diameter=0.0,
                 start_layer_id=None, end_layer_id=None, net=None):
        self.uid = _uid(uid)
        self.x = float(x); self.y = float(y)
        self.diameter = max(0.001, float(diameter))
        self.hole_diameter = max(0.0, min(float(hole_diameter), self.diameter))
        self.start_layer_id = start_layer_id
        self.end_layer_id = end_layer_id
        self.net = str(net) if net not in (None, "") else None

    def snapshot(self):
        return {
            "uid": self.uid, "x": self.x, "y": self.y,
            "diameter": self.diameter, "hole_diameter": self.hole_diameter,
            "start_layer_id": self.start_layer_id, "end_layer_id": self.end_layer_id,
            "net": self.net,
        }

    @classmethod
    def restore(cls, data):
        data = data or {}
        return cls(data.get("uid"), data.get("x", 0.0), data.get("y", 0.0),
                   data.get("diameter", 0.8), data.get("hole_diameter", 0.0),
                   data.get("start_layer_id"), data.get("end_layer_id"), data.get("net"))


_SQRT2_2 = math.sqrt(2.0) / 2.0

# The eight directions KiCad's default "45 degree" routing mode allows.
_DIRS45 = (
    (1.0, 0.0), (_SQRT2_2, _SQRT2_2), (0.0, 1.0), (-_SQRT2_2, _SQRT2_2),
    (-1.0, 0.0), (-_SQRT2_2, -_SQRT2_2), (0.0, -1.0), (_SQRT2_2, -_SQRT2_2),
)


def _normalize(vx, vy):
    n = math.hypot(vx, vy)
    if n <= EPS:
        return None
    return (vx / n, vy / n)


def snap_direction_45(vx, vy):
    """Nearest of the eight H/V/45 unit directions, or ``None`` for a null vector."""
    unit = _normalize(vx, vy)
    if unit is None:
        return None
    return max(_DIRS45, key=lambda d: unit[0] * d[0] + unit[1] * d[1])


def _is_45(vx, vy):
    if abs(vx) <= EPS and abs(vy) <= EPS:
        return True
    return abs(vx) <= EPS or abs(vy) <= EPS or abs(abs(vx) - abs(vy)) <= 1e-6


def _line_intersection(p, u, q, v):
    """Intersect line ``p + t*u`` with ``q + s*v``; ``None`` when parallel."""
    det = u[0] * (-v[1]) - u[1] * (-v[0])
    if abs(det) <= 1e-12:
        return None
    rx, ry = q[0] - p[0], q[1] - p[1]
    t = (rx * (-v[1]) - ry * (-v[0])) / det
    return (p[0] + t * u[0], p[1] + t * u[1])


def _compact(points, tol=EPS):
    """Drop consecutive duplicate vertices, keeping the polyline inspectable."""
    out = []
    for p in points:
        if out and abs(out[-1][0] - p[0]) <= tol and abs(out[-1][1] - p[1]) <= tol:
            continue
        out.append((float(p[0]), float(p[1])))
    return out


def _collinear_simplify(points, tol=1e-7):
    """Collapse consecutive same-direction collinear pieces into one segment.

    A 180-degree reversal is deliberately *not* collapsed: although the three
    points are mathematically collinear, the middle point is a real routing turn
    and removing it would change the copper geometry.
    """
    pts = _compact(points)
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = out[-1], pts[i], pts[i + 1]
        abx, aby = b[0] - a[0], b[1] - a[1]
        bcx, bcy = c[0] - b[0], c[1] - b[1]
        cross = abx * bcy - aby * bcx
        scale = max(1.0, abs(abx) + abs(aby) + abs(bcx) + abs(bcy))
        same_direction = (abx * bcx + aby * bcy) > tol
        if abs(cross) > tol * scale or not same_direction:
            out.append(b)
    out.append(pts[-1])
    return out


def normalize_track_points(points, tol=1e-7):
    """Return canonical track vertices with no redundant straight-line joints."""
    return _collinear_simplify(points, tol=tol)


def delete_track_segment(track, segment_index):
    """Return the track pieces left after removing one logical segment.

    The original uid is retained on the first surviving piece so external
    references remain as stable as possible.  Deleting an interior segment
    splits one Track into two electrically disconnected Track objects.
    """
    if not isinstance(track, Track):
        return []
    pts = normalize_track_points(track.points)
    i = int(segment_index)
    if i < 0 or i + 1 >= len(pts):
        return [Track.restore(track.snapshot())]

    candidates = [pts[:i + 1], pts[i + 1:]]
    surviving = [part for part in candidates if len(part) >= 2]
    result = []
    for n, part in enumerate(surviving):
        result.append(Track(
            uid=track.uid if n == 0 else None,
            layer_id=track.layer_id, width=track.width, points=part, net=track.net))
    return result


def join_track_endpoints(first, second, tol=1e-6):
    """Weld two compatible Track objects that meet endpoint-to-endpoint.

    ``first`` supplies the uid/metadata of the returned Track.  The helper is
    deliberately endpoint-only: a route landing in the middle of another
    Track is a real T-junction and must remain a separate polyline object.
    Width changes likewise remain separate because one Track has one width.
    """
    if not isinstance(first, Track) or not isinstance(second, Track):
        return None
    if first.layer_id != second.layer_id:
        return None
    if abs(float(first.width) - float(second.width)) > 1e-9:
        return None
    if first.net not in (None, "") and second.net not in (None, "") and first.net != second.net:
        return None
    a = normalize_track_points(first.points)
    b = normalize_track_points(second.points)
    if len(a) < 2 or len(b) < 2:
        return None

    def same(p, q):
        return math.hypot(float(p[0]) - float(q[0]),
                          float(p[1]) - float(q[1])) <= float(tol)

    # Orient the two polylines so the shared endpoint is a[-1] == b[0].
    if same(a[-1], b[0]):
        left, right = a, b
    elif same(a[-1], b[-1]):
        left, right = a, list(reversed(b))
    elif same(a[0], b[0]):
        left, right = list(reversed(a)), b
    elif same(a[0], b[-1]):
        left, right = b, a
    else:
        return None

    net = first.net if first.net not in (None, "") else second.net
    return Track(uid=first.uid, layer_id=first.layer_id, width=first.width,
                 points=left + right[1:], net=net)


def drag_track_segment(points, segment_index, dx, dy,
                       anchor_start=False, anchor_end=False, keep45=False):
    """Return polyline geometry for an interactive PCB track-segment drag.

    The selected segment is translated by ``dx,dy``. Adjacent segments stretch
    automatically because they share the moved vertices. When an outer endpoint
    is electrically anchored to a pad/via/other track, the endpoint itself is
    retained and a short connector (dogleg) is inserted to the translated
    segment.

    With ``keep45`` the neighbouring segments keep their original directions and
    the shared joints slide along those directions instead, which is how KiCad's
    dragger preserves a 45-degree route.  Where that is impossible (a parallel
    neighbour, or an anchored outer end) the dogleg behaviour is used instead.
    """
    # The caller's segment index refers to the supplied point list.  Track
    # models are canonicalized on construction, but keeping raw-call semantics
    # here preserves compatibility for helpers/tests/external callers.
    pts = [(float(p[0]), float(p[1])) for p in (points or [])]
    i = int(segment_index)
    if i < 0 or i + 1 >= len(pts):
        return pts
    dx = float(dx); dy = float(dy)
    moved_start = (pts[i][0] + dx, pts[i][1] + dy)
    moved_end = (pts[i + 1][0] + dx, pts[i + 1][1] + dy)

    if keep45:
        result = _drag_segment_keep45(pts, i, moved_start, moved_end,
                                      anchor_start, anchor_end)
        if result is not None:
            return result

    out = list(pts[:i])
    if i == 0 and bool(anchor_start):
        out.append(pts[0])
    out.append(moved_start)
    out.append(moved_end)
    if i + 1 == len(pts) - 1 and bool(anchor_end):
        out.append(pts[-1])
    else:
        out.extend(pts[i + 2:])

    # Keep the live model compact at zero/small deltas; repeated equal vertices
    # are geometrically harmless but make saved tracks harder to inspect.
    return normalize_track_points(out)


def _drag_segment_keep45(pts, i, moved_start, moved_end, anchor_start, anchor_end):
    """45-preserving joint solve for :func:`drag_track_segment`.

    Returns ``None`` when the caller should fall back to the plain translation,
    so a drag never fails outright just because the geometry is awkward.
    """
    u = _normalize(moved_end[0] - moved_start[0], moved_end[1] - moved_start[1])
    if u is None:
        return None

    head = []
    start_point = moved_start
    if i == 0:
        if anchor_start:
            # An anchored end cannot move, so join it with a 45 dogleg.
            head = [pts[0]] + route45(pts[0], moved_start)[:-1]
        # else: the free end simply travels with the segment.
    else:
        prev = pts[i - 1]
        v = _normalize(pts[i][0] - prev[0], pts[i][1] - prev[1])
        joint = _line_intersection(moved_start, u, prev, v) if v else None
        if joint is not None:
            head = list(pts[:i]) + [joint]
            start_point = joint
        else:
            # Parallel neighbour: no intersection exists, so bridge with 45.
            head = list(pts[:i]) + route45(prev, moved_start)[:-1]

    tail = []
    end_point = moved_end
    last = len(pts) - 1
    if i + 1 == last:
        if anchor_end:
            tail = route45(moved_end, pts[-1])
    else:
        nxt = pts[i + 2]
        w = _normalize(nxt[0] - pts[i + 1][0], nxt[1] - pts[i + 1][1])
        joint = _line_intersection(moved_end, u, nxt, w) if w else None
        if joint is not None:
            tail = [joint] + list(pts[i + 2:])
            end_point = joint
        else:
            tail = route45(moved_end, nxt)[:-1] + list(pts[i + 2:])

    out = normalize_track_points(head + [start_point, end_point] + tail)
    if len(out) < 2:
        return None
    return out


def drag_track_corner(points, index, dx, dy, keep45=False):
    """Move a single track vertex, KiCad's corner drag.

    Without ``keep45`` the vertex simply follows the cursor.  With it, the two
    adjacent segments are re-fitted onto the 45-degree grid so the route keeps
    legal angles instead of drifting to arbitrary ones.
    """
    pts = [(float(p[0]), float(p[1])) for p in (points or [])]
    k = int(index)
    if k < 0 or k >= len(pts):
        return pts
    target = (pts[k][0] + float(dx), pts[k][1] + float(dy))
    if not keep45:
        pts[k] = target
        return normalize_track_points(pts)

    head = list(pts[:k])
    tail = list(pts[k + 1:])
    out = []
    if head:
        out.extend(head)
        out.extend(route45(head[-1], target)[:-1])
    out.append(target)
    if tail:
        out.extend(route45(target, tail[0])[:-1])
        out.extend(tail)
    return _collinear_simplify(out)


def drag_via_attached_points(points, indices, dx, dy, keep45=False):
    """Move the track vertices that follow a dragged via.

    The listed vertices translate with the via.  With ``keep45`` the segment
    joining each moved vertex to the rest of the track is rebuilt with 45-degree
    geometry, so dragging a via does not leave arbitrary-angle stubs behind.
    """
    pts = [(float(p[0]), float(p[1])) for p in (points or [])]
    moved = sorted(set(int(i) for i in (indices or ()) if 0 <= int(i) < len(pts)))
    if not moved:
        return pts
    dx = float(dx); dy = float(dy)
    for k in moved:
        pts[k] = (pts[k][0] + dx, pts[k][1] + dy)
    if not keep45:
        return normalize_track_points(pts)

    moved_set = set(moved)
    out = []
    for k, point in enumerate(pts):
        if out:
            previous_moved = (k - 1) in moved_set
            current_moved = k in moved_set
            if previous_moved != current_moved:
                # Exactly one end of this segment travelled with the via, so
                # rebuild it on the 45 grid instead of leaving a skewed stub.
                out.extend(route45(out[-1], point)[:-1])
        out.append(point)
    return _collinear_simplify(out)


def _xy(p):
    if isinstance(p, dict):
        return float(p.get("x", 0.0)), float(p.get("y", 0.0))
    if hasattr(p, "x"):
        x = p.x() if callable(p.x) else p.x
        y = p.y() if callable(p.y) else p.y
        return float(x), float(y)
    return float(p[0]), float(p[1])


def pad_key(ref, pin):
    return "%s\x1f%s" % (str(ref or ""), str(pin or ""))


def dist2(a, b):
    ax, ay = _xy(a); bx, by = _xy(b)
    dx, dy = ax - bx, ay - by
    return dx * dx + dy * dy


def closest_point_on_segment(point, a, b):
    px, py = _xy(point); ax, ay = _xy(a); bx, by = _xy(b)
    vx, vy = bx - ax, by - ay
    l2 = vx * vx + vy * vy
    t = ((px - ax) * vx + (py - ay) * vy) / l2 if l2 else 0.0
    t = max(0.0, min(1.0, t))
    return (ax + t * vx, ay + t * vy)


def dist_to_segment(point, a, b):
    q = closest_point_on_segment(point, a, b)
    return math.sqrt(dist2(point, q))


def _orient(a, b, c):
    ax, ay = _xy(a); bx, by = _xy(b); cx, cy = _xy(c)
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _on_segment(a, b, p, eps=1e-9):
    ax, ay = _xy(a); bx, by = _xy(b); px, py = _xy(p)
    return (abs(_orient(a, b, p)) <= eps and
            min(ax, bx) - eps <= px <= max(ax, bx) + eps and
            min(ay, by) - eps <= py <= max(ay, by) + eps)


def segments_intersect(a, b, c, d):
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if ((o1 > 0 > o2 or o2 > 0 > o1) and (o3 > 0 > o4 or o4 > 0 > o3)):
        return True
    return (_on_segment(a, b, c) or _on_segment(a, b, d) or
            _on_segment(c, d, a) or _on_segment(c, d, b))


def segment_distance(a, b, c, d):
    # Cheap axis-aligned rejection first: the exact test below costs four
    # point/segment projections plus an orientation test, and on a routed board
    # the overwhelming majority of segment pairs are nowhere near each other.
    if (min(a[0], b[0]) > max(c[0], d[0]) or min(c[0], d[0]) > max(a[0], b[0]) or
            min(a[1], b[1]) > max(c[1], d[1]) or min(c[1], d[1]) > max(a[1], b[1])):
        return min(dist_to_segment(a, c, d), dist_to_segment(b, c, d),
                   dist_to_segment(c, a, b), dist_to_segment(d, a, b))
    if segments_intersect(a, b, c, d):
        return 0.0
    return min(dist_to_segment(a, c, d), dist_to_segment(b, c, d),
               dist_to_segment(c, a, b), dist_to_segment(d, a, b))


# ---------------------------------------------------------------------------
# Bounding boxes and broad-phase indexing
#
# Every clearance predicate in this module is an exact envelope test, which is
# what makes the router trustworthy but also what makes it quadratic when it is
# applied to every pair of objects on a board.  The helpers below add a cheap
# rejection layer: an axis-aligned box test first, then a uniform-grid broad
# phase so only objects that share a cell are ever compared exactly.
# ---------------------------------------------------------------------------

_INF_BOX = (float("inf"), float("inf"), float("-inf"), float("-inf"))


def _points_bbox(points, margin=0.0):
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    if not xs:
        return _INF_BOX
    return (min(xs) - margin, min(ys) - margin,
            max(xs) + margin, max(ys) + margin)


def track_bbox(track, margin=0.0):
    """Axis-aligned envelope of a track including half its copper width."""
    return _points_bbox(getattr(track, "points", ()) or (),
                        float(getattr(track, "width", 0.0)) / 2.0 + margin)


def via_bbox(via, margin=0.0):
    r = float(getattr(via, "diameter", 0.0)) / 2.0 + margin
    x, y = float(via.x), float(via.y)
    return (x - r, y - r, x + r, y + r)


def pad_bbox(pad, margin=0.0):
    """Conservative envelope of a rotated pad.

    The half-diagonal is used instead of the exact rotated extent so the box
    stays valid for any rotation without recomputing trigonometry per query.
    """
    w = abs(float(pad.get("w", 0.0))); h = abs(float(pad.get("h", 0.0)))
    r = math.hypot(w, h) / 2.0 + margin
    x, y = float(pad["x"]), float(pad["y"])
    return (x - r, y - r, x + r, y + r)


def bbox_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _bbox_union(boxes):
    boxes = [b for b in boxes if b[0] <= b[2]]
    if not boxes:
        return _INF_BOX
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


class SpatialGrid(object):
    """Uniform-grid broad phase over axis-aligned boxes.

    A uniform grid is deliberately chosen over a tree: board copper is spread
    fairly evenly over a bounded area, insertion is O(1) amortised, and there
    is no rebalancing cost when the router re-indexes between mouse events.
    """

    __slots__ = ("cell", "buckets", "_all", "_oversized")

    def __init__(self, entries=(), cell=None):
        """``entries`` is an iterable of ``(bbox, payload)`` pairs."""
        entries = list(entries)
        self.cell = float(cell) if cell else self._auto_cell(entries)
        self.buckets = {}
        self._all = []
        # Objects too large to bucket sensibly are kept in a short side list so
        # a query never has to rescan (or re-bucket) the full population.
        self._oversized = []
        for bbox, payload in entries:
            self.insert(bbox, payload)

    @staticmethod
    def _auto_cell(entries):
        """Pick a cell close to the typical object size.

        Too small a cell makes a long track occupy hundreds of buckets; too
        large a cell degenerates back to a linear scan.  The mean extent of the
        indexed boxes is a good compromise and costs one pass to compute.
        """
        spans = []
        for bbox, _payload in entries:
            if bbox[0] <= bbox[2]:
                spans.append(max(bbox[2] - bbox[0], bbox[3] - bbox[1]))
        if not spans:
            return 1.0
        mean = sum(spans) / float(len(spans))
        return max(0.25, min(50.0, mean if mean > EPS else 1.0))

    def _cells(self, bbox):
        if bbox[0] > bbox[2]:
            return ()
        c = self.cell
        i0 = int(math.floor(bbox[0] / c)); i1 = int(math.floor(bbox[2] / c))
        j0 = int(math.floor(bbox[1] / c)); j1 = int(math.floor(bbox[3] / c))
        # Guard against a pathological box (for example an outline that spans
        # the whole sheet) turning one insert into millions of buckets.
        if (i1 - i0 + 1) * (j1 - j0 + 1) > 4096:
            return None
        return [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]

    def insert(self, bbox, payload):
        entry = (bbox, payload)
        self._all.append(entry)
        cells = self._cells(bbox)
        if cells is None:
            # Oversized object: recorded once here so queries can pick it up
            # without re-testing every entry in the grid.
            self._oversized.append(entry)
            return
        for key in cells:
            self.buckets.setdefault(key, []).append(entry)

    def query(self, bbox):
        """Return payloads whose indexed box overlaps *bbox* (no duplicates)."""
        cells = self._cells(bbox)
        if cells is None:
            return [p for b, p in self._all if bbox_overlap(b, bbox)]
        out = []
        seen = set()
        buckets = self.buckets
        for key in cells:
            bucket = buckets.get(key)
            if not bucket:
                continue
            for b, payload in bucket:
                ident = id(payload)
                if ident in seen:
                    continue
                seen.add(ident)
                if bbox_overlap(b, bbox):
                    out.append(payload)
        for b, payload in self._oversized:
            if id(payload) in seen:
                continue
            seen.add(id(payload))
            if bbox_overlap(b, bbox):
                out.append(payload)
        return out

    def pairs(self):
        """Yield each unordered pair of payloads that share at least one cell."""
        seen = set()
        for bucket in self.buckets.values():
            n = len(bucket)
            for i in range(n):
                bi, pi = bucket[i]
                for j in range(i + 1, n):
                    bj, pj = bucket[j]
                    if not bbox_overlap(bi, bj):
                        continue
                    key = (id(pi), id(pj))
                    if key[0] > key[1]:
                        key = (key[1], key[0])
                    if key in seen:
                        continue
                    seen.add(key)
                    yield pi, pj
        for bo, po in self._oversized:
            for b, p in self._all:
                if p is po or not bbox_overlap(bo, b):
                    continue
                key = (id(po), id(p))
                if key[0] > key[1]:
                    key = (key[1], key[0])
                if key in seen:
                    continue
                seen.add(key)
                yield po, p


class ObstacleIndex(object):
    """Broad-phase index over the pads/tracks/vias a router must avoid.

    Building this once per interactive event and reusing it across the many
    clearance probes of a single mouse move (a clip binary search alone runs
    the predicate ~20 times) is what keeps interactive routing responsive.
    """

    __slots__ = ("pads", "tracks", "vias", "layer_order",
                 "_pad_grid", "_track_grid", "_via_grid", "margin")

    def __init__(self, pads=(), tracks=(), vias=(), layer_order=(), margin=0.5):
        self.pads = list(pads or ())
        self.tracks = list(tracks or ())
        self.vias = list(vias or ())
        self.layer_order = list(layer_order or ())
        self.margin = max(0.0, float(margin))
        m = self.margin
        self._pad_grid = SpatialGrid((pad_bbox(p, m), p) for p in self.pads)
        self._track_grid = SpatialGrid((track_bbox(t, m), t) for t in self.tracks)
        self._via_grid = SpatialGrid((via_bbox(v, m), v) for v in self.vias)

    def nearby_pads(self, bbox):
        return self._pad_grid.query(bbox)

    def nearby_tracks(self, bbox):
        return self._track_grid.query(bbox)

    def nearby_vias(self, bbox):
        return self._via_grid.query(bbox)


def route45(a, b, posture=0):
    """Connect *a* to *b* with H/V/45 geometry ending exactly at *b*.

    ``posture`` toggles which of the two valid two-segment solutions is used,
    mirroring KiCad's '/' posture switch.
    """
    ax, ay = _xy(a); bx, by = _xy(b)
    dx, dy = bx - ax, by - ay
    adx, ady = abs(dx), abs(dy)
    if adx < EPS and ady < EPS:
        return []
    if adx < EPS or ady < EPS or abs(adx - ady) < EPS:
        return [(bx, by)]
    if not posture:
        mid = ((ax + math.copysign(ady, dx), by) if adx > ady
               else (bx, ay + math.copysign(adx, dy)))
    else:
        mid = ((bx - math.copysign(ady, dx), ay) if adx > ady
               else (ax, by - math.copysign(adx, dy)))
    if dist2(mid, (ax, ay)) < EPS or dist2(mid, (bx, by)) < EPS:
        return [(bx, by)]
    return [mid, (bx, by)]


def _layer_index(layer_id, layer_order):
    try:
        return list(layer_order).index(layer_id)
    except ValueError:
        return -1


def via_spans_layer(via, layer_id, layer_order):
    a = _layer_index(via.start_layer_id, layer_order)
    b = _layer_index(via.end_layer_id, layer_order)
    i = _layer_index(layer_id, layer_order)
    if min(a, b, i) < 0:
        return False
    lo, hi = sorted((a, b))
    return lo <= i <= hi


def via_spans_overlap(a, b, layer_order):
    a0 = _layer_index(a.start_layer_id, layer_order); a1 = _layer_index(a.end_layer_id, layer_order)
    b0 = _layer_index(b.start_layer_id, layer_order); b1 = _layer_index(b.end_layer_id, layer_order)
    if min(a0, a1, b0, b1) < 0:
        return False
    alo, ahi = sorted((a0, a1)); blo, bhi = sorted((b0, b1))
    return max(alo, blo) <= min(ahi, bhi)


def _pad_local(point, pad):
    px, py = _xy(point)
    dx, dy = px - float(pad["x"]), py - float(pad["y"])
    angle = -math.radians(float(pad.get("rot", 0.0) or 0.0))
    c, s = math.cos(angle), math.sin(angle)
    return c * dx - s * dy, s * dx + c * dy


def pad_contains_point(point, pad):
    x, y = _pad_local(point, pad)
    w, h = max(EPS, float(pad.get("w", 0.0))), max(EPS, float(pad.get("h", 0.0)))
    shape = str(pad.get("shape", "rect"))
    if shape == "circle" or (shape == "oval" and abs(w - h) < EPS):
        return x * x + y * y <= (w / 2.0) ** 2 + EPS
    if shape == "oval":
        if w >= h:
            r = h / 2.0; half = max(0.0, (w - h) / 2.0)
            qx = max(-half, min(half, x))
            return (x - qx) ** 2 + y ** 2 <= r * r + EPS
        r = w / 2.0; half = max(0.0, (h - w) / 2.0)
        qy = max(-half, min(half, y))
        return x ** 2 + (y - qy) ** 2 <= r * r + EPS
    return abs(x) <= w / 2.0 + EPS and abs(y) <= h / 2.0 + EPS


def point_pad_distance(point, pad):
    x, y = _pad_local(point, pad)
    w, h = max(EPS, float(pad.get("w", 0.0))), max(EPS, float(pad.get("h", 0.0)))
    shape = str(pad.get("shape", "rect"))
    if shape == "circle" or (shape == "oval" and abs(w - h) < EPS):
        return max(0.0, math.hypot(x, y) - w / 2.0)
    if shape == "oval":
        if w >= h:
            r = h / 2.0; half = max(0.0, (w - h) / 2.0)
            qx = max(-half, min(half, x))
            return max(0.0, math.hypot(x - qx, y) - r)
        r = w / 2.0; half = max(0.0, (h - w) / 2.0)
        qy = max(-half, min(half, y))
        return max(0.0, math.hypot(x, y - qy) - r)
    dx = max(abs(x) - w / 2.0, 0.0); dy = max(abs(y) - h / 2.0, 0.0)
    return math.hypot(dx, dy)


def _rect_corners(pad):
    w, h = float(pad.get("w", 0.0)), float(pad.get("h", 0.0))
    a = math.radians(float(pad.get("rot", 0.0) or 0.0)); c, s = math.cos(a), math.sin(a)
    cx, cy = float(pad["x"]), float(pad["y"])
    return [(cx + c*x - s*y, cy + s*x + c*y)
            for x, y in ((-w/2,-h/2),(w/2,-h/2),(w/2,h/2),(-w/2,h/2))]


def _segment_pad_distance(a, b, pad):
    # Circle/oval: sample the capsule centreline exactly where possible.
    shape = str(pad.get("shape", "rect"))
    w, h = float(pad.get("w", 0.0)), float(pad.get("h", 0.0))
    if shape == "circle" or (shape == "oval" and abs(w-h) < EPS):
        return max(0.0, dist_to_segment((pad["x"], pad["y"]), a, b) - w/2.0)
    # Rotated rectangle approximation for rect/roundrect/trapezoid/custom and
    # as a conservative envelope for a non-circular oval.  It is exact for the
    # overwhelmingly common rectangular SMD pads.
    A = _pad_local(a, pad); B = _pad_local(b, pad)
    hx, hy = w/2.0, h/2.0
    inside = lambda p: abs(p[0]) <= hx and abs(p[1]) <= hy
    if inside(A) or inside(B):
        return 0.0
    corners = [(-hx,-hy),(hx,-hy),(hx,hy),(-hx,hy)]
    for i in range(4):
        if segments_intersect(A, B, corners[i], corners[(i+1)%4]):
            return 0.0
    def point_rect(p):
        return math.hypot(max(abs(p[0])-hx,0.0), max(abs(p[1])-hy,0.0))
    d = min(point_rect(A), point_rect(B))
    for q in corners:
        d = min(d, dist_to_segment(q, A, B))
    return d


def track_touches_pad(track, pad, eps=0.02):
    if track.layer_id != pad.get("layer_id"):
        return False
    points = track.points
    if len(points) < 1:
        return False
    limit = track.width / 2.0 + eps
    box = pad_bbox(pad, limit)
    if not bbox_overlap(_points_bbox(points), box):
        return False
    for i in range(1, len(points)):
        a, b = points[i-1], points[i]
        if not bbox_overlap(_points_bbox((a, b)), box):
            continue
        if _segment_pad_distance(a, b, pad) <= limit:
            return True
    return False


def via_touches_pad(via, pad, layer_order, eps=0.02):
    if not via_spans_layer(via, pad.get("layer_id"), layer_order):
        return False
    return point_pad_distance((via.x, via.y), pad) <= via.diameter / 2.0 + eps


def tracks_touch(a, b, eps=0.02):
    if a.layer_id != b.layer_id:
        return False
    pa, pb = a.points, b.points
    if len(pa) < 2 or len(pb) < 2:
        return False
    limit = a.width/2.0 + b.width/2.0 + eps
    # Whole-track rejection before the O(n*m) segment sweep.
    if not bbox_overlap(_points_bbox(pa, limit), _points_bbox(pb)):
        return False
    boxes_b = [_points_bbox((pb[j-1], pb[j])) for j in range(1, len(pb))]
    for i in range(1, len(pa)):
        sa, ea = pa[i-1], pa[i]
        box_a = _points_bbox((sa, ea), limit)
        for j in range(1, len(pb)):
            if not bbox_overlap(box_a, boxes_b[j-1]):
                continue
            if segment_distance(sa, ea, pb[j-1], pb[j]) <= limit:
                return True
    return False


def track_touches_via(track, via, layer_order, eps=0.02):
    if not via_spans_layer(via, track.layer_id, layer_order):
        return False
    points = track.points
    if len(points) < 2:
        return False
    limit = track.width/2.0 + via.diameter/2.0 + eps
    centre = (via.x, via.y)
    box = (via.x - limit, via.y - limit, via.x + limit, via.y + limit)
    if not bbox_overlap(_points_bbox(points), box):
        return False
    for i in range(1, len(points)):
        a, b = points[i-1], points[i]
        if not bbox_overlap(_points_bbox((a, b)), box):
            continue
        if dist_to_segment(centre, a, b) <= limit:
            return True
    return False


def vias_touch(a, b, layer_order, eps=0.02):
    if not via_spans_overlap(a, b, layer_order):
        return False
    return math.hypot(a.x-b.x, a.y-b.y) <= a.diameter/2.0 + b.diameter/2.0 + eps


def _outline_data(shape):
    if hasattr(shape, "snapshot"):
        return dict(shape.snapshot())
    return dict(shape or {})


def _same_outline_point(a, b, tol=1e-4):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _closed_outline_loops(outlines, segments=96, tol=1e-4):
    """Return closed board-outline loops as plain XY point lists.

    Rectangle/circle/ellipse primitives are closed by definition.  Individual
    line primitives are chained by matching endpoints, so a board drawn from
    four line segments constrains routing just like a rectangle.  Open line
    chains intentionally do not constrain routing.
    """
    loops = []
    line_segments = []
    for shape in outlines or ():
        data = _outline_data(shape)
        kind = str(data.get("kind") or "").lower()
        if kind == "rectangle":
            cx = float(data.get("x", 0.0)); cy = float(data.get("y", 0.0))
            w = abs(float(data.get("width", 0.0))); h = abs(float(data.get("height", 0.0)))
            if w > EPS and h > EPS:
                loops.append([(cx-w/2.0, cy-h/2.0), (cx+w/2.0, cy-h/2.0),
                              (cx+w/2.0, cy+h/2.0), (cx-w/2.0, cy+h/2.0)])
        elif kind in ("circle", "ellipse"):
            cx = float(data.get("x", 0.0)); cy = float(data.get("y", 0.0))
            if kind == "circle":
                rx = ry = abs(float(data.get("radius", 0.0)))
            else:
                rx = abs(float(data.get("width", 0.0))) / 2.0
                ry = abs(float(data.get("height", 0.0))) / 2.0
            if rx > EPS and ry > EPS:
                count = max(24, int(segments))
                loops.append([(cx + rx * math.cos(2.0 * math.pi * i / count),
                               cy + ry * math.sin(2.0 * math.pi * i / count))
                              for i in range(count)])
        elif kind == "line":
            a = (float(data.get("x1", 0.0)), float(data.get("y1", 0.0)))
            b = (float(data.get("x2", 0.0)), float(data.get("y2", 0.0)))
            if not _same_outline_point(a, b, tol):
                line_segments.append((a, b))

    unused = list(line_segments)
    while unused:
        a, b = unused.pop(0)
        chain = [a, b]
        progressed = True
        while progressed and unused:
            progressed = False
            end = chain[-1]
            for i, (u, v) in enumerate(unused):
                if _same_outline_point(end, u, tol):
                    chain.append(v); unused.pop(i); progressed = True; break
                if _same_outline_point(end, v, tol):
                    chain.append(u); unused.pop(i); progressed = True; break
        if len(chain) >= 4 and _same_outline_point(chain[-1], chain[0], tol):
            chain.pop()
            loops.append(chain)
    return loops


class BoardOutline(object):
    """Closed board-outline loops prepared once for repeated routing queries.

    Deriving loops (and tessellating circles/ellipses) on every clearance probe
    was a real cost: a single interactive mouse move runs the probe about twenty
    times through the clip binary search.  This object caches the loops and
    indexes their edges so a boundary-distance query only tests nearby edges.
    """

    __slots__ = ("loops", "blocking", "_edge_grid", "_bbox", "_loop_boxes")

    def __init__(self, outlines=None, segments=96, tol=1e-4, loops=None, blocking=False):
        if loops is not None:
            self.loops = [[(float(x), float(y)) for x, y in loop]
                          for loop in loops if len(loop) >= 3]
        else:
            self.loops = _closed_outline_loops(outlines, segments=segments, tol=tol)
        # ``blocking`` marks a layer that has a board solid but no section
        # (the plane is above/below the solid): nothing may be placed there.
        self.blocking = bool(blocking) and not self.loops
        self._loop_boxes = [_points_bbox(loop) for loop in self.loops]
        entries = []
        for loop in self.loops:
            n = len(loop)
            for i in range(n):
                a, b = loop[i], loop[(i + 1) % n]
                entries.append((_points_bbox((a, b)), (a, b)))
        self._edge_grid = SpatialGrid(entries) if entries else None
        self._bbox = _bbox_union([b for b, _e in entries]) if entries else _INF_BOX

    @classmethod
    def from_loops(cls, loops, blocking=False):
        """Prepare already-closed loops, e.g. one board-solid cross-section."""
        return cls(loops=list(loops or []), blocking=blocking)

    @property
    def active(self):
        """``True`` when this boundary constrains placement at all."""
        return bool(self.loops) or self.blocking

    def __bool__(self):
        return self.active

    __nonzero__ = __bool__

    def contains(self, point):
        if self.blocking:
            return False
        px, py = _xy(point)
        count = 0
        for loop, box in zip(self.loops, self._loop_boxes):
            if px < box[0] or px > box[2] or py < box[1] or py > box[3]:
                continue
            if _point_in_polygon((px, py), loop):
                count += 1
        return count % 2 == 1

    def _edges_near(self, bbox):
        if self._edge_grid is None:
            return ()
        return self._edge_grid.query(bbox)

    def distance_to_point(self, point, limit):
        """Distance from *point* to the boundary, early-exiting past *limit*.

        Only the answer's relationship to ``limit`` matters to the callers, so
        edges further away than ``limit`` are never visited.
        """
        px, py = _xy(point)
        box = (px - limit, py - limit, px + limit, py + limit)
        best = float("inf")
        for a, b in self._edges_near(box):
            best = min(best, dist_to_segment((px, py), a, b))
            if best < limit:
                return best
        return best

    def distance_to_segment(self, a, b, limit):
        box = _points_bbox((a, b), limit)
        best = float("inf")
        for ea, eb in self._edges_near(box):
            best = min(best, segment_distance(a, b, ea, eb))
            if best < limit:
                return best
        return best


_OUTLINE_CACHE = {}
_OUTLINE_CACHE_LIMIT = 16


def _outline_signature(outlines):
    """Cheap identity for a set of outline primitives.

    Outline shapes are edited in place, so the signature has to include the
    geometry itself and not merely object identity.
    """
    parts = []
    for shape in outlines or ():
        data = _outline_data(shape)
        parts.append((
            str(data.get("kind") or ""),
            round(float(data.get("x", 0.0) or 0.0), 7),
            round(float(data.get("y", 0.0) or 0.0), 7),
            round(float(data.get("width", 0.0) or 0.0), 7),
            round(float(data.get("height", 0.0) or 0.0), 7),
            round(float(data.get("radius", 0.0) or 0.0), 7),
            round(float(data.get("x1", 0.0) or 0.0), 7),
            round(float(data.get("y1", 0.0) or 0.0), 7),
            round(float(data.get("x2", 0.0) or 0.0), 7),
            round(float(data.get("y2", 0.0) or 0.0), 7),
        ))
    return tuple(parts)


def board_outline(outlines):
    """Return a cached :class:`BoardOutline` for these outline primitives."""
    if isinstance(outlines, BoardOutline):
        return outlines
    if outlines is None:
        return _NO_OUTLINE
    key = _outline_signature(outlines)
    hit = _OUTLINE_CACHE.get(key)
    if hit is None:
        hit = BoardOutline(outlines)
        if len(_OUTLINE_CACHE) >= _OUTLINE_CACHE_LIMIT:
            _OUTLINE_CACHE.clear()
        _OUTLINE_CACHE[key] = hit
    return hit


_NO_OUTLINE = BoardOutline(loops=[])


def _point_in_polygon(point, polygon):
    """Even/odd point-in-polygon test; boundary proximity is handled separately."""
    x, y = _xy(point)
    inside = False
    n = len(polygon or ())
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]; xj, yj = polygon[j]
        if ((yi > y) != (yj > y)):
            cross_x = (xj - xi) * (y - yi) / ((yj - yi) or EPS) + xi
            if x < cross_x:
                inside = not inside
        j = i
    return inside


def _inside_outline_region(point, loops):
    # Even/odd fill naturally supports disjoint board islands and nested cutouts.
    return (sum(1 for loop in loops if _point_in_polygon(point, loop)) % 2) == 1


def _boundary_distance_to_segment(a, b, loops):
    best = float("inf")
    for loop in loops:
        for i in range(len(loop)):
            best = min(best, segment_distance(a, b, loop[i], loop[(i + 1) % len(loop)]))
    return best


def _boundary_distance_to_point(point, loops):
    best = float("inf")
    for loop in loops:
        for i in range(len(loop)):
            best = min(best, dist_to_segment(point, loop[i], loop[(i + 1) % len(loop)]))
    return best


def _clearance(rules, key, fallback=0.02):
    """Read a non-negative clearance from a DesignRules/dict-like object."""
    if rules is None:
        return max(0.0, float(fallback))
    try:
        if hasattr(rules, "get"):
            return max(0.0, float(rules.get(key, fallback)))
    except Exception:
        pass
    try:
        return max(0.0, float(dict(rules).get(key, fallback)))
    except Exception:
        return max(0.0, float(fallback))


def route_outline_conflicts(candidate, outlines, eps=0.02, rules=None):
    """Return a board-outline conflict when routed copper leaves the board.

    The entire track envelope must remain inside the closed Outline region, so
    the centre line is kept at least half the track width (plus ``eps``) away
    from the board edge.  With no closed outline, routing remains unconstrained.
    ``outlines`` may be raw primitives or a prepared :class:`BoardOutline`.
    """
    outline = board_outline(outlines)
    points = list(getattr(candidate, "points", ()) or ()) if candidate is not None else []
    if not outline.active or not points:
        return []
    clearance = max(0.0, float(getattr(candidate, "width", 0.0)) / 2.0 + _clearance(rules, "outline_track", eps))
    hit = [{"kind": "outline", "net": None, "object": None}]
    # A single route point matters when a route is started from free space.
    for point in points:
        if (not outline.contains(point)
                or outline.distance_to_point(point, clearance) + EPS < clearance):
            return hit
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        # In a concave board, endpoints can both be inside while the segment
        # crosses outside.  Distance zero catches every boundary crossing.
        if outline.distance_to_segment(a, b, clearance) + EPS < clearance:
            return hit
    return []


def via_outline_conflicts(candidate, outlines, eps=0.02, rules=None):
    """Return a board-outline conflict when any part of a via leaves the board."""
    outline = board_outline(outlines)
    if not outline.active or candidate is None:
        return []
    point = (float(candidate.x), float(candidate.y))
    clearance = max(0.0, float(candidate.diameter) / 2.0 + _clearance(rules, "outline_via", eps))
    if (not outline.contains(point)
            or outline.distance_to_point(point, clearance) + EPS < clearance):
        return [{"kind": "outline", "net": None, "object": None}]
    return []


def via_layer_outline_conflicts(candidate, outline_sets, eps=0.02, rules=None):
    """Validate one via independently against each copper-layer outline set.

    Outlines from different copper reference planes are separate physical board
    cross-sections.  Combining them into one :class:`BoardOutline` is wrong:
    the even/odd fill rule can make coincident or nested per-layer boundaries
    cancel, falsely reporting a legal via as outside the board.
    """
    for outlines in outline_sets or ():
        conflicts = via_outline_conflicts(candidate, outlines, eps=eps, rules=rules)
        if conflicts:
            return conflicts
    return []


def _different_known_net(active_net, obstacle_net):
    """True only when both copper nets are known and they differ.

    Interactive routing can still be started from free space, where the new
    copper has no net yet.  In that case we preserve the existing behaviour
    until a net is known.  Once a route originates from named copper/pads, any
    physical contact with another named net is a hard collision.
    """
    if active_net in (None, "") or obstacle_net in (None, ""):
        return False
    return str(active_net) != str(obstacle_net)


def route_conflicts(candidate, pads, tracks, vias, layer_order, active_net=None,
                    eps=0.02, rules=None, index=None):
    """Return different-net copper touched by a candidate routed track.

    The test uses the real copper envelopes (track widths, via diameters and
    pad geometry), not centre-line intersection alone.  Same-net copper is
    intentionally allowed so a route can reconnect to or overlap its own net.

    Passing a prebuilt :class:`ObstacleIndex` as ``index`` restricts the exact
    tests to obstacles near the candidate.  This matters because an interactive
    mouse move evaluates this predicate roughly twenty times.
    """
    if candidate is None or len(getattr(candidate, "points", ())) < 2:
        return []
    net = active_net if active_net not in (None, "") else getattr(candidate, "net", None)
    track_eps = _clearance(rules, "track_track", eps)
    via_eps = _clearance(rules, "track_via", eps)
    pad_eps = _clearance(rules, "track_pad", eps)

    if index is not None:
        reach = max(track_eps, via_eps, pad_eps) + float(getattr(candidate, "width", 0.0))
        box = track_bbox(candidate, reach)
        tracks = index.nearby_tracks(box)
        vias = index.nearby_vias(box)
        pads = index.nearby_pads(box)

    out = []
    for track in tracks or ():
        if track is candidate or not _different_known_net(net, getattr(track, "net", None)):
            continue
        if tracks_touch(candidate, track, eps=track_eps):
            out.append({"kind": "track", "net": track.net, "object": track})
    for via in vias or ():
        if not _different_known_net(net, getattr(via, "net", None)):
            continue
        if track_touches_via(candidate, via, layer_order, eps=via_eps):
            out.append({"kind": "via", "net": via.net, "object": via})
    for pad in pads or ():
        if not _different_known_net(net, pad.get("net")):
            continue
        if track_touches_pad(candidate, pad, eps=pad_eps):
            out.append({"kind": "pad", "net": pad.get("net"), "object": pad})
    return out


def via_conflicts(candidate, pads, tracks, vias, layer_order, active_net=None,
                  eps=0.02, rules=None, index=None):
    """Return different-net copper touched by a candidate via barrel/pad."""
    if candidate is None:
        return []
    net = active_net if active_net not in (None, "") else getattr(candidate, "net", None)
    via_track_eps = _clearance(rules, "track_via", eps)
    via_via_eps = _clearance(rules, "via_via", eps)
    via_pad_eps = _clearance(rules, "via_pad", eps)

    if index is not None:
        reach = max(via_track_eps, via_via_eps, via_pad_eps) + float(candidate.diameter)
        box = via_bbox(candidate, reach)
        tracks = index.nearby_tracks(box)
        vias = index.nearby_vias(box)
        pads = index.nearby_pads(box)

    out = []
    for track in tracks or ():
        if not _different_known_net(net, getattr(track, "net", None)):
            continue
        if track_touches_via(track, candidate, layer_order, eps=via_track_eps):
            out.append({"kind": "track", "net": track.net, "object": track})
    for via in vias or ():
        if via is candidate or not _different_known_net(net, getattr(via, "net", None)):
            continue
        if vias_touch(candidate, via, layer_order, eps=via_via_eps):
            out.append({"kind": "via", "net": via.net, "object": via})
    for pad in pads or ():
        if not _different_known_net(net, pad.get("net")):
            continue
        if via_touches_pad(candidate, pad, layer_order, eps=via_pad_eps):
            out.append({"kind": "pad", "net": pad.get("net"), "object": pad})
    return out



def clip_polyline_before_conflict(points, conflict_fn, fixed_count=1, iterations=16):
    """Clip the moving tail of a polyline at the first illegal copper contact.

    ``conflict_fn`` receives a candidate prefix and must return truthy when that
    prefix is illegal.  The points before ``fixed_count`` are already committed
    route vertices and are never moved.  Once a new segment first conflicts, a
    short binary search finds the furthest legal point on that *same* segment.

    This gives an interactive router a hard clearance barrier: the mouse may
    continue moving, but the visible copper head cannot pass through an
    obstacle.  Checking fixed prefixes makes the search monotonic even for a
    two-segment 45-degree posture.
    """
    pts = [(float(p[0]), float(p[1])) for p in (points or ())]
    if len(pts) < 2:
        return pts, False
    fixed_count = max(1, min(int(fixed_count or 1), len(pts)))
    legal = list(pts[:fixed_count])
    # A committed prefix should already be legal.  If external state has made
    # it invalid, do not rewrite it here; simply refuse to extend it further.
    if conflict_fn(legal):
        return legal, True

    for end in pts[fixed_count:]:
        trial = legal + [end]
        if not conflict_fn(trial):
            legal.append(end)
            continue

        start = legal[-1]
        dx = end[0] - start[0]; dy = end[1] - start[1]
        if abs(dx) <= EPS and abs(dy) <= EPS:
            return legal, True
        lo, hi = 0.0, 1.0
        for _ in range(max(4, int(iterations))):
            mid = (lo + hi) * 0.5
            q = (start[0] + dx * mid, start[1] + dy * mid)
            if conflict_fn(legal + [q]):
                hi = mid
            else:
                lo = mid
        if lo > 1e-6:
            q = (start[0] + dx * lo, start[1] + dy * lo)
            if math.hypot(q[0]-start[0], q[1]-start[1]) > EPS:
                legal.append(q)
        return legal, True
    return legal, False


def _detour_candidates(start, target, obstacle_boxes, margin):
    """Waypoints just outside each blocking obstacle's inflated box.

    KiCad's walkaround hugs an obstacle's hull.  Using the inflated axis-aligned
    box keeps this cheap and, because every leg is still routed with
    :func:`route45` and re-validated, a slightly loose hull only ever costs a
    little path length, never correctness.
    """
    seen = set()
    out = []
    for box in obstacle_boxes:
        x0, y0, x1, y1 = box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin
        for corner in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            key = (round(corner[0], 6), round(corner[1], 6))
            if key in seen:
                continue
            seen.add(key)
            out.append(corner)
    # Prefer waypoints that keep the detour short.
    out.sort(key=lambda c: dist2(start, c) + dist2(c, target))
    return out


def walkaround_route(start, target, conflict_fn, obstacle_boxes=(), posture=0,
                     margin=0.05, max_waypoints=2, candidate_limit=12,
                     pair_limit=6):
    """Route ``start`` to ``target`` around obstacles using H/V/45 geometry.

    ``conflict_fn`` receives a candidate point list and returns truthy when that
    geometry is illegal, exactly as for :func:`clip_polyline_before_conflict`.
    ``obstacle_boxes`` are the axis-aligned envelopes of whatever is blocking;
    their inflated corners become the candidate detour waypoints.

    Returns the point list *after* ``start`` (so it can be appended directly to
    a route), or ``None`` when no legal detour was found within the search
    budget.  Falling back to the caller's hard-barrier clip in that case keeps
    the interaction predictable rather than silently doing nothing.

    The budget matters: this runs inside a mouse-move handler, so the two-
    waypoint phase searches only the ``pair_limit`` most promising waypoints
    rather than every ordered pair.
    """
    start = (float(start[0]), float(start[1]))
    target = (float(target[0]), float(target[1]))

    def legs(points):
        """Full point list for a start -> waypoints... -> target path."""
        out = [start]
        cursor = start
        for point in list(points) + [target]:
            step = route45(cursor, point, posture)
            if not step:
                continue
            out.extend(step)
            cursor = out[-1]
        return _compact(out)

    direct = legs(())
    if len(direct) >= 2 and not conflict_fn(direct):
        return direct[1:]

    waypoints = _detour_candidates(start, target, obstacle_boxes, margin)[:candidate_limit]

    best = None

    def consider(path):
        nonlocal best
        if len(path) < 2 or conflict_fn(path):
            return False
        length = sum(math.hypot(path[i][0] - path[i - 1][0],
                                path[i][1] - path[i - 1][1])
                     for i in range(1, len(path)))
        if best is None or length < best[0]:
            best = (length, path)
        return True

    for w in waypoints:
        consider(legs((w,)))
    if best is not None:
        return best[1][1:]

    if int(max_waypoints) >= 2:
        # Two-waypoint detours handle the common case of rounding a corner that
        # a single waypoint cannot clear on both legs.
        narrowed = waypoints[:max(0, int(pair_limit))]
        for a in narrowed:
            for b in narrowed:
                if a is b:
                    continue
                consider(legs((a, b)))
        if best is not None:
            return best[1][1:]
    return None


def conflict_boxes(conflicts, margin=0.0):
    """Axis-aligned envelopes for the objects reported by a conflict list."""
    boxes = []
    for hit in conflicts or ():
        obj = hit.get("object")
        kind = str(hit.get("kind") or "")
        if obj is None:
            continue
        if kind == "track":
            boxes.append(track_bbox(obj, margin))
        elif kind == "via":
            boxes.append(via_bbox(obj, margin))
        elif kind == "pad":
            boxes.append(pad_bbox(obj, margin))
    return boxes


def _candidate_allowed(candidate, active_net):
    if not active_net:
        return True
    net = candidate.get("net")
    return not net or net == active_net


def route_snap(world, layer_id, pads, tracks, vias, layer_order, radius,
               grid=0.1, object_snap=True, grid_snap=True, active_net=None,
               index=None):
    """KiCad-style magnetic snap result.

    Priority is via copper, pad copper, nearby pad/via centres, then the nearest
    projection onto a track centreline.  ``Shift`` is represented by
    ``object_snap=False``; ``Ctrl`` by ``grid_snap=False``.  An optional
    :class:`ObstacleIndex` limits the scan to objects near the cursor.
    """
    wx, wy = _xy(world)
    r = max(0.0, float(radius))
    best = None; best_d2 = r * r

    def consider(cand, d2):
        nonlocal best, best_d2
        if not _candidate_allowed(cand, active_net):
            return
        if d2 <= best_d2:
            best, best_d2 = cand, d2

    if object_snap:
        if index is not None:
            # Track snapping reaches past the plain radius by half a track
            # width, so query with a slightly generous box.
            reach = r + 1.0
            box = (wx - reach, wy - reach, wx + reach, wy + reach)
            pads = index.nearby_pads(box)
            tracks = index.nearby_tracks(box)
            vias = index.nearby_vias(box)

        spanning_vias = [v for v in (vias or ())
                         if via_spans_layer(v, layer_id, layer_order)]
        for via in spanning_vias:
            cand = {"x": via.x, "y": via.y, "kind": "via", "via": via, "net": via.net}
            if math.hypot(via.x-wx, via.y-wy) <= via.diameter/2.0 and _candidate_allowed(cand, active_net):
                return cand

        # One pass builds each pad candidate once: the former code created the
        # same dicts twice, first for containment then for centre distance.
        layer_pads = []
        for pad in (pads or ()):
            if pad.get("layer_id") != layer_id:
                continue
            cand = dict(x=float(pad["x"]), y=float(pad["y"]), kind="pad", pad=pad,
                        net=pad.get("net"))
            layer_pads.append((pad, cand))
        for pad, cand in layer_pads:
            if pad_contains_point((wx, wy), pad) and _candidate_allowed(cand, active_net):
                return cand
        for pad, cand in layer_pads:
            consider(cand, (cand["x"]-wx)**2 + (cand["y"]-wy)**2)

        for via in spanning_vias:
            consider({"x":via.x,"y":via.y,"kind":"via","via":via,"net":via.net},
                     (via.x-wx)**2 + (via.y-wy)**2)
        for track in (tracks or ()):
            if track.layer_id != layer_id or len(track.points) < 2:
                continue
            reach = max(r, track.width/2.0 + 0.05)
            if not bbox_overlap(_points_bbox(track.points, reach),
                                (wx, wy, wx, wy)):
                continue
            reach2 = reach * reach
            for i in range(1, len(track.points)):
                q = closest_point_on_segment((wx, wy), track.points[i-1], track.points[i])
                d2v = (q[0]-wx)**2 + (q[1]-wy)**2
                if d2v <= reach2:
                    consider({"x":q[0],"y":q[1],"kind":"track","track":track,"net":track.net}, d2v)
        if best is not None:
            return best

    if not grid_snap:
        return {"x":wx, "y":wy, "kind":"free", "net":None}
    step = max(EPS, float(grid))
    return {"x":round(wx/step)*step, "y":round(wy/step)*step, "kind":"grid", "net":None}


class _DSU(object):
    def __init__(self, keys=()):
        self.parent = dict((k, k) for k in keys)
    def ensure(self, k):
        if k not in self.parent: self.parent[k] = k
    def find(self, k):
        self.ensure(k); r = k
        while self.parent[r] != r: r = self.parent[r]
        while self.parent[k] != k:
            n = self.parent[k]; self.parent[k] = r; k = n
        return r
    def unite(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b: self.parent[b] = a


_TOUCH_EPS = 0.02


def build_copper_topology(tracks, vias, layer_order):
    """Union routed copper into electrical islands.

    Only object pairs whose envelopes share a broad-phase cell are compared
    exactly, which turns the former all-pairs sweep into roughly linear work on
    a normally distributed board.  The returned structure additionally carries a
    spatial index so callers (ratsnest, net assignment) can locate the copper
    near a pad without rescanning every item.
    """
    items = []
    for t in tracks or ():
        items.append(("t:" + t.uid, "track", t))
    for v in vias or ():
        items.append(("v:" + v.uid, "via", v))
    dsu = _DSU(k for k, _kind, _obj in items)

    entries = []
    for entry in items:
        _key, kind, obj = entry
        box = (track_bbox(obj, _TOUCH_EPS) if kind == "track"
               else via_bbox(obj, _TOUCH_EPS))
        entries.append((box, entry))
    grid = SpatialGrid(entries)

    for first, second in grid.pairs():
        ka, ta, a = first
        kb, tb, b = second
        if dsu.find(ka) == dsu.find(kb):
            continue  # already one island; the exact test cannot change that
        if ta == tb == "track":
            hit = tracks_touch(a, b)
        elif ta == tb == "via":
            hit = vias_touch(a, b, layer_order)
        else:
            track, via = (a, b) if ta == "track" else (b, a)
            hit = track_touches_via(track, via, layer_order)
        if hit:
            dsu.unite(ka, kb)
    return {"items": items, "dsu": dsu, "grid": grid}


def _topology_grid(topo):
    """Broad-phase index for a topology, rebuilt only for legacy dicts."""
    grid = topo.get("grid") if isinstance(topo, dict) else None
    if grid is not None:
        return grid
    entries = []
    for entry in topo["items"]:
        _key, kind, obj = entry
        box = (track_bbox(obj, _TOUCH_EPS) if kind == "track"
               else via_bbox(obj, _TOUCH_EPS))
        entries.append((box, entry))
    grid = SpatialGrid(entries)
    if isinstance(topo, dict):
        topo["grid"] = grid
    return grid


def _items_near_pad(pad, topo):
    """Topology items whose envelope can possibly touch *pad*."""
    return _topology_grid(topo).query(pad_bbox(pad, _TOUCH_EPS))


def _pad_touches_item(pad, kind, obj, layer_order):
    return track_touches_pad(obj, pad) if kind == "track" else via_touches_pad(obj, pad, layer_order)


def _roots_touching_pad(pad, topo, layer_order):
    """Islands electrically touched by *pad*, via the broad-phase index."""
    dsu = topo["dsu"]
    roots = set()
    for key, kind, obj in _items_near_pad(pad, topo):
        if _pad_touches_item(pad, kind, obj, layer_order):
            roots.add(dsu.find(key))
    return roots


def _roots_for_snap(snap, layer_id, topo, layer_order):
    dsu = topo["dsu"]; roots = set()
    track = snap.get("track"); via = snap.get("via"); pad = snap.get("pad")
    if track: roots.add(dsu.find("t:"+track.uid))
    if via: roots.add(dsu.find("v:"+via.uid))
    if pad:
        roots.update(_roots_touching_pad(pad, topo, layer_order))
    return roots


def routing_context(snap, layer_id, nets, pads_by_net, tracks, vias, layer_order):
    topo = build_copper_topology(tracks, vias, layer_order)
    roots = _roots_for_snap(snap, layer_id, topo, layer_order)
    names = set()
    if snap.get("track") and snap["track"].net: names.add(snap["track"].net)
    if snap.get("via") and snap["via"].net: names.add(snap["via"].net)
    if snap.get("pad") and snap["pad"].get("net"): names.add(snap["pad"].get("net"))
    if roots:
        for name, pads in (pads_by_net or {}).items():
            for pad in pads or ():
                if _roots_touching_pad(pad, topo, layer_order) & roots:
                    names.add(name)
                    break
    net_name = next(iter(names)) if len(names) == 1 else None
    connected = set()
    if net_name:
        snap_pad = snap.get("pad")
        snap_key = pad_key((snap_pad or {}).get("ref"), (snap_pad or {}).get("pin"))
        for pad in pads_by_net.get(net_name, []):
            key = pad_key(pad.get("ref"), pad.get("pin"))
            if snap_pad is not None and (pad is snap_pad or key == snap_key):
                connected.add(key)
                continue
            if roots and (_roots_touching_pad(pad, topo, layer_order) & roots):
                connected.add(key)
    return {"net": net_name, "connected": connected, "topology": topo}


def assign_connection_nets(pads_by_net, tracks, vias, layer_order):
    """Propagate schematic net names onto saved routed copper islands.

    KiCad net names originate in the schematic/netlist, while routed tracks and
    vias are geometric objects.  Once copper touches a named pad, every track
    and via in that same electrical island should carry the same net name.
    Explicit names already present on routed copper are also treated as seeds,
    which preserves an unfinished branch between edits.

    Islands that touch more than one distinct named net are ambiguous (a short),
    so this helper deliberately does not overwrite their existing metadata.
    The return value is the number of Track/Via models whose ``net`` changed.
    """
    topo = build_copper_topology(tracks, vias, layer_order)
    dsu = topo["dsu"]
    names_by_root = {}

    def add_name(root, name):
        if name not in (None, ""):
            names_by_root.setdefault(root, set()).add(str(name))

    # Saved copper can already know its net (for example a route that started
    # on a pad); use that as one source when propagating through vias/branches.
    for key, _kind, obj in topo["items"]:
        add_name(dsu.find(key), getattr(obj, "net", None))

    # The netlist is authoritative at pads.  Any copper island touching a pad
    # inherits that pad's exact net name, including KiCad generated names such
    # as ``Net-(R1-Pad1)`` and hierarchical names beginning with '/'.
    for name, pads in (pads_by_net or {}).items():
        if name in (None, ""):
            continue
        for pad in pads or []:
            for root in _roots_touching_pad(pad, topo, layer_order):
                add_name(root, name)

    changed = 0
    for key, _kind, obj in topo["items"]:
        names = names_by_root.get(dsu.find(key), set())
        if len(names) != 1:
            continue
        name = next(iter(names))
        if getattr(obj, "net", None) != name:
            obj.net = name
            changed += 1
    return changed


def _closest_point_on_track(p, track):
    best = None
    for i in range(1, len(track.points)):
        q = closest_point_on_segment(p, track.points[i-1], track.points[i])
        d = dist2(p, q)
        if best is None or d < best[0]: best = (d, q)
    return best[1] if best else None


def _anchors_by_root(topo):
    """Map each copper island root to its physical connection anchors.

    Cached on the topology because every net's grouping needs it, and rebuilding
    it per net made the ratsnest cost O(nets x copper items).
    """
    cached = topo.get("_anchors") if isinstance(topo, dict) else None
    if cached is not None:
        return cached
    anchors = {}
    dsu = topo["dsu"]
    for key, kind, obj in topo["items"]:
        bucket = anchors.setdefault(dsu.find(key), [])
        if kind == "track" and obj.points:
            a, b = obj.points[0], obj.points[-1]
            bucket.append({"x": a[0], "y": a[1], "kind": "track-end"})
            bucket.append({"x": b[0], "y": b[1], "kind": "track-end"})
        elif kind == "via":
            bucket.append({"x": obj.x, "y": obj.y, "kind": "via"})
    if isinstance(topo, dict):
        topo["_anchors"] = anchors
    return anchors


def _tracks_by_root(topo):
    """Map each copper island root to its member tracks (cached per topology)."""
    cached = topo.get("_tracks_by_root") if isinstance(topo, dict) else None
    if cached is not None:
        return cached
    by_root = {}
    dsu = topo["dsu"]
    for key, kind, obj in topo["items"]:
        if kind == "track":
            by_root.setdefault(dsu.find(key), []).append(obj)
    if isinstance(topo, dict):
        topo["_tracks_by_root"] = by_root
    return by_root


def _groups_for_net(pads, topo, layer_order):
    """Group one net's pads into the electrical islands they already form."""
    if not pads:
        return []
    dsu = _DSU()
    pad_roots = {}
    for i, pad in enumerate(pads):
        pk = "p:%d" % i
        dsu.ensure(pk)
        roots = _roots_touching_pad(pad, topo, layer_order)
        for root in roots:
            ck = "c:" + root
            dsu.ensure(ck)
            dsu.unite(pk, ck)
        pad_roots[i] = roots

    # Stacked pads at the same spot on the same layer are one node.  Bucketing
    # by rounded position replaces the former all-pairs distance scan.
    by_position = {}
    for i, pad in enumerate(pads):
        key = (pad.get("layer_id"), round(float(pad["x"]), 6), round(float(pad["y"]), 6))
        if key in by_position:
            dsu.unite("p:%d" % by_position[key], "p:%d" % i)
        else:
            by_position[key] = i

    merged = {}
    for i, pad in enumerate(pads):
        root = dsu.find("p:%d" % i)
        g = merged.setdefault(root, {"pads": [], "copper_roots": set(), "anchors": []})
        g["pads"].append(pad)
        g["anchors"].append({"x": pad["x"], "y": pad["y"], "kind": "pad"})
        g["copper_roots"].update(pad_roots[i])

    anchors_by_root = _anchors_by_root(topo)
    for g in merged.values():
        for root in g["copper_roots"]:
            g["anchors"].extend(anchors_by_root.get(root, ()))
        uniq = {}
        for a in g["anchors"]:
            key = (round(a["x"], 7), round(a["y"], 7))
            if key not in uniq or uniq[key].get("kind") == "pad":
                uniq[key] = a
        g["anchors"] = list(uniq.values())
    return list(merged.values())


def _group_tracks(group, topo, cache):
    """Tracks belonging to a group's copper islands, memoised per ratsnest run."""
    key = id(group)
    hit = cache.get(key)
    if hit is None:
        by_root = _tracks_by_root(topo)
        hit = [t for root in group["copper_roots"] for t in by_root.get(root, ())]
        cache[key] = hit
    return hit


def _closest_group_pair(A, B, topo, cache=None):
    cache = {} if cache is None else cache
    best = None

    def consider(a, b):
        nonlocal best
        d = dist2(a, b)
        if best is None or d < best[0]:
            best = (d, a, b)

    for a in A["anchors"]:
        for b in B["anchors"]:
            consider(a, b)
    for t in _group_tracks(A, topo, cache):
        for b in B["anchors"]:
            q = _closest_point_on_track(b, t)
            if q:
                consider({"x": q[0], "y": q[1], "kind": "track"}, b)
    for t in _group_tracks(B, topo, cache):
        for a in A["anchors"]:
            q = _closest_point_on_track(a, t)
            if q:
                consider(a, {"x": q[0], "y": q[1], "kind": "track"})
    return best



def automatic_via_target_layer(route_head, current_layer_id, net_name, connected_pad_keys,
                               pads_by_net, layer_order):
    """Choose a via destination from the live ratsnest's unresolved pad targets.

    The current route head is treated as the ratsnest source.  Among pads on the
    active net which are not already part of the route's connected island, the
    closest pad on a *different* copper layer wins.  This mirrors the first
    useful airwire leaving the live route while avoiding a no-op same-layer via.

    ``None`` means the ratsnest does not currently imply a different layer; the
    UI may then fall back to its deterministic next-copper-layer behaviour.
    """
    if route_head is None or not net_name:
        return None
    order = list(layer_order or [])
    if current_layer_id not in order:
        return None
    connected = set(connected_pad_keys or ())
    candidates = []
    cur_index = order.index(current_layer_id)
    for pad in (pads_by_net or {}).get(net_name, ()):
        layer_id = pad.get("layer_id")
        if layer_id not in order or layer_id == current_layer_id:
            continue
        if pad_key(pad.get("ref"), pad.get("pin")) in connected:
            continue
        # Primary key follows the visible ratsnest: nearest unresolved target.
        # The remaining keys only make equal-distance cases deterministic.
        candidates.append((dist2(route_head, pad),
                           abs(order.index(layer_id) - cur_index),
                           order.index(layer_id), layer_id))
    return min(candidates)[-1] if candidates else None

def ratsnest_segments(pads_by_net, tracks, vias, layer_order, suppressed_net=None,
                      topology=None):
    """Return static airwires after accounting for saved copper islands.

    ``topology`` lets a caller that already built the copper topology for this
    exact geometry hand it in rather than paying for a second build.
    """
    topo = topology if topology is not None else build_copper_topology(tracks, vias, layer_order)
    out = []
    for name, pads in (pads_by_net or {}).items():
        if suppressed_net and name == suppressed_net:
            continue
        groups = _groups_for_net(pads, topo, layer_order)
        if len(groups) < 2:
            continue
        track_cache = {}
        # Every (i, j) group pair has a fixed cost, so compute each one once
        # instead of recomputing the whole frontier on every Prim iteration.
        pair_cache = {}

        def pair_for(i, j):
            key = (i, j) if i < j else (j, i)
            hit = pair_cache.get(key)
            if hit is None:
                hit = _closest_group_pair(groups[key[0]], groups[key[1]],
                                          topo, track_cache) or False
                pair_cache[key] = hit
            if hit is False:
                return None
            # Anchors are returned in (first, second) order of the cached pair;
            # flip them when the caller asked for the reverse direction.
            if key == (i, j):
                return hit
            return (hit[0], hit[2], hit[1])

        in_tree = [0]
        rest = set(range(1, len(groups)))
        while rest:
            best = None
            for i in in_tree:
                for j in rest:
                    pair = pair_for(i, j)
                    if pair and (best is None or pair[0] < best[0]):
                        best = (pair[0], i, j, pair[1], pair[2])
            if best is None:
                break
            _d, _i, j, a, b = best
            out.append((a, b, name))
            in_tree.append(j)
            rest.remove(j)
    return out
