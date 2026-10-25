# -*- coding: utf-8 -*-
"""FreeCAD-facing board-solid loaders, exercised against a minimal Part stub.

The real loaders run inside FreeCAD.  These tests inject tiny ``FreeCAD`` /
``Part`` stand-ins so the call sequence (tessellation, bounds, placement for
export) is verified without OpenCascade.
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d import boardsolid as bs                       # noqa: E402


class _Vec(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class _Box(object):
    def __init__(self, lo, hi):
        self.XMin, self.YMin, self.ZMin = lo
        self.XMax, self.YMax, self.ZMax = hi
        self.DiagonalLength = sum((h - l) ** 2 for l, h in zip(lo, hi)) ** 0.5


class _Shape(object):
    """Unit-ish box solid offset in Z, recording transforms."""

    def __init__(self):
        box = bs.box_solid(10.0, 4.0, 2.0)
        self._verts = [(x, y, z + 5.0) for x, y, z in box.vertices]
        self._tris = list(box.triangles)
        self.Faces = [object()] * 6
        self.Solids = [self]
        self.Shells = []
        self.BoundBox = _Box((-5.0, -2.0, 5.0), (5.0, 2.0, 7.0))
        self.calls = []

    def isNull(self):
        return False

    def tessellate(self, tol):
        self.calls.append(("tessellate", tol))
        return [_Vec(*v) for v in self._verts], list(self._tris)

    def copy(self):
        return self

    def rotate(self, base, axis, angle):
        self.calls.append(("rotate", (axis.x, axis.y, axis.z), angle))

    def translate(self, vec):
        self.calls.append(("translate", (vec.x, vec.y, vec.z)))


class LoaderStubCase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: sys.modules.get(k) for k in ("FreeCAD", "Part", "MeshPart")}
        app = types.ModuleType("FreeCAD")
        app.Vector = _Vec
        part = types.ModuleType("Part")
        part.Shape = _Shape
        sys.modules["FreeCAD"] = app
        sys.modules["Part"] = part
        sys.modules.pop("MeshPart", None)        # force the tessellate() path

    def tearDown(self):
        for key, mod in self._saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod

    def test_solid_from_shape_tessellates_and_keeps_exact_bounds(self):
        shape = _Shape()
        solid = bs.solid_from_shape(shape, source={"kind": "freecad", "label": "Body"})
        self.assertEqual(solid.kind, "freecad")
        self.assertIs(solid.exact_shape, shape)
        self.assertEqual(solid.exact_bounds, (-5.0, -2.0, 5.0, 5.0, 2.0, 7.0))
        self.assertTrue(any(c[0] == "tessellate" for c in shape.calls))
        # Lowest point (z = 5 in the source) becomes the reference plane.
        self.assertAlmostEqual(solid.world_bounds()[2], 0.0)
        self.assertAlmostEqual(solid.height, 2.0)
        self.assertEqual(len(solid.section(1.0)), 1)

    def test_export_shape_is_placed_like_the_viewer_mesh(self):
        shape = _Shape()
        solid = bs.solid_from_shape(shape, source={"kind": "freecad"})
        solid.set_placement(up_axis="+Y", rotation=30.0, offset_x=4.0, offset_y=-1.0)
        shape.calls = []
        out = bs.exact_shape_for_export(solid)
        self.assertIs(out, shape)
        self.assertEqual(shape.calls[0], ("rotate", (1.0, 0.0, 0.0), 90.0))
        self.assertEqual(shape.calls[1], ("rotate", (0.0, 0.0, 1.0), 30.0))
        kind, (dx, dy, dz) = shape.calls[2]
        self.assertEqual(kind, "translate")
        self.assertAlmostEqual(dx, 4.0)
        self.assertAlmostEqual(dy, -1.0)
        # +Y up: the source's lowest Y (-2) becomes the bottom, so shift by +2.
        self.assertAlmostEqual(dz, 2.0)

    def test_shape_without_faces_is_rejected(self):
        shape = _Shape()
        shape.Faces = []
        with self.assertRaises(bs.BoardSolidError):
            bs.solid_from_shape(shape)


if __name__ == "__main__":
    unittest.main()
