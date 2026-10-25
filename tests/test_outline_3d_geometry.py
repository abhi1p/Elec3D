"""Legacy (pre-0.17) drawn-outline geometry, kept for migrating old boards."""

import math
import unittest

from elec3d.design import OutlineShape
from elec3d.meshgeom import outline_loops as _outline_loops

try:
    from elec3d.view3d import _SceneBuilder
    HAVE_VIEW3D = True
except ImportError:
    _SceneBuilder = None
    HAVE_VIEW3D = False


class TestOutline3DGeometry(unittest.TestCase):
    def test_ellipse_loop_keeps_ellipse_dimensions(self):
        shape = OutlineShape("e", "ellipse", x=3.0, y=-2.0, width=20.0, height=8.0)
        loops = _outline_loops([shape], segments=96)
        self.assertEqual(len(loops), 1)
        xs = [p[0] for p in loops[0]]; ys = [p[1] for p in loops[0]]
        self.assertAlmostEqual(max(xs) - min(xs), 20.0, places=6)
        self.assertAlmostEqual(max(ys) - min(ys), 8.0, places=6)

    @unittest.skipUnless(HAVE_VIEW3D, "no PySide binding available for view3d import")
    def test_circle_prism_does_not_create_bbox_corners(self):
        shape = OutlineShape("c", "circle", x=0.0, y=0.0, radius=5.0)
        loop = _outline_loops([shape], segments=64)[0]
        scene = _SceneBuilder()
        scene.prism([(x, -y) for x, y in loop], 0.0, 2.0, "#ffffff", 0.2)
        points = [p for tri in scene.triangles for p in tri[:3]]
        self.assertTrue(points)
        # A bbox extrusion would contain (±5, ±5); a circular prism must not.
        self.assertFalse(any(abs(abs(p[0])-5.0) < 1e-8 and abs(abs(p[1])-5.0) < 1e-8
                             for p in points))

    def test_connected_lines_form_closed_polygon(self):
        shapes = [
            OutlineShape("1", "line", x1=0, y1=0, x2=4, y2=0),
            OutlineShape("2", "line", x1=4, y1=4, x2=0, y2=4),
            OutlineShape("3", "line", x1=4, y1=0, x2=4, y2=4),
            OutlineShape("4", "line", x1=0, y1=4, x2=0, y2=0),
        ]
        loops = _outline_loops(shapes)
        self.assertEqual(len(loops), 1)
        self.assertEqual(len(loops[0]), 4)


if __name__ == "__main__":
    unittest.main()
