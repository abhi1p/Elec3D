# -*- coding: utf-8 -*-
"""Board solid: mesh slicing, placement, persistence and legacy migration.

These tests are pure Python (no Qt, no FreeCAD).  ``filleted_board_mesh.json``
is a real OpenCascade tessellation of a 50 x 30 x 1.6 mm board with R3 corner
fillets, two 3 mm holes and a 10 x 10 x 0.5 mm pocket in the top face.
"""

import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d import boardsolid as bs                       # noqa: E402
from elec3d import designfile                             # noqa: E402
from elec3d.design import LayerStack, OutlineShape        # noqa: E402
from elec3d.routing import (BoardOutline, route_outline_conflicts,  # noqa: E402
                             via_outline_conflicts, Track, Via)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _circle(r, n=64, cx=0.0, cy=0.0):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
            for i in range(n)]


def _ring_solid(outer=10.0, inner=3.0, height=2.0):
    vo, to = bs._prism_mesh_from_sections([(0.0, [_circle(outer)]), (height, [_circle(outer)])])
    vi, ti = bs._prism_mesh_from_sections([(0.0, [_circle(inner)]), (height, [_circle(inner)])])
    off = len(vo)
    tris = to + [(off + c, off + b, off + a) for a, b, c in ti]   # hole faces point inward
    return bs.BoardSolid(vo + vi, tris)


def _polygon_area(loop):
    return abs(sum(loop[i][0] * loop[(i + 1) % len(loop)][1] -
                   loop[(i + 1) % len(loop)][0] * loop[i][1] for i in range(len(loop)))) / 2.0


class TestSlicing(unittest.TestCase):
    def test_box_section_inside_on_faces_and_above(self):
        box = bs.box_solid(20, 10, 1.6)
        for z in (0.0, 0.8, 1.6):          # bottom face, middle, top face
            loops = box.section(z)
            self.assertEqual(len(loops), 1, z)
            self.assertAlmostEqual(box.section_area(z), 200.0, places=6)
        self.assertEqual(box.section(1.61), [])
        self.assertEqual(box.section(-0.1), [])

    def test_tapered_solid_interpolates_between_faces(self):
        a = [(-10, -5), (10, -5), (10, 5), (-10, 5)]
        b = [(-5, -3), (5, -3), (5, 3), (-5, 3)]
        v, t = bs._prism_mesh_from_sections([(0.0, [a]), (1.0, [a]), (2.0, [b])])
        solid = bs.BoardSolid(v, t)
        self.assertAlmostEqual(solid.section_area(0.5), 200.0, places=6)
        self.assertAlmostEqual(solid.section_area(2.0), 60.0, places=6)
        # Halfway up the taper: 15 x 8
        self.assertAlmostEqual(solid.section_area(1.5), 120.0, places=6)

    def test_hole_is_a_nested_loop_that_subtracts(self):
        ring = _ring_solid()
        loops = ring.section(1.0)
        self.assertEqual(len(loops), 2)
        expect = _polygon_area(_circle(10.0)) - _polygon_area(_circle(3.0))
        self.assertAlmostEqual(ring.section_area(1.0), expect, places=6)

    def test_real_occ_tessellation_with_fillets_holes_and_pocket(self):
        with open(os.path.join(DATA, "filleted_board_mesh.json")) as fh:
            solid = bs.BoardSolid.restore(json.load(fh))
        holes = 2 * math.pi * 1.5 ** 2
        full = 1500.0 - (4.0 - math.pi) * 9.0 - holes
        # Chords of the tessellated arcs lose a few hundredths of a mm^2.
        for z, expect, loops in ((0.0, full, 3), (0.8, full, 3), (1.1, full, 3),
                                 (1.4, full - 100.0, 4), (1.6, full - 100.0, 4)):
            self.assertEqual(len(solid.section(z)), loops, z)
            self.assertAlmostEqual(solid.section_area(z), expect, delta=0.05)
        self.assertEqual(solid.section(1.7), [])
        self.assertAlmostEqual(solid.height, 1.6, places=6)

    def test_weld_merges_duplicated_face_vertices(self):
        # Two triangles of a square given with duplicated shared vertices.
        verts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 0, 0), (1, 1, 0), (0, 1, 0)]
        v, t = bs.weld_mesh(verts, [(0, 1, 2), (3, 4, 5)])
        self.assertEqual(len(v), 4)
        self.assertEqual(len(t), 2)


class TestPlacement(unittest.TestCase):
    def test_lowest_point_is_the_reference_plane(self):
        verts = [(x, y, z + 7.5) for x, y, z in bs.box_solid(4, 4, 2).vertices]
        solid = bs.BoardSolid(verts, bs.box_solid(4, 4, 2).triangles, weld=False)
        b = solid.world_bounds()
        self.assertAlmostEqual(b[2], 0.0)
        self.assertAlmostEqual(b[5], 2.0)

    def test_up_axis_reorients_the_solid(self):
        solid = bs.box_solid(10, 4, 2)          # 10 (x) x 4 (y) x 2 (z)
        self.assertTrue(solid.set_placement(up_axis="+Y"))
        self.assertAlmostEqual(solid.height, 4.0)
        self.assertTrue(solid.set_placement(up_axis="-X"))
        self.assertAlmostEqual(solid.height, 10.0)
        self.assertFalse(solid.set_placement(up_axis="-X"))

    def test_rotation_offset_and_board_coordinates(self):
        solid = bs.box_solid(10, 4, 2)
        solid.set_placement(rotation=90.0, offset_x=5.0, offset_y=2.0)
        minx, miny, maxx, maxy = solid.board_bounds()      # Y-down editor frame
        self.assertAlmostEqual(minx, 3.0); self.assertAlmostEqual(maxx, 7.0)
        self.assertAlmostEqual(miny, -7.0); self.assertAlmostEqual(maxy, 3.0)
        loop = solid.board_section(1.0)[0]
        self.assertTrue(all(abs(y) <= 7.0 + 1e-9 for _x, y in loop))

    def test_center_on_origin(self):
        solid = bs.box_solid(10, 4, 2, center=(30.0, -12.0))
        self.assertTrue(solid.center_on_origin())
        b = solid.world_bounds()
        self.assertAlmostEqual((b[0] + b[3]) / 2.0, 0.0)
        self.assertAlmostEqual((b[1] + b[4]) / 2.0, 0.0)

    def test_signature_changes_with_placement(self):
        solid = bs.box_solid(10, 4, 2)
        before = solid.signature()
        solid.set_placement(offset_x=1.0)
        self.assertNotEqual(before, solid.signature())


class TestPersistence(unittest.TestCase):
    def test_snapshot_round_trip_through_design_file(self):
        ring = _ring_solid()
        ring.source = {"kind": "step", "path": "${MODEL_DIR}/ring.step", "label": "ring.step"}
        ring.set_placement(rotation=30.0, offset_x=4.0, offset_y=-2.0)
        text = designfile.dumps({"board_solid": ring.snapshot(),
                                 "stack": {"layers": [], "stackup": {"bottom_margin": 0.4}}})
        self.assertIn("(board_solid", text)
        self.assertNotIn("top_margin", text)
        data = designfile.loads(text)
        restored = bs.BoardSolid.restore(data["board_solid"])
        self.assertEqual(restored.source["path"], "${MODEL_DIR}/ring.step")
        self.assertEqual(restored.placement["rotation"], 30.0)
        self.assertAlmostEqual(restored.section_area(1.0), ring.section_area(1.0), places=3)
        self.assertEqual(data["stack"]["stackup"]["bottom_margin"], 0.4)
        self.assertNotIn("top_margin", data["stack"]["stackup"])


class TestStackupReference(unittest.TestCase):
    def test_reference_heights_start_at_bottom_margin(self):
        stack = LayerStack()
        top = stack.add()
        stack.set_bottom_margin(0.25)
        stack.set_separation(stack.bottom().id, top.id, 1.2)
        heights = stack.reference_heights()
        self.assertAlmostEqual(heights[stack.bottom().id], 0.25)
        self.assertAlmostEqual(heights[top.id], 1.45)
        self.assertAlmostEqual(stack.top_reference_height(), 1.45)
        self.assertFalse(hasattr(stack, "set_top_margin"))
        self.assertNotIn("top_margin", stack.snapshot()["stackup"])

    def test_legacy_top_margin_is_kept_for_migration(self):
        stack = LayerStack()
        stack.restore({"layers": [{"id": 1, "name": "B.Cu", "color": "#000"}],
                       "stackup": {"top_margin": 0.7, "bottom_margin": 0.5}})
        self.assertAlmostEqual(stack.legacy_top_margin, 0.7)
        self.assertAlmostEqual(stack.bottom_margin, 0.5)


class TestLegacyMigration(unittest.TestCase):
    def test_drawn_outline_becomes_an_equivalent_solid(self):
        stack = LayerStack()
        top = stack.add()
        outlines = [OutlineShape("a", "rectangle", layer_id=top.id, x=0, y=0, width=30, height=20)]
        solid = bs.solid_from_legacy_outlines(stack, outlines, top_margin=1.0)
        self.assertIsNotNone(solid)
        self.assertEqual(solid.kind, "legacy")
        heights = stack.reference_heights()
        for layer in stack.layers:
            self.assertAlmostEqual(solid.section_area(heights[layer.id]), 600.0, places=5)
        self.assertAlmostEqual(solid.height, stack.top_reference_height() + 1.0)

    def test_per_layer_outlines_are_lofted(self):
        stack = LayerStack()
        top = stack.add()
        outlines = [
            OutlineShape("a", "rectangle", layer_id=stack.bottom().id, x=0, y=0, width=40, height=20),
            OutlineShape("b", "rectangle", layer_id=top.id, x=0, y=0, width=20, height=20),
        ]
        solid = bs.solid_from_legacy_outlines(stack, outlines)
        heights = stack.reference_heights()
        self.assertAlmostEqual(solid.section_area(heights[stack.bottom().id]), 800.0, delta=1e-3)
        self.assertAlmostEqual(solid.section_area(heights[top.id]), 400.0, delta=1e-3)

    def test_no_closed_outline_means_no_solid(self):
        stack = LayerStack()
        line = OutlineShape("a", "line", layer_id=1, x1=0, y1=0, x2=5, y2=0)
        self.assertIsNone(bs.solid_from_legacy_outlines(stack, [line]))


class TestRoutingBoundary(unittest.TestCase):
    def setUp(self):
        solid = bs.box_solid(20, 10, 1.6)
        self.boundary = BoardOutline.from_loops(solid.board_section(0.8), blocking=True)
        self.outside = BoardOutline.from_loops(solid.board_section(5.0), blocking=True)

    def test_inside_track_is_legal_and_crossing_track_is_not(self):
        inside = Track(layer_id=1, width=0.25, points=[(-5, 0), (5, 0)])
        crossing = Track(layer_id=1, width=0.25, points=[(0, 0), (12, 0)])
        self.assertEqual(route_outline_conflicts(inside, self.boundary), [])
        self.assertTrue(route_outline_conflicts(crossing, self.boundary))

    def test_plane_outside_solid_blocks_everything(self):
        self.assertTrue(self.outside.active)
        via = Via(x=0.0, y=0.0, diameter=0.6, start_layer_id=1, end_layer_id=1)
        self.assertTrue(via_outline_conflicts(via, self.outside))

    def test_no_solid_means_unconstrained(self):
        track = Track(layer_id=1, width=0.25, points=[(0, 0), (500, 0)])
        self.assertEqual(route_outline_conflicts(track, None), [])


if __name__ == "__main__":
    unittest.main()
