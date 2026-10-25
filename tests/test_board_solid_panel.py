# -*- coding: utf-8 -*-
"""Board solid integration with the 2D editor (Qt, offscreen).

    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_board_solid_panel.py
"""

import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d import boardsolid as bs                       # noqa: E402
from elec3d import designfile                             # noqa: E402
from elec3d.footprint import parse_footprint_file          # noqa: E402
from elec3d.routing import Track                          # noqa: E402

try:
    from elec3d.qt import QtWidgets
    from elec3d.panel2d import Viewer2D
    HAVE_QT = True
except ImportError:                                         # pragma: no cover
    HAVE_QT = False

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SAMPLE = os.path.join(DATA, "NE555DR.kicad_mod")
_app = None


def setUpModule():
    global _app
    if HAVE_QT:
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _taper():
    """40 x 30 mm up to z = 1, tapering to 30 x 20 mm at z = 3."""
    a = [(-20, -15), (20, -15), (20, 15), (-20, 15)]
    b = [(-15, -10), (15, -10), (15, 10), (-15, 10)]
    v, t = bs._prism_mesh_from_sections([(0.0, [a]), (1.0, [a]), (3.0, [b])])
    return bs.BoardSolid(v, t, source={"kind": "generated", "label": "taper"})


def _width(loops):
    xs = [p[0] for loop in loops for p in loop]
    return max(xs) - min(xs)


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class BoardPanelCase(unittest.TestCase):
    def setUp(self):
        self.viewer = Viewer2D(theme="dark")
        self.viewer.resize(1100, 750)
        self.viewer.show()
        self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.viewer._on_stack_add()                      # B.Cu at 1.0, F.Cu at 2.6
        _app.processEvents()

    def tearDown(self):
        self.viewer.hide()
        self.viewer.deleteLater()
        _app.processEvents()

    def layers(self):
        return self.viewer.stack.bottom(), self.viewer.stack.top()


class TestImportAndSections(BoardPanelCase):
    def test_every_copper_layer_gets_its_own_section(self):
        self.viewer._set_board_solid(_taper(), "test")
        bottom, top = self.layers()
        sections = self.viewer._board_sections()
        self.assertAlmostEqual(_width(sections[bottom.id]), 40.0, places=6)   # z = 1.0
        self.assertAlmostEqual(_width(sections[top.id]), 32.0, places=6)      # z = 2.6
        self.assertEqual(sorted(self.viewer.canvas.board_edge_items), sorted([bottom.id, top.id]))
        self.assertEqual(self.viewer.canvas.board_edge_active_id, top.id)
        self.assertTrue(self.viewer.board_card.isVisible())
        self.assertFalse(self.viewer.board_empty.isVisible())

    def test_stackup_changes_rederive_the_sections(self):
        self.viewer._set_board_solid(_taper(), "test")
        bottom, top = self.layers()
        self.viewer.stack.set_separation(bottom.id, top.id, 1.0)       # F.Cu now at 2.0
        self.viewer._on_stack_geometry_changed()
        self.assertAlmostEqual(_width(self.viewer._board_sections()[top.id]), 35.0, places=6)

    def test_plane_above_the_solid_is_flagged_and_blocks_placement(self):
        self.viewer._set_board_solid(_taper(), "test")
        bottom, top = self.layers()
        self.viewer.stack.set_separation(bottom.id, top.id, 5.0)       # F.Cu at 6.0
        self.viewer._on_stack_geometry_changed()
        text, role = self.viewer._board_layer_status(6.0)
        self.assertEqual(role, "layerBad")
        boundary = self.viewer._board_outline_for_layer(top.id)
        self.assertTrue(boundary.blocking)
        self.assertIn("⚠", self.viewer.sec_board.header.text())

    def test_without_a_solid_routing_is_unconstrained(self):
        self.assertIsNone(self.viewer._board_outline_for_layer(self.viewer.stack.top().id))
        self.assertTrue(self.viewer.board_empty.isVisible())
        self.assertEqual(self.viewer.canvas.board_edge_items, {})


class TestDrcAndRouting(BoardPanelCase):
    def test_full_drc_reports_copper_crossing_the_board_edge(self):
        self.viewer._set_board_solid(_taper(), "test")
        top = self.viewer.stack.top()
        self.viewer.tracks = [Track(layer_id=top.id, width=0.25, points=[(0.0, 0.0), (25.0, 0.0)])]
        rules = [issue["rule"] for issue in self.viewer._collect_drc_violations()]
        self.assertTrue(any("outline" in rule or "edge" in rule for rule in rules), rules)

    def test_layer_violation_helper_uses_the_derived_section(self):
        self.viewer._set_board_solid(_taper(), "test")
        top = self.viewer.stack.top()
        self.viewer.tracks = [Track(layer_id=top.id, width=0.25, points=[(-5.0, 2.0), (5.0, 2.0)])]
        self.viewer._invalidate_routing_cache()
        self.assertEqual(self.viewer._layer_outline_drc_violation(top.id), "")
        # 15 mm is inside the 40 mm wide bottom section but outside F.Cu's 32 mm.
        self.viewer.tracks = [Track(layer_id=top.id, width=0.25, points=[(0.0, 0.0), (17.0, 0.0)])]
        self.assertIn("outline-to-track", self.viewer._layer_outline_drc_violation(top.id))


class TestHistoryAndPlacement(BoardPanelCase):
    def test_import_and_remove_are_undoable(self):
        solid = _taper()
        self.viewer._set_board_solid(solid, "test")
        self.viewer.remove_board_solid()
        self.assertIsNone(self.viewer.board_solid)
        self.viewer.undo()
        self.assertIs(self.viewer.board_solid, solid)
        self.viewer.undo()
        self.assertIsNone(self.viewer.board_solid)
        self.viewer.redo()
        self.assertIs(self.viewer.board_solid, solid)

    def test_offset_edit_is_one_undo_step_in_editor_coordinates(self):
        solid = _taper()
        self.viewer._set_board_solid(solid, "test")
        depth = len(self.viewer._history)
        self.viewer.board_offset["y"].setValue(5.0)          # editor +Y is down
        self.viewer._on_board_placement_edit(commit=True)
        self.assertEqual(len(self.viewer._history), depth + 1)
        self.assertAlmostEqual(solid.placement["offset_y"], -5.0)
        miny = min(p[1] for loop in self.viewer._board_sections()[self.layers()[0].id] for p in loop)
        self.assertAlmostEqual(miny, -10.0)                  # was -15, moved 5 mm down
        self.viewer.undo()
        self.assertAlmostEqual(solid.placement["offset_y"], 0.0)
        self.assertAlmostEqual(self.viewer.board_offset["y"].value(), 0.0)

    def test_center_moves_the_solid_onto_the_origin(self):
        solid = _taper()
        solid.set_placement(offset_x=40.0)
        self.viewer._set_board_solid(solid, "test")
        self.assertTrue(self.viewer.center_board_solid())
        b = solid.world_bounds()
        self.assertAlmostEqual((b[0] + b[3]) / 2.0, 0.0)

    def test_replacing_keeps_the_previous_placement(self):
        first = _taper()
        first.set_placement(up_axis="+Z", rotation=90.0, offset_x=3.0)
        self.viewer._set_board_solid(first, "test")
        original = bs.load_step
        try:
            bs.load_step = lambda path, placement=None: bs.BoardSolid(
                first.vertices, first.triangles, placement=placement, weld=False,
                source={"kind": "step", "path": path, "label": "rev2.step"})
            self.assertTrue(self.viewer.import_board_step("/tmp/rev2.step"))
        finally:
            bs.load_step = original
        self.assertEqual(self.viewer.board_solid.label, "rev2.step")
        self.assertEqual(self.viewer.board_solid.placement["rotation"], 90.0)
        self.assertEqual(self.viewer.board_solid.placement["offset_x"], 3.0)

    def test_placement_change_marks_the_design_modified(self):
        self.viewer._set_board_solid(_taper(), "test")
        self.viewer._capture_saved_content_state()
        self.viewer.refresh_modified_state()
        self.assertFalse(self.viewer.is_modified())
        self.viewer.board_rotation.setValue(45.0)
        self.viewer._on_board_placement_edit(commit=True)
        self.viewer.refresh_modified_state()
        self.assertTrue(self.viewer.is_modified())


class TestPersistence(BoardPanelCase):
    def setUp(self):
        BoardPanelCase.setUp(self)
        self.tmp = tempfile.mkdtemp(prefix="e3d-board-")

    def tearDown(self):
        BoardPanelCase.tearDown(self)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_board_solid_round_trips_through_a_board_file(self):
        solid = _taper()
        solid.set_placement(rotation=90.0, offset_x=2.0)
        self.viewer._set_board_solid(solid, "test")
        before = {k: _width(v) for k, v in self.viewer._board_sections().items()}
        path = os.path.join(self.tmp, "board.e3d_pcb")
        designfile.write_file(path, self.viewer._design_snapshot(path))
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("(board_solid", text)
        self.assertNotIn("(outline", text)
        self.assertNotIn("top_margin", text)

        fresh = Viewer2D(theme="dark")
        try:
            fresh.load_design_from(path)
            self.assertIsNotNone(fresh.board_solid)
            self.assertEqual(fresh.board_solid.placement["rotation"], 90.0)
            after = {k: _width(v) for k, v in fresh._board_sections().items()}
            for key, value in before.items():
                self.assertAlmostEqual(after[key], value, places=4)
        finally:
            fresh.deleteLater()

    def test_legacy_outline_board_is_converted_on_open(self):
        bottom, top = self.layers()
        stack = self.viewer.stack.snapshot()
        stack["stackup"]["top_margin"] = 0.8
        legacy = {
            "format_version": 4, "stack": stack, "footprints": [],
            "outlines": [{"uid": "o1", "kind": "rectangle", "layer_id": top.id,
                          "x": 0.0, "y": 0.0, "width": 30.0, "height": 20.0}],
            "routing": {}, "view": {},
        }
        text = designfile.dumps(legacy).replace("(version 4)", "(version 4)")
        # dumps() no longer writes outlines; append the legacy block by hand.
        text = text.rstrip().rstrip(")") + (
            '  (outline\n    (shape "o1" "rectangle"\n'
            '      (property "layer_id" %d)\n      (property "x" 0)\n      (property "y" 0)\n'
            '      (property "width" 30)\n      (property "height" 20)\n    )\n  )\n)\n' % top.id)
        text = text.replace("(stackup\n", "(stackup\n      (top_margin 0.8)\n", 1)
        path = os.path.join(self.tmp, "legacy.e3d_pcb")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        fresh = Viewer2D(theme="dark")
        try:
            fresh.load_design_from(path)
            solid = fresh.board_solid
            self.assertIsNotNone(solid)
            self.assertEqual(solid.kind, "legacy")
            for loops in fresh._board_sections().values():
                self.assertAlmostEqual(_width(loops), 30.0, places=5)
            self.assertAlmostEqual(solid.height, fresh.stack.top_reference_height() + 0.8, places=6)
        finally:
            fresh.deleteLater()


if __name__ == "__main__":
    unittest.main()
