# -*- coding: utf-8 -*-
"""The imported board solid and its per-layer cross-sections.

Since v0.17 the board shape is no longer drawn layer by layer.  The user
imports one solid (a STEP file or a shape that is already open in FreeCAD) and
every copper layer's routing boundary is *derived* from it: the boundary of a
layer is the cross-section of the solid at that layer's reference-plane height.

Reference frame
---------------
* The solid is oriented with its chosen *up* axis pointing to +Z.
* The XY plane through the solid's **lowest point** is ``z = 0``, the stack-up
  reference.  ``LayerStack.bottom_margin`` is measured from it to the bottom
  copper reference plane; every further plane is one ``separation`` higher.
* World coordinates are right-handed and Y-up (the 3D view's convention).  The
  2D editor and KiCad use Y-down board coordinates, so ``board_y = -world_y``.

Storage
-------
A tessellated, vertex-welded triangle mesh is kept in memory and embedded in
the ``.e3d_pcb`` (compressed), so a saved board always reopens with its exact
boundaries even without FreeCAD or the original STEP file.  The source path is
kept as well so the exact B-Rep can be used for STEP cavity export.

Slicing is pure Python and works on the welded mesh: every triangle crossing
the plane contributes one segment whose ends are keyed by the mesh edge they
lie on, so loops are chained topologically rather than by fuzzy point
matching.  Vertices that lie exactly on the plane are classified consistently
(see :func:`slice_mesh`), which keeps the loops closed even when a layer
coincides with a horizontal face of the solid.

This module imports neither Qt nor FreeCAD at import time.  The FreeCAD
loaders at the bottom import ``Part``/``MeshPart`` lazily.
"""

from __future__ import division

import base64
import hashlib
import math
import os
import struct
import zlib

from . import meshgeom

__all__ = [
    "BoardSolid", "BoardSolidError", "UP_AXES", "slice_mesh", "weld_mesh",
    "solid_from_legacy_outlines", "box_solid",
    "load_step", "solid_from_shape", "freecad_solid_candidates",
    "solid_from_freecad_object", "exact_shape_for_export",
]

UP_AXES = ("+Z", "-Z", "+Y", "-Y", "+X", "-X")
UP_AXIS_LABELS = {
    "+Z": "+Z up", "-Z": "-Z up (flip)", "+Y": "+Y up", "-Y": "-Y up",
    "+X": "+X up", "-X": "-X up",
}

_SECTION_EPS = 1e-9
_CACHE_LIMIT = 64


class BoardSolidError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# orientation helpers
# ---------------------------------------------------------------------------

def _orient(p, up):
    """Rotate source point ``p`` so the source ``up`` axis becomes +Z.

    Every mapping is a proper rotation (determinant +1), so triangle winding
    and therefore outward normals survive re-orientation.
    """
    x, y, z = p
    if up == "-Z":
        return (x, -y, -z)
    if up == "+Y":
        return (x, -z, y)
    if up == "-Y":
        return (x, z, -y)
    if up == "+X":
        return (-z, y, x)
    if up == "-X":
        return (z, y, -x)
    return (x, y, z)


def _rot_z(p, degrees):
    a = math.radians(float(degrees or 0.0))
    if abs(a) < 1e-15:
        return p
    c, s = math.cos(a), math.sin(a)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c, p[2])


def _clean_up_axis(value):
    value = str(value or "+Z").upper().strip()
    if value in ("Z", "X", "Y"):
        value = "+" + value
    return value if value in UP_AXES else "+Z"


# ---------------------------------------------------------------------------
# mesh utilities
# ---------------------------------------------------------------------------

def weld_mesh(vertices, triangles, tol=1e-6):
    """Merge coincident vertices and drop degenerate triangles.

    OpenCascade tessellates face by face, so a vertex on a shared edge appears
    once per face.  Topological slicing needs shared edges to share vertex
    indices, hence the weld.  ``tol`` is a quantisation step in millimetres.
    """
    scale = 1.0 / max(1e-12, float(tol))
    index = {}
    out_vertices = []
    remap = []
    for v in vertices:
        x, y, z = float(v[0]), float(v[1]), float(v[2])
        key = (int(round(x * scale)), int(round(y * scale)), int(round(z * scale)))
        hit = index.get(key)
        if hit is None:
            hit = len(out_vertices)
            index[key] = hit
            out_vertices.append((x, y, z))
        remap.append(hit)
    out_triangles = []
    seen = set()
    for tri in triangles:
        try:
            a, b, c = remap[int(tri[0])], remap[int(tri[1])], remap[int(tri[2])]
        except (IndexError, ValueError, TypeError):
            continue
        if a == b or b == c or a == c:
            continue
        key = (a, b, c) if a < b and a < c else ((b, c, a) if b < c else (c, a, b))
        if key in seen:
            continue
        seen.add(key)
        out_triangles.append((a, b, c))
    return out_vertices, out_triangles


def _simplify_loop(points, tol=1e-6):
    """Drop duplicate and collinear vertices from one closed loop."""
    pts = []
    for p in points:
        if not pts or abs(p[0] - pts[-1][0]) > tol or abs(p[1] - pts[-1][1]) > tol:
            pts.append(p)
    while len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) <= tol and abs(pts[0][1] - pts[-1][1]) <= tol:
        pts.pop()
    changed = True
    while changed and len(pts) > 3:
        changed = False
        out = []
        n = len(pts)
        for i in range(n):
            a = out[-1] if out else pts[i - 1]
            b = pts[i]
            c = pts[(i + 1) % n]
            abx, aby = b[0] - a[0], b[1] - a[1]
            acx, acy = c[0] - a[0], c[1] - a[1]
            length = math.hypot(acx, acy)
            # perpendicular distance of b from the a-c chord, and b between a/c
            if length > tol:
                dist = abs(abx * acy - aby * acx) / length
                along = (abx * acx + aby * acy) / (length * length)
                if dist <= tol and -1e-9 <= along <= 1.0 + 1e-9:
                    changed = True
                    continue
            out.append(b)
        pts = out
    return pts if len(pts) >= 3 else []


def slice_mesh(vertices, triangles, z, below=True, zbounds=None):
    """Return closed XY loops where the plane ``Z = z`` cuts a triangle mesh.

    ``below=True`` classifies vertices lying exactly on the plane as *above*
    it, which is the same as slicing an infinitesimal distance below ``z``;
    ``below=False`` slices infinitesimally above.  Either choice guarantees
    every crossing lies strictly inside a mesh edge, so segment ends can be
    identified by edge and chained without tolerances.

    ``zbounds`` may be a per-triangle ``(zmin, zmax)`` list to skip triangles
    that cannot reach the plane.
    """
    z = float(z)
    points = {}
    links = {}

    def side(value):
        return value >= z if below else value > z

    for t_index, tri in enumerate(triangles):
        if zbounds is not None:
            lo, hi = zbounds[t_index]
            if hi < z - 1e-12 or lo > z + 1e-12:
                continue
        a, b, c = tri
        va, vb, vc = vertices[a], vertices[b], vertices[c]
        sa, sb, sc = side(va[2]), side(vb[2]), side(vc[2])
        if sa == sb == sc:
            continue
        ends = []
        for i, j, vi, vj, si, sj in ((a, b, va, vb, sa, sb),
                                     (b, c, vb, vc, sb, sc),
                                     (c, a, vc, va, sc, sa)):
            if si == sj:
                continue
            key = (i, j) if i < j else (j, i)
            if key not in points:
                di = vi[2] - z
                dj = vj[2] - z
                denom = di - dj
                t = 0.5 if abs(denom) < 1e-300 else di / denom
                points[key] = (vi[0] + (vj[0] - vi[0]) * t,
                               vi[1] + (vj[1] - vi[1]) * t)
            ends.append(key)
        if len(ends) == 2:
            links.setdefault(ends[0], []).append(ends[1])
            links.setdefault(ends[1], []).append(ends[0])

    loops = []
    open_chains = []
    visited = set()
    for start in list(links.keys()):
        if start in visited:
            continue
        chain = [start]
        visited.add(start)
        prev, cur = None, start
        closed = False
        while True:
            nxt = None
            for cand in links.get(cur, ()):
                if cand == start and prev is not None and len(chain) > 2:
                    closed = True
                    break
                if cand not in visited:
                    nxt = cand
                    break
            if closed or nxt is None:
                break
            visited.add(nxt)
            chain.append(nxt)
            prev, cur = cur, nxt
        pts = [points[k] for k in chain]
        if closed:
            loops.append(pts)
        elif len(pts) >= 2:
            open_chains.append(pts)

    # A non-manifold or cracked mesh can leave open chains.  Join them by
    # nearest endpoints and keep anything that closes within tolerance.
    if open_chains:
        loops.extend(_join_open_chains(open_chains))

    result = []
    for loop in loops:
        simple = _simplify_loop(loop)
        if len(simple) >= 3 and abs(meshgeom.polygon_area(simple)) > 1e-10:
            result.append(simple)
    return result


def _join_open_chains(chains, tol=1e-3):
    chains = [list(c) for c in chains]
    loops = []
    while chains:
        chain = chains.pop(0)
        grew = True
        while grew:
            grew = False
            end = chain[-1]
            best_i, best_d, best_rev = None, tol, False
            for i, other in enumerate(chains):
                d0 = math.hypot(other[0][0] - end[0], other[0][1] - end[1])
                d1 = math.hypot(other[-1][0] - end[0], other[-1][1] - end[1])
                if d0 < best_d:
                    best_i, best_d, best_rev = i, d0, False
                if d1 < best_d:
                    best_i, best_d, best_rev = i, d1, True
            if best_i is not None:
                other = chains.pop(best_i)
                if best_rev:
                    other.reverse()
                chain.extend(other[1:])
                grew = True
        if len(chain) >= 3 and math.hypot(chain[0][0] - chain[-1][0],
                                          chain[0][1] - chain[-1][1]) <= max(tol, 1e-6):
            loops.append(chain[:-1] if len(chain) > 3 else chain)
    return loops


def _pack_floats(values):
    return base64.b64encode(zlib.compress(struct.pack("<%df" % len(values), *values), 9)).decode("ascii")


def _pack_ints(values):
    return base64.b64encode(zlib.compress(struct.pack("<%dI" % len(values), *values), 9)).decode("ascii")


def _unpack(text, code):
    raw = zlib.decompress(base64.b64decode(str(text or "").encode("ascii")))
    size = struct.calcsize("<" + code)
    count = len(raw) // size
    return struct.unpack("<%d%s" % (count, code), raw[:count * size])


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------

class BoardSolid(object):
    """Board body as a welded triangle mesh plus placement and provenance.

    ``vertices`` are in the *source* coordinate system (as read from the STEP
    file or FreeCAD object).  ``placement`` re-orients and positions them:

    ``up_axis``   which source axis points up (``+Z``, ``-Y`` …)
    ``rotation``  degrees about the vertical axis, applied after orientation
    ``offset_x``/``offset_y``  board-plane translation in world millimetres

    Z is never user-positioned: the lowest point always lands on ``z = 0``.
    """

    def __init__(self, vertices, triangles, source=None, placement=None,
                 exact_bounds=None, weld=True):
        if weld:
            vertices, triangles = weld_mesh(vertices, triangles)
        self.vertices = [tuple(float(c) for c in v) for v in vertices]
        self.triangles = [tuple(int(i) for i in t) for t in triangles]
        if not self.vertices or not self.triangles:
            raise BoardSolidError("The imported shape has no faces to build a board from.")
        self.source = dict(source or {})
        self.source.setdefault("kind", "mesh")
        self.placement = {"up_axis": "+Z", "rotation": 0.0, "offset_x": 0.0, "offset_y": 0.0}
        self.exact_bounds = (tuple(float(v) for v in exact_bounds)
                             if exact_bounds and len(exact_bounds) == 6 else None)
        # Transient exact OpenCascade shape (STEP/FreeCAD imports only).
        self.exact_shape = None
        self._world = None
        self._zbounds = None
        self._sections = {}
        self._mesh_digest = None
        self.set_placement(**dict(placement or {}))

    # -- identity --------------------------------------------------------
    @property
    def label(self):
        src = self.source
        return str(src.get("label") or os.path.basename(str(src.get("path") or "")) or "Board solid")

    @property
    def kind(self):
        return str(self.source.get("kind") or "mesh")

    def mesh_digest(self):
        if self._mesh_digest is None:
            h = hashlib.sha1()
            h.update(struct.pack("<II", len(self.vertices), len(self.triangles)))
            for v in self.vertices[:: max(1, len(self.vertices) // 512)]:
                h.update(struct.pack("<3d", *v))
            for t in self.triangles[:: max(1, len(self.triangles) // 512)]:
                h.update(struct.pack("<3I", *t))
            self._mesh_digest = h.hexdigest()[:16]
        return self._mesh_digest

    def signature(self):
        """Stable value that changes whenever the board geometry changes."""
        p = self.placement
        return (self.mesh_digest(), p["up_axis"], round(p["rotation"], 9),
                round(p["offset_x"], 9), round(p["offset_y"], 9))

    # -- placement --------------------------------------------------------
    def set_placement(self, up_axis=None, rotation=None, offset_x=None, offset_y=None, **_ignored):
        """Update placement; return ``True`` when anything actually changed."""
        new = dict(self.placement)
        if up_axis is not None:
            new["up_axis"] = _clean_up_axis(up_axis)
        for key, value in (("rotation", rotation), ("offset_x", offset_x), ("offset_y", offset_y)):
            if value is None:
                continue
            try:
                new[key] = float(value)
            except (TypeError, ValueError):
                continue
        new["rotation"] = ((new["rotation"] + 180.0) % 360.0) - 180.0
        if abs(new["rotation"] + 180.0) < 1e-12:
            new["rotation"] = 180.0
        if all((new[k] == self.placement.get(k)) if k == "up_axis"
               else abs(float(new[k]) - float(self.placement.get(k, 0.0))) < 1e-12
               for k in new):
            return False
        self.placement = new
        self._world = None
        self._zbounds = None
        self._sections = {}
        return True

    def _oriented_zmin(self):
        """Height of the lowest point after orientation.

        Mesh vertices lie on the true surface, so the mesh minimum can only be
        a hair above the exact minimum (e.g. under a curved bottom).  The exact
        B-Rep bound is preferred when it is consistent with the mesh; a loose
        bound (OpenCascade may pad boxes around B-spline control points) is
        ignored in favour of the mesh.
        """
        up = self.placement["up_axis"]
        mesh_min = min(_orient(v, up)[2] for v in self.vertices)
        if self.exact_bounds is not None:
            x0, y0, z0, x1, y1, z1 = self.exact_bounds
            corners = [_orient((x, y, z), up) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
            exact_min = min(c[2] for c in corners)
            if -1e-6 <= mesh_min - exact_min <= 0.05:
                return exact_min
        return mesh_min

    def world_vertices(self):
        """Vertices in world space: oriented, rotated, lowest point on z = 0."""
        if self._world is None:
            up = self.placement["up_axis"]
            rot = self.placement["rotation"]
            ox, oy = self.placement["offset_x"], self.placement["offset_y"]
            zmin = self._oriented_zmin()
            world = []
            for v in self.vertices:
                p = _rot_z(_orient(v, up), rot)
                world.append((p[0] + ox, p[1] + oy, p[2] - zmin))
            self._world = world
            self._zbounds = [(min(world[a][2], world[b][2], world[c][2]),
                              max(world[a][2], world[b][2], world[c][2]))
                             for a, b, c in self.triangles]
        return self._world

    def world_bounds(self):
        """``(minx, miny, minz, maxx, maxy, maxz)`` of the placed solid."""
        world = self.world_vertices()
        xs = [v[0] for v in world]; ys = [v[1] for v in world]; zs = [v[2] for v in world]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    @property
    def height(self):
        b = self.world_bounds()
        return max(0.0, b[5])

    def size(self):
        b = self.world_bounds()
        return (b[3] - b[0], b[4] - b[1], max(0.0, b[5]))

    def board_bounds(self):
        """XY bounds in 2D editor (Y-down) coordinates."""
        b = self.world_bounds()
        return (b[0], -b[4], b[3], -b[1])

    def center_on_origin(self):
        """Shift the board plane so the solid's XY bounding box is centred."""
        b = self.world_bounds()
        cx, cy = (b[0] + b[3]) / 2.0, (b[1] + b[4]) / 2.0
        return self.set_placement(offset_x=self.placement["offset_x"] - cx,
                                  offset_y=self.placement["offset_y"] - cy)

    # -- sections ----------------------------------------------------------
    def section(self, z):
        """Closed world-space (Y-up) loops of the solid at height ``z``.

        A plane that coincides with the very bottom face (``z == 0``) would
        otherwise produce nothing, so an empty below-slice is retried just
        above the plane.  A plane above the solid returns ``[]``.
        """
        key = round(float(z), 9)
        hit = self._sections.get(key)
        if hit is not None:
            return [list(loop) for loop in hit]
        world = self.world_vertices()
        loops = slice_mesh(world, self.triangles, key, below=True, zbounds=self._zbounds)
        if not loops:
            loops = slice_mesh(world, self.triangles, key, below=False, zbounds=self._zbounds)
        loops = meshgeom.sorted_loops(loops)
        if len(self._sections) >= _CACHE_LIMIT:
            self._sections.clear()
        self._sections[key] = [tuple(loop) for loop in loops]
        return [list(loop) for loop in loops]

    def board_section(self, z):
        """Section loops in 2D editor (Y-down) coordinates."""
        return [[(x, -y) for x, y in loop] for loop in self.section(z)]

    def section_area(self, z):
        """Net even/odd area of the section (holes subtract)."""
        loops = self.section(z)
        total = 0.0
        for loop in loops:
            area = abs(meshgeom.polygon_area(loop))
            sample = loop[0]
            depth = sum(1 for other in loops if other is not loop
                        and meshgeom.point_in_loops(sample, [other]))
            total += -area if depth % 2 else area
        return total

    def feature_edges(self, angle_deg=28.0):
        """Mesh edges worth drawing: sharp creases and open boundaries.

        Smooth tessellation edges (fillets, cylinders) are skipped so the 3D
        view shows the solid's real silhouette lines.  Rigid placement does not
        change dihedral angles, so the result is cached per mesh.
        """
        cache = getattr(self, "_feature_cache", None)
        if cache is not None and cache[0] == angle_deg:
            return cache[1]
        verts = self.vertices
        normals = []
        for a, b, c in self.triangles:
            pa, pb, pc = verts[a], verts[b], verts[c]
            ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            normals.append((nx / length, ny / length, nz / length))
        edges = {}
        for t_index, (a, b, c) in enumerate(self.triangles):
            for i, j in ((a, b), (b, c), (c, a)):
                edges.setdefault((i, j) if i < j else (j, i), []).append(t_index)
        limit = math.cos(math.radians(float(angle_deg)))
        result = []
        for key, owners in edges.items():
            if len(owners) != 2:
                result.append(key)
                continue
            n1, n2 = normals[owners[0]], normals[owners[1]]
            if n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2] < limit:
                result.append(key)
        self._feature_cache = (angle_deg, result)
        return result

    # -- persistence -------------------------------------------------------
    def snapshot(self):
        flat_v = [c for v in self.vertices for c in v]
        flat_t = [i for t in self.triangles for i in t]
        data = {
            "source": dict(self.source),
            "placement": dict(self.placement),
            "vertex_count": len(self.vertices),
            "triangle_count": len(self.triangles),
            "vertices": _pack_floats(flat_v),
            "triangles": _pack_ints(flat_t),
        }
        if self.exact_bounds is not None:
            data["exact_bounds"] = list(self.exact_bounds)
        return data

    @classmethod
    def restore(cls, data):
        data = dict(data or {})
        try:
            flat_v = _unpack(data.get("vertices", ""), "f")
            flat_t = _unpack(data.get("triangles", ""), "I")
        except Exception as exc:
            raise BoardSolidError("Board solid mesh data is corrupt: %s" % exc)
        vertices = [tuple(flat_v[i:i + 3]) for i in range(0, len(flat_v) - 2, 3)]
        triangles = [tuple(flat_t[i:i + 3]) for i in range(0, len(flat_t) - 2, 3)]
        # float32 storage already round-tripped welded vertices; keep indices.
        return cls(vertices, triangles, source=data.get("source"),
                   placement=data.get("placement"),
                   exact_bounds=data.get("exact_bounds"), weld=False)

    def copy(self):
        clone = BoardSolid(self.vertices, self.triangles, source=self.source,
                           placement=self.placement, exact_bounds=self.exact_bounds,
                           weld=False)
        clone.exact_shape = self.exact_shape
        return clone

    def __repr__(self):
        sx, sy, sz = self.size()
        return "BoardSolid(%r, %d tris, %.2fx%.2fx%.2f mm)" % (
            self.label, len(self.triangles), sx, sy, sz)


# ---------------------------------------------------------------------------
# simple generated solids (tests, previews and legacy migration)
# ---------------------------------------------------------------------------

def _prism_mesh_from_sections(sections):
    """Build a closed mesh from ``[(z, [loop, ...]), ...]`` world sections.

    Loop *i* of each section is connected to loop *i* of the next section.
    Rings with matching vertex counts keep their exact vertices; otherwise
    they are resampled to a common count.  Bottom/top rings are capped.
    """
    if len(sections) < 2:
        raise BoardSolidError("A solid needs at least two cross-sections.")
    count = max(len(loops) for _z, loops in sections)
    vertices, triangles = [], []
    for li in range(count):
        rings = []
        for _z, loops in sections:
            loops = meshgeom.sorted_loops(loops)
            if not loops:
                continue
            rings.append(list(loops[li] if li < len(loops) else loops[-1]))
        if len(rings) != len(sections):
            continue
        n = len(rings[0])
        if any(len(r) != n for r in rings):
            n = min(320, max(48, 2 * max(len(r) for r in rings)))
            rings = [meshgeom.resample_loop(r, n) for r in rings]
        # Orient every ring counter-clockwise and phase-align to its neighbour.
        aligned = []
        for ring in rings:
            if meshgeom.polygon_area(ring) < 0.0:
                ring = list(reversed(ring))
            if aligned:
                prev = aligned[-1]
                best_shift, best_cost = 0, None
                for shift in range(n):
                    cost = 0.0
                    for i in range(0, n, max(1, n // 64)):
                        q = ring[(i + shift) % n]
                        cost += (prev[i][0] - q[0]) ** 2 + (prev[i][1] - q[1]) ** 2
                    if best_cost is None or cost < best_cost:
                        best_cost, best_shift = cost, shift
                ring = [ring[(i + best_shift) % n] for i in range(n)]
            aligned.append(ring)
        base_indices = []
        for (z, _loops), ring in zip(sections, aligned):
            base = len(vertices)
            vertices.extend((float(x), float(y), float(z)) for x, y in ring)
            base_indices.append(base)
        for k in range(len(aligned) - 1):
            b0, b1 = base_indices[k], base_indices[k + 1]
            for i in range(n):
                j = (i + 1) % n
                triangles.append((b0 + i, b0 + j, b1 + j))
                triangles.append((b0 + i, b1 + j, b1 + i))
        for tri in meshgeom.triangulate_polygon(aligned[0]):
            b0 = base_indices[0]
            triangles.append((b0 + tri[2], b0 + tri[1], b0 + tri[0]))
        for tri in meshgeom.triangulate_polygon(aligned[-1]):
            b1 = base_indices[-1]
            triangles.append((b1 + tri[0], b1 + tri[1], b1 + tri[2]))
    if not triangles:
        raise BoardSolidError("No closed loops to build a solid from.")
    return vertices, triangles


def box_solid(width, depth, height, center=(0.0, 0.0), label="Box"):
    """A rectangular board block, mostly useful for tests and previews."""
    cx, cy = float(center[0]), float(center[1])
    w, d = abs(float(width)) / 2.0, abs(float(depth)) / 2.0
    loop = [(cx - w, cy - d), (cx + w, cy - d), (cx + w, cy + d), (cx - w, cy + d)]
    v, t = _prism_mesh_from_sections([(0.0, [loop]), (abs(float(height)), [loop])])
    return BoardSolid(v, t, source={"kind": "generated", "label": label})


def solid_from_legacy_outlines(stack, outlines, top_margin=1.0):
    """Convert pre-v0.17 per-layer drawn Outlines into an equivalent solid.

    The old 3D view lofted the board between each copper layer's closed
    Outline, interpolated missing layers and extended the bottom/top margins
    with the nearest boundary.  The same skin is rebuilt here as a mesh so old
    projects keep their exact routing boundaries.  Returns ``None`` when there
    is no closed outline to convert.
    """
    layers = list(getattr(stack, "layers", []) or [])
    if not layers:
        return None
    fallback_layer = layers[-1].id
    raw = meshgeom.outline_loops_by_layer(outlines or [], fallback_layer_id=fallback_layer)
    raw = dict((lid, [[(x, -y) for x, y in loop] for loop in loops])
               for lid, loops in raw.items() if loops)
    defined = [i for i, layer in enumerate(layers) if raw.get(layer.id)]
    if not defined:
        return None
    z = float(getattr(stack, "bottom_margin", 1.0))
    zmap = {}
    for i, layer in enumerate(layers):
        zmap[layer.id] = z
        if i + 1 < len(layers):
            z += float(stack.separation(layer.id, layers[i + 1].id) or 0.0)
    total = z + max(0.0, float(top_margin or 0.0))

    layer_loops = {}
    for i, layer in enumerate(layers):
        if raw.get(layer.id):
            layer_loops[layer.id] = meshgeom.sorted_loops(raw[layer.id])
            continue
        lower_i = max((j for j in defined if j < i), default=None)
        upper_i = min((j for j in defined if j > i), default=None)
        if lower_i is not None and upper_i is not None:
            lo, hi = layers[lower_i], layers[upper_i]
            zl, zu = zmap[lo.id], zmap[hi.id]
            t = 0.0 if abs(zu - zl) < 1e-12 else (zmap[layer.id] - zl) / (zu - zl)
            layer_loops[layer.id] = meshgeom.interpolate_loop_sets(raw[lo.id], raw[hi.id], t)
        else:
            near = layers[lower_i if lower_i is not None else upper_i]
            layer_loops[layer.id] = meshgeom.sorted_loops(raw[near.id])

    sections = [(0.0, layer_loops[layers[0].id])]
    for layer in layers:
        zl = zmap[layer.id]
        if abs(zl - sections[-1][0]) <= 1e-12:
            sections[-1] = (zl, layer_loops[layer.id])
        else:
            sections.append((zl, layer_loops[layer.id]))
    total = max(total, sections[-1][0] + 0.001)
    if total > sections[-1][0] + 1e-12:
        sections.append((total, layer_loops[layers[-1].id]))
    if len(sections) < 2:
        sections.append((sections[0][0] + 0.001, sections[0][1]))
    v, t = _prism_mesh_from_sections(sections)
    return BoardSolid(v, t, source={"kind": "legacy",
                                    "label": "Converted from drawn outlines"})


# ---------------------------------------------------------------------------
# FreeCAD / OpenCascade loaders (lazy imports)
# ---------------------------------------------------------------------------

def _fc_part():
    try:
        import FreeCAD as App  # noqa: F401
        import Part
    except Exception as exc:
        raise BoardSolidError("FreeCAD Part/OpenCascade is unavailable: %s" % exc)
    return App, Part


def _deflection_for(shape):
    try:
        diag = float(shape.BoundBox.DiagonalLength)
    except Exception:
        diag = 100.0
    return max(0.005, min(0.05, diag / 4000.0))


def _single_body(shape, Part):
    """Fuse multiple solids so overlapping bodies do not cancel in even/odd."""
    solids = list(getattr(shape, "Solids", []) or [])
    if len(solids) > 1:
        try:
            fused = solids[0].multiFuse(solids[1:])
            try:
                fused = fused.removeSplitter()
            except Exception:
                pass
            return fused
        except Exception:
            return shape
    if not solids and getattr(shape, "Faces", None):
        try:
            solid = Part.Solid(Part.Shell(list(shape.Faces)))
            if not solid.isNull() and solid.isValid():
                return solid
        except Exception:
            pass
    return shape


def _mesh_from_shape(shape, deflection):
    """Tessellate an OCC shape into raw (vertices, triangles)."""
    try:
        import MeshPart
        mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=float(deflection),
                                      AngularDeflection=math.radians(12.0), Relative=False)
        points, facets = mesh.Topology
        if points and facets:
            return ([(p.x, p.y, p.z) for p in points], [tuple(f) for f in facets])
    except Exception:
        pass
    points, facets = shape.tessellate(float(deflection))
    return ([(p.x, p.y, p.z) for p in points], [tuple(f) for f in facets])


def solid_from_shape(shape, source=None, placement=None, deflection=None):
    """Create a :class:`BoardSolid` from a ``Part.Shape``."""
    _App, Part = _fc_part()
    if shape is None or shape.isNull():
        raise BoardSolidError("The selected object has no shape.")
    if not list(getattr(shape, "Faces", []) or []):
        raise BoardSolidError("The selected shape has no faces; a solid body is required.")
    body = _single_body(shape, Part)
    if not list(getattr(body, "Solids", []) or []):
        # A closed shell still slices correctly; an open sheet does not.
        try:
            if not all(sh.isClosed() for sh in body.Shells):
                raise BoardSolidError("The shape is not closed; import a solid body.")
        except BoardSolidError:
            raise
        except Exception:
            pass
    d = float(deflection) if deflection else _deflection_for(body)
    vertices, triangles = _mesh_from_shape(body, d)
    try:
        bb = body.optimalBoundingBox(True, False)   # FreeCAD >= 0.20: tight box
    except Exception:
        bb = body.BoundBox
    solid = BoardSolid(vertices, triangles, source=source, placement=placement,
                       exact_bounds=(bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax))
    solid.exact_shape = body
    return solid


def load_step(path, placement=None):
    """Read a STEP file through FreeCAD's Part module (no document is created)."""
    _App, Part = _fc_part()
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(path):
        raise BoardSolidError("STEP file not found: %s" % path)
    try:
        shape = Part.Shape()
        shape.read(path)
    except Exception:
        try:
            shape = Part.read(path)
        except Exception as exc:
            raise BoardSolidError("Could not read %s: %s" % (os.path.basename(path), exc))
    return solid_from_shape(shape, source={
        "kind": "step", "path": path, "label": os.path.basename(path)}, placement=placement)


def _object_shape(obj):
    """Shape of a FreeCAD object in global coordinates, links included."""
    try:
        import Part
        shape = Part.getShape(obj, "", needSubElement=False, refine=False, transform=True)
        if shape is not None and not shape.isNull():
            shape = shape.copy()
            try:
                # Part.getShape applies the object's own placement; objects
                # inside App::Part containers also need the parents'.
                shape.Placement = obj.getGlobalPlacement()
            except Exception:
                pass
            return shape
    except Exception:
        pass
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return None
    shape = shape.copy()
    try:
        shape.Placement = obj.getGlobalPlacement()
    except Exception:
        pass
    return shape


def freecad_solid_candidates():
    """List open FreeCAD objects that carry a solid (or closed-shell) shape.

    Returns ``[{"document", "object", "label", "doc_label", "selected"}]``,
    with the current 3D-view selection first.
    """
    try:
        import FreeCAD as App
    except Exception:
        return []
    selected = set()
    try:
        import FreeCADGui as Gui
        for sel in Gui.Selection.getSelection():
            selected.add((sel.Document.Name, sel.Name))
    except Exception:
        pass
    result = []
    for doc in list(App.listDocuments().values()):
        for obj in list(getattr(doc, "Objects", []) or []):
            try:
                if obj.TypeId in ("App::Part", "App::DocumentObjectGroup", "App::Origin") or \
                        obj.TypeId.startswith(("Sketcher::", "App::Origin", "App::Line", "App::Plane")):
                    continue
            except Exception:
                pass
            shape = getattr(obj, "Shape", None)
            try:
                if shape is None or shape.isNull():
                    continue
                if not (list(shape.Solids) or (list(shape.Shells) and
                                               all(s.isClosed() for s in shape.Shells))):
                    continue
            except Exception:
                continue
            # Skip intermediate features hidden inside a PartDesign Body etc.
            try:
                if obj.TypeId.startswith("PartDesign::") and obj.TypeId != "PartDesign::Body":
                    continue
            except Exception:
                pass
            result.append({
                "document": doc.Name, "object": obj.Name,
                "label": str(getattr(obj, "Label", obj.Name)),
                "doc_label": str(getattr(doc, "Label", doc.Name)),
                "selected": (doc.Name, obj.Name) in selected,
            })
    result.sort(key=lambda r: (not r["selected"], r["doc_label"].lower(), r["label"].lower()))
    return result


def solid_from_freecad_object(document, obj_name, placement=None):
    """Tessellate a FreeCAD object's global-placement shape into a solid."""
    try:
        import FreeCAD as App
    except Exception as exc:
        raise BoardSolidError("FreeCAD is unavailable: %s" % exc)
    doc = App.getDocument(document) if document in App.listDocuments() else None
    obj = doc.getObject(obj_name) if doc is not None else None
    if obj is None:
        raise BoardSolidError("FreeCAD object %s/%s no longer exists." % (document, obj_name))
    shape = _object_shape(obj)
    if shape is None:
        raise BoardSolidError("FreeCAD object %s has no shape." % getattr(obj, "Label", obj_name))
    return solid_from_shape(shape, source={
        "kind": "freecad", "document": doc.Name, "object": obj.Name,
        "label": "%s (%s)" % (getattr(obj, "Label", obj.Name), getattr(doc, "Label", doc.Name)),
    }, placement=placement)


def exact_shape_for_export(solid):
    """Return an exact ``Part.Shape`` placed like :meth:`BoardSolid.world_vertices`.

    Uses the in-memory B-Rep, then the STEP source, then a FreeCAD object, and
    finally falls back to a solid sewn from the stored mesh.
    """
    App, Part = _fc_part()
    shape = None
    if solid.exact_shape is not None:
        try:
            shape = solid.exact_shape.copy()
        except Exception:
            shape = None
    if shape is None and os.path.isfile(str(solid.source.get("path") or "")):
        try:
            shape = _single_body(Part.read(str(solid.source["path"])), Part)
        except Exception:
            shape = None
    if shape is None and solid.kind == "freecad":
        try:
            shape = solid_from_freecad_object(solid.source.get("document"),
                                              solid.source.get("object")).exact_shape
        except Exception:
            shape = None
    if shape is None:
        import Mesh
        mesh = Mesh.Mesh([[solid.vertices[i] for i in tri] for tri in solid.triangles])
        sewn = Part.Shape()
        sewn.makeShapeFromMesh(mesh.Topology, 0.01)
        try:
            shape = Part.Solid(Part.Shell(sewn.Faces))
        except Exception:
            shape = sewn
        try:
            shape = shape.removeSplitter()
        except Exception:
            pass

    up = solid.placement["up_axis"]
    rotations = {
        "-Z": ((1, 0, 0), 180.0), "+Y": ((1, 0, 0), 90.0), "-Y": ((1, 0, 0), -90.0),
        "+X": ((0, 1, 0), -90.0), "-X": ((0, 1, 0), 90.0),
    }
    if up in rotations:
        axis, angle = rotations[up]
        shape.rotate(App.Vector(0, 0, 0), App.Vector(*axis), angle)
    if abs(solid.placement["rotation"]) > 1e-12:
        shape.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), solid.placement["rotation"])
    zmin = solid._oriented_zmin()
    shape.translate(App.Vector(solid.placement["offset_x"], solid.placement["offset_y"], -zmin))
    return shape
