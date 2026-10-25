# -*- coding: utf-8 -*-
"""Tests for the 2D viewer.

Runs headless (``QT_QPA_PLATFORM=offscreen``) and is skipped entirely when no
PySide binding is installed, so ``python3 -m unittest discover -s tests`` still
works on a machine that only has the parser dependencies.

    QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d import __version__                            # noqa: E402
from elec3d.footprint import parse_footprint_file          # noqa: E402

try:
    from elec3d.qt import Qt, QtCore, QtGui, QtWidgets
    from elec3d import view2d
    from elec3d.panel2d import Viewer2D
    HAVE_QT = True
except ImportError:                                         # pragma: no cover
    HAVE_QT = False

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SAMPLE = os.path.join(DATA, "NE555DR.kicad_mod")
SHAPES = os.path.join(DATA, "SHAPES_TEST.kicad_mod")

_app = None


def setUpModule():
    global _app
    if HAVE_QT:
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestPadPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fp = parse_footprint_file(SHAPES)
        cls.pads = dict((p.number, p) for p in cls.fp.pads)

    def _rect(self, number):
        return view2d.pad_path(self.pads[number]).boundingRect()

    def test_sizes_match_the_file(self):
        for number, w, h in (("1", 1.8, 1.8), ("3", 2.6, 1.6),
                             ("4", 2.0, 1.6), ("8", 2.4, 1.0)):
            rect = self._rect(number)
            self.assertAlmostEqual(rect.width(), w, places=3,
                                   msg="pad %s width" % number)
            self.assertAlmostEqual(rect.height(), h, places=3,
                                   msg="pad %s height" % number)

    def test_drill_is_punched_out(self):
        pad = self.pads["1"]
        path = view2d.pad_path(pad)
        # centre sits in the drill, a point just outside the drill is copper
        self.assertFalse(path.contains(QtCore.QPointF(0.0, 0.0)))
        self.assertTrue(path.contains(QtCore.QPointF(0.7, 0.0)))

    def test_drill_stays_curved(self):
        """Booleans must not flatten a 0.9mm hole into a visible polygon."""
        pad = self.pads["1"]
        path = view2d.pad_path(pad)
        curves = sum(1 for i in range(path.elementCount())
                     if path.elementAt(i).type == QtGui.QPainterPath.CurveToElement)
        self.assertGreaterEqual(curves, 8)      # 4 for the pad, 4 for the drill

    def test_oval_drill(self):
        path = view2d.pad_path(self.pads["3"])
        self.assertFalse(path.contains(QtCore.QPointF(0.0, 0.0)))
        self.assertTrue(path.contains(QtCore.QPointF(1.1, 0.0)))

    def test_chamfered_roundrect_differs_from_plain(self):
        plain = view2d.pad_path(self.pads["4"])
        chamfered = view2d.pad_path(self.pads["5"])
        curves = lambda p: sum(1 for i in range(p.elementCount())
                               if p.elementAt(i).type == QtGui.QPainterPath.CurveToElement)
        self.assertEqual(curves(plain), 4)      # four rounded corners
        self.assertEqual(curves(chamfered), 2)  # two rounded, two chamfered

    def test_trapezoid_uses_rect_delta(self):
        pad = self.pads["6"]                    # size 2 x 1.6, rect_delta 0.8 0
        path = view2d.pad_path(pad)
        rect = path.boundingRect()
        self.assertAlmostEqual(rect.height(), 2.4, places=3)   # 1.6 + 0.8
        self.assertTrue(path.contains(QtCore.QPointF(-0.9, 1.0)))   # tall left edge
        self.assertFalse(path.contains(QtCore.QPointF(0.9, 1.0)))   # short right edge

    def test_np_thru_hole_is_fully_drilled(self):
        path = view2d.pad_path(self.pads["10"])  # 2.4mm pad, 2.4mm drill
        self.assertFalse(path.contains(QtCore.QPointF(0.0, 0.0)))


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestFootprintItem(unittest.TestCase):
    def setUp(self):
        self.fp = parse_footprint_file(SAMPLE)
        self.item = view2d.FootprintItem(self.fp)

    def test_pad_items_and_labels(self):
        self.assertEqual(len(self.item.pad_items), 8)
        self.assertEqual(len(self.item.pad_labels), 8)

    def test_hidden_text_excluded_from_extent(self):
        """The hidden value string is far wider than the part itself."""
        visible = self.item.visible_rect()
        self.assertLess(visible.width(), 9.0)
        self.item.set_hidden_texts_visible(True)
        self.assertGreater(self.item.visible_rect().width(), 15.0)

    def test_hidden_text_excluded_from_click_shape(self):
        """Invisible value text must not create a large empty hit target."""
        self.assertLess(self.item.shape().boundingRect().width(), 9.0)
        self.item.set_hidden_texts_visible(True)
        self.assertGreater(self.item.shape().boundingRect().width(), 15.0)

    def test_entire_visible_bounding_rect_is_clickable(self):
        """Empty space inside the footprint outline is a selection target."""
        rect = self.item.visible_rect()
        shape = self.item.shape()
        self.assertTrue(shape.contains(rect.center()))
        self.assertAlmostEqual(shape.boundingRect().left(), rect.left(), places=6)
        self.assertAlmostEqual(shape.boundingRect().right(), rect.right(), places=6)
        self.assertAlmostEqual(shape.boundingRect().top(), rect.top(), places=6)
        self.assertAlmostEqual(shape.boundingRect().bottom(), rect.bottom(), places=6)

    def test_layer_toggle(self):
        count = lambda: sum(1 for i in self.item.childItems() if i.isVisible())
        before = count()
        self.item.set_layer_visible("F.SilkS", False)
        self.assertLess(count(), before)
        self.item.set_layer_visible("F.SilkS", True)
        self.assertEqual(count(), before)

    def test_layer_visibility_state_is_saved(self):
        self.assertTrue(self.item.is_layer_visible("F.SilkS"))
        self.item.set_layer_visible("F.SilkS", False)
        self.assertFalse(self.item.is_layer_visible("F.SilkS"))
        self.item.set_layer_visible("F.SilkS", True)
        self.assertTrue(self.item.is_layer_visible("F.SilkS"))

    def test_theme_keeps_pad_labels_readable(self):
        for theme in ("light", "dark"):
            self.item.apply_theme(theme)
            self.assertEqual(self.item.pad_labels[0].brush().color().name(),
                             view2d.PAD_LABEL[theme].lower())


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestEmptyCanvas(unittest.TestCase):
    def test_empty_scene_has_pan_workspace(self):
        canvas = view2d.FootprintCanvas()
        try:
            rect = canvas.scene().sceneRect()
            self.assertGreater(rect.width(), 1000.0)
            self.assertGreater(rect.height(), 1000.0)
            self.assertTrue(rect.contains(QtCore.QPointF(0.0, 0.0)))
        finally:
            canvas.close()

    def test_pan_workspace_is_not_shrunk_after_import(self):
        canvas = view2d.FootprintCanvas()
        try:
            before = QtCore.QRectF(canvas.scene().sceneRect())
            canvas.add_footprint(parse_footprint_file(SAMPLE))
            after = canvas.scene().sceneRect()
            self.assertLessEqual(after.left(), before.left())
            self.assertLessEqual(after.top(), before.top())
            self.assertGreaterEqual(after.right(), before.right())
            self.assertGreaterEqual(after.bottom(), before.bottom())
        finally:
            canvas.close()

    def test_content_outside_default_workspace_expands_scene(self):
        canvas = view2d.FootprintCanvas()
        try:
            item = canvas.add_footprint(parse_footprint_file(SAMPLE),
                                        QtCore.QPointF(15000.0, 0.0))
            canvas._update_scene_rect()
            self.assertTrue(canvas.scene().sceneRect().contains(
                item.sceneBoundingRect()))
            self.assertLessEqual(canvas.scene().sceneRect().left(),
                                 -view2d.PAN_WORKSPACE_HALF_SPAN)
        finally:
            canvas.close()

    def test_cursor_snaps_to_active_grid(self):
        canvas = view2d.FootprintCanvas()
        try:
            canvas.set_grid(step=0.1, snap=True)
            point = canvas._snap_cursor_point(QtCore.QPointF(2.725, 0.525))
            self.assertAlmostEqual(point.x(), 2.7, places=9)
            self.assertAlmostEqual(point.y(), 0.5, places=9)
        finally:
            canvas.close()

    def test_cursor_is_raw_when_snap_is_disabled(self):
        canvas = view2d.FootprintCanvas()
        try:
            canvas.set_grid(step=0.1, snap=False)
            point = canvas._snap_cursor_point(QtCore.QPointF(2.725, 0.525))
            self.assertAlmostEqual(point.x(), 2.725, places=9)
            self.assertAlmostEqual(point.y(), 0.525, places=9)
        finally:
            canvas.close()

    def test_horizontal_pan_round_trip_has_no_diagonal_drift(self):
        canvas = view2d.FootprintCanvas()
        canvas.resize(800, 600)
        canvas.show()
        _app.processEvents()

        class PanEvent(object):
            def __init__(self, pos):
                self._pos = pos
                self.accepted = False

            def pos(self):
                return self._pos

            def accept(self):
                self.accepted = True

        try:
            origin = QtCore.QPoint(400, 300)
            h0 = canvas.horizontalScrollBar().value()
            v0 = canvas.verticalScrollBar().value()
            canvas._panning = True
            canvas._pan_origin = QtCore.QPoint(origin)
            canvas._pan_scroll_origin = QtCore.QPoint(h0, v0)

            # Repeated horizontal excursions must never change the vertical
            # position, and returning the pointer to the press point must
            # return both scrollbars to exactly their starting values.
            for _ in range(50):
                canvas.mouseMoveEvent(PanEvent(origin + QtCore.QPoint(137, 0)))
                self.assertEqual(canvas.verticalScrollBar().value(), v0)
                canvas.mouseMoveEvent(PanEvent(origin - QtCore.QPoint(91, 0)))
                self.assertEqual(canvas.verticalScrollBar().value(), v0)
                canvas.mouseMoveEvent(PanEvent(origin))
                self.assertEqual(canvas.horizontalScrollBar().value(), h0)
                self.assertEqual(canvas.verticalScrollBar().value(), v0)
        finally:
            canvas._panning = False
            canvas.close()


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestEmptyCanvasOrigin(unittest.TestCase):
    def test_origin_is_centred_when_launched_empty(self):
        viewer = Viewer2D()
        try:
            viewer.resize(1300, 800)
            viewer.show()
            _app.processEvents()
            viewer.resize(1500, 900)                  # host settling into its size
            _app.processEvents()
            canvas = viewer.canvas
            centre = canvas.mapToScene(canvas.viewport().rect().center())
            tolerance = 2.0 / canvas.px_per_mm()      # within a couple of pixels
            self.assertLess(abs(centre.x()), tolerance)
            self.assertLess(abs(centre.y()), tolerance)
        finally:
            viewer.close()


class TestViewer(unittest.TestCase):
    def setUp(self):
        self.viewer = Viewer2D()
        self.viewer.resize(1000, 700)
        self.item = self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.viewer.show()
        _app.processEvents()

    def tearDown(self):
        self.viewer.close()

    def test_fit_uses_the_whole_viewport(self):
        self.viewer.canvas.fit()
        # 7.45mm wide part in a ~750px viewport: far more than the 40 px/mm default
        self.assertGreater(self.viewer.canvas.px_per_mm(), 60)

    def test_sidebar_reflects_the_footprint(self):
        self.assertEqual(self.viewer.title.text(), "NE555DR")
        self.assertEqual(self.viewer.layer_layout.count(), 3)   # F.SilkS, Dwgs.User, F.Cu
        self.assertEqual(self.viewer.pad_layout.count(), 8)

    def test_hover_reports_pad_details(self):
        pad = self.item.pad_items["1"]
        pos = self.viewer.canvas.mapFromScene(pad.sceneBoundingRect().center())
        self.viewer.canvas._update_hover(pos)
        self.assertIn("pad 1", self.viewer.status_hover.text())
        self.assertIn("smd", self.viewer.status_hover.text())

    def test_click_shows_pad_properties(self):
        pad = self.item.pad_items["3"]
        pos = self.viewer.canvas.mapFromScene(pad.sceneBoundingRect().center())
        self.viewer.report_click(self.viewer.canvas._info_at(pos))
        self.assertEqual(self.viewer.prop_kind.text(), "Pad 3")

    def test_grid_snap_on_move(self):
        self.viewer.canvas.set_grid(step=0.5, snap=True)
        self.item.setPos(QtCore.QPointF(3.14, -2.71))
        self.assertAlmostEqual(self.item.pos().x(), 3.0, places=6)
        self.assertAlmostEqual(self.item.pos().y(), -2.5, places=6)

    def test_snap_can_be_disabled(self):
        self.viewer.canvas.set_grid(step=0.5, snap=False)
        self.item.setPos(QtCore.QPointF(3.14, -2.71))
        self.assertAlmostEqual(self.item.pos().x(), 3.14, places=6)

    def test_cursor_readout(self):
        self.viewer._on_cursor(1.2345, -6.5)
        self.assertIn("1.234", self.viewer.status_pos.text())

    def test_sidebar_rebuild_does_not_create_top_level_rows(self):
        self.viewer._rebuild_layers()
        self.viewer._rebuild_pads()
        _app.processEvents()
        top_levels = QtWidgets.QApplication.topLevelWidgets()
        floating_layers = [w for w in top_levels
                           if w.__class__.__name__ == "LayerRow"]
        self.assertEqual(floating_layers, [])
        for i in range(self.viewer.layer_layout.count()):
            row = self.viewer.layer_layout.itemAt(i).widget()
            self.assertFalse(row.isWindow())
            self.assertIs(row.parentWidget(), self.viewer.layer_box)

    def _layer_row(self, name):
        for i in range(self.viewer.layer_layout.count()):
            row = self.viewer.layer_layout.itemAt(i).widget()
            if getattr(row, "layer", None) == name:
                return row
        self.fail("missing layer row %s" % name)

    def test_layer_checkbox_reflects_saved_visibility_after_rebuild(self):
        row = self._layer_row("F.SilkS")
        row.check.setChecked(False)
        _app.processEvents()
        self.assertFalse(self.item.is_layer_visible("F.SilkS"))

        self.viewer._rebuild_layers()
        _app.processEvents()
        self.assertFalse(self._layer_row("F.SilkS").check.isChecked())

    def test_layer_checkbox_state_survives_deselect_and_reselect(self):
        first_entry = self.viewer.active_entry()
        self._layer_row("F.SilkS").check.setChecked(False)
        _app.processEvents()

        second_item = self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        second_entry = self.viewer.active_entry()
        self.assertIs(second_entry["item"], second_item)
        self.viewer._select_entry(first_entry)
        _app.processEvents()

        self.assertFalse(self._layer_row("F.SilkS").check.isChecked())
        self.assertFalse(first_entry["item"].is_layer_visible("F.SilkS"))

    def test_layer_toggle_preserves_scene_selection(self):
        self.assertTrue(self.item.isSelected())
        self._layer_row("F.SilkS").check.setChecked(False)
        _app.processEvents()
        self.assertTrue(self.item.isSelected())

    def test_rotate_counter_clockwise_and_undo_redo(self):
        self.assertAlmostEqual(self.item.rotation(), 0.0)
        self.assertTrue(self.viewer.rotate_selected(clockwise=False))
        # Qt is Y-down: 270 Qt degrees is 90 degrees CCW on screen.
        self.assertAlmostEqual(self.item.rotation(), 270.0)
        self.assertAlmostEqual(self.viewer.prop_rotation.value(), 90.0)

        self.assertTrue(self.viewer.undo())
        self.assertAlmostEqual(self.item.rotation(), 0.0)
        self.assertTrue(self.viewer.redo())
        self.assertAlmostEqual(self.item.rotation(), 270.0)

    def test_shift_r_direction_is_clockwise(self):
        self.assertTrue(self.viewer.rotate_selected(clockwise=True))
        self.assertAlmostEqual(self.item.rotation(), 90.0)
        # User-facing CAD angle is CCW-positive, so 90 CW reads as 270.
        self.assertAlmostEqual(self.viewer.prop_rotation.value(), 270.0)

    def test_multi_selection_rotates_around_shared_center_and_undoes(self):
        second = self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.item.setPos(QtCore.QPointF(-8.0, 3.0))
        second.setPos(QtCore.QPointF(14.0, 11.0))
        self.viewer.canvas.select_footprints([self.item, second])
        _app.processEvents()

        bounds = self.item.mapRectToScene(self.item.visible_rect()).united(
            second.mapRectToScene(second.visible_rect()))
        center = bounds.center()
        old_positions = [QtCore.QPointF(item.pos()) for item in (self.item, second)]

        self.assertTrue(self.viewer.rotate_selected(clockwise=False))
        for item, old in zip((self.item, second), old_positions):
            # R is -90 Qt degrees. In the Y-down scene this is a visual CCW
            # turn around the shared center: (dx, dy) -> (dy, -dx).
            dx = old.x() - center.x()
            dy = old.y() - center.y()
            self.assertAlmostEqual(item.pos().x(), center.x() + dy, places=6)
            self.assertAlmostEqual(item.pos().y(), center.y() - dx, places=6)
            self.assertAlmostEqual(item.rotation(), 270.0)

        after_positions = [QtCore.QPointF(item.pos()) for item in (self.item, second)]
        self.assertTrue(self.viewer.undo())
        for item, old in zip((self.item, second), old_positions):
            self.assertAlmostEqual(item.pos().x(), old.x(), places=6)
            self.assertAlmostEqual(item.pos().y(), old.y(), places=6)
            self.assertAlmostEqual(item.rotation(), 0.0)

        self.assertTrue(self.viewer.redo())
        for item, after in zip((self.item, second), after_positions):
            self.assertAlmostEqual(item.pos().x(), after.x(), places=6)
            self.assertAlmostEqual(item.pos().y(), after.y(), places=6)
            self.assertAlmostEqual(item.rotation(), 270.0)

    def test_drag_guard_preserves_group_rotation(self):
        second = self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.item.setPos(QtCore.QPointF(-8.0, 3.0))
        second.setPos(QtCore.QPointF(14.0, 11.0))
        self.viewer.canvas.select_footprints([self.item, second])
        _app.processEvents()

        self.assertTrue(self.viewer.rotate_selected(clockwise=False))
        expected = dict((item, float(item.rotation())) for item in (self.item, second))
        self.viewer.canvas._drag_rotation = dict(expected)

        # Model the bad state seen after Qt starts a native multi-item move: the
        # move path must restore the angles captured at mouse-down.
        self.item.setRotation(0.0)
        second.setRotation(0.0)
        self.viewer.canvas._preserve_drag_rotations()

        for item in (self.item, second):
            self.assertAlmostEqual(item.rotation(), expected[item], places=6)

    def test_rotate_while_drag_is_armed_keeps_start_snapshot_and_rebases_live_state(self):
        second = self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.item.setPos(QtCore.QPointF(-8.0, 3.0))
        second.setPos(QtCore.QPointF(14.0, 11.0))
        self.viewer.canvas.select_footprints([self.item, second])
        _app.processEvents()

        # Model mouse-down occurring before R.  v0.8.8 must retain these exact
        # transforms for undo while giving subsequent movement a separate
        # post-R continuation baseline.
        items = (self.item, second)
        old_positions = dict((item, QtCore.QPointF(item.pos())) for item in items)
        old_rotations = dict((item, float(item.rotation())) for item in items)
        self.viewer.canvas._drag_origin = dict(old_positions)
        self.viewer.canvas._drag_start_rotation = dict(old_rotations)
        self.viewer.canvas._drag_rotation = dict(old_rotations)
        self.viewer.canvas._last_mouse_view_pos = QtCore.QPoint(0, 0)
        history_before = len(self.viewer._history)

        self.assertTrue(self.viewer.rotate_selected(clockwise=False))
        rotated_positions = dict((item, QtCore.QPointF(item.pos())) for item in items)
        self.assertEqual(len(self.viewer._history), history_before)
        for item in items:
            # Immutable undo state is still the true mouse-down state.
            self.assertAlmostEqual(self.viewer.canvas._drag_origin[item].x(),
                                   old_positions[item].x(), places=6)
            self.assertAlmostEqual(self.viewer.canvas._drag_origin[item].y(),
                                   old_positions[item].y(), places=6)
            self.assertAlmostEqual(self.viewer.canvas._drag_start_rotation[item],
                                   old_rotations[item], places=6)
            # Live continuation state is post-R.
            self.assertAlmostEqual(self.viewer.canvas._drag_rotation[item], 270.0, places=6)
            self.assertAlmostEqual(self.viewer.canvas._drag_rebase_positions[item].x(),
                                   rotated_positions[item].x(), places=6)
            self.assertAlmostEqual(self.viewer.canvas._drag_rebase_positions[item].y(),
                                   rotated_positions[item].y(), places=6)

        # Model Qt exposing stale orientation on its next native move.
        self.item.setRotation(0.0)
        second.setRotation(0.0)
        self.viewer.canvas._preserve_drag_rotations()
        for item in items:
            self.assertAlmostEqual(item.rotation(), 270.0, places=6)

    def test_compound_move_rotate_history_undoes_to_true_mouse_down_state(self):
        second = self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.item.setPos(QtCore.QPointF(-8.0, 3.0))
        second.setPos(QtCore.QPointF(14.0, 11.0))
        items = (self.item, second)
        self.viewer.canvas.select_footprints(list(items))
        _app.processEvents()

        old_positions = dict((item, QtCore.QPointF(item.pos())) for item in items)
        old_rotations = dict((item, float(item.rotation())) for item in items)
        self.viewer.canvas._drag_origin = dict(old_positions)
        self.viewer.canvas._drag_start_rotation = dict(old_rotations)
        self.viewer.canvas._drag_rotation = dict(old_rotations)
        self.viewer.canvas._last_mouse_view_pos = QtCore.QPoint(0, 0)

        # Rotation while held is deliberately not entered into history yet.
        self.assertTrue(self.viewer.rotate_selected(clockwise=False))
        self.assertEqual(len(self.viewer._history), 0)

        # Model a following live move, then the release payload.
        for item in items:
            item.setPos(item.pos() + QtCore.QPointF(5.0, -2.0))
        changes = []
        for item in items:
            changes.append((item, old_positions[item], old_rotations[item],
                            QtCore.QPointF(item.pos()), float(item.rotation())))
        self.viewer._on_footprints_moved(changes)
        self.assertEqual(len(self.viewer._history), 1)

        self.assertTrue(self.viewer.undo())
        for item in items:
            self.assertAlmostEqual(item.pos().x(), old_positions[item].x(), places=6)
            self.assertAlmostEqual(item.pos().y(), old_positions[item].y(), places=6)
            self.assertAlmostEqual(item.rotation(), old_rotations[item], places=6)

    def test_single_rotation_does_not_move_footprint_origin(self):
        self.item.setPos(QtCore.QPointF(7.25, -4.5))
        before = QtCore.QPointF(self.item.pos())
        self.assertTrue(self.viewer.rotate_selected(clockwise=False))
        self.assertAlmostEqual(self.item.pos().x(), before.x(), places=6)
        self.assertAlmostEqual(self.item.pos().y(), before.y(), places=6)

    def test_basic_rotation_property_is_editable_and_undoable(self):
        self.viewer.prop_rotation.setValue(45.0)
        self.viewer._on_property_rotation()
        self.assertAlmostEqual(self.item.rotation(), 315.0)
        self.assertAlmostEqual(self.viewer.prop_rotation.value(), 45.0)
        self.assertTrue(self.viewer.undo())
        self.assertAlmostEqual(self.item.rotation(), 0.0)


    def test_reselecting_active_footprint_keeps_graphics_and_pad_rows_alive(self):
        # Deselecting does not change the active footprint.  Selecting it again
        # should therefore change only Properties/selection highlight, not
        # rebuild the unrelated Footprint Graphics and Pads sections.
        self.viewer.canvas.select_footprints([])
        _app.processEvents()
        layer_rows = [self.viewer.layer_layout.itemAt(i).widget()
                      for i in range(self.viewer.layer_layout.count())]
        pad_rows = [self.viewer.pad_layout.itemAt(i).widget()
                    for i in range(self.viewer.pad_layout.count())]

        self.viewer.canvas.select_footprint(self.item)
        _app.processEvents()

        self.assertEqual([self.viewer.layer_layout.itemAt(i).widget()
                          for i in range(self.viewer.layer_layout.count())], layer_rows)
        self.assertEqual([self.viewer.pad_layout.itemAt(i).widget()
                          for i in range(self.viewer.pad_layout.count())], pad_rows)

    def test_property_grid_reuses_rows_across_deselect_reselect(self):
        self.viewer._refresh_properties()
        _app.processEvents()
        first_rows = list(self.viewer.props._row_widgets)
        self.assertTrue(first_rows)

        self.viewer.canvas.select_footprints([])
        _app.processEvents()
        self.viewer.canvas.select_footprint(self.item)
        _app.processEvents()

        self.assertGreaterEqual(len(self.viewer.props._row_widgets), len(first_rows))
        self.assertEqual(self.viewer.props._row_widgets[:len(first_rows)], first_rows)

    def test_spinboxes_align_within_each_sidebar_group(self):
        # Native FreeCAD styles report different size hints for different
        # ranges/suffixes.  Each logical editor group should still expose one
        # deterministic numeric width.
        routing = [self.viewer.route_track_width, self.viewer.route_via_dia,
                   self.viewer.route_via_hole]
        self.assertEqual(len({sp.minimumWidth() for sp in routing}), 1)
        self.assertEqual(len({sp.maximumWidth() for sp in routing}), 1)

        model = list(self.viewer.model_adjust_spins.values())
        self.assertEqual(len({sp.minimumWidth() for sp in model}), 1)
        self.assertEqual(len({sp.maximumWidth() for sp in model}), 1)

        stack = [row.spin for row in self.viewer._stackup_rows]
        self.assertTrue(stack)
        self.assertEqual(len({sp.minimumWidth() for sp in stack}), 1)
        self.assertEqual(len({sp.maximumWidth() for sp in stack}), 1)

        board = [self.viewer.board_rotation] + list(self.viewer.board_offset.values())
        self.assertEqual(len({sp.minimumWidth() for sp in board}), 1)
        self.assertEqual(len({sp.maximumWidth() for sp in board}), 1)

    def test_drag_history_undoes_as_one_transform(self):
        before = QtCore.QPointF(self.item.pos())
        after = QtCore.QPointF(before.x() + 3.0, before.y() + 2.0)
        self.item.setPos(after)
        self.viewer._on_footprints_moved([(self.item, before, after)])

        self.assertTrue(self.viewer.undo())
        self.assertEqual(self.item.pos(), before)
        self.assertTrue(self.viewer.redo())
        self.assertEqual(self.item.pos(), after)

    def test_delete_multi_selection_is_one_undoable_operation(self):
        second = self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        first = self.item
        self.viewer.canvas.select_footprints([first, second])
        _app.processEvents()
        self.assertEqual(len(self.viewer.canvas.selected_footprints()), 2)

        self.assertTrue(self.viewer.delete_selected_footprints())
        self.assertEqual(len(self.viewer._entries), 0)
        self.assertEqual(len(self.viewer.canvas.footprints), 0)

        self.assertTrue(self.viewer.undo())
        self.assertEqual(len(self.viewer._entries), 2)
        self.assertEqual(len(self.viewer.canvas.selected_footprints()), 2)

        self.assertTrue(self.viewer.redo())
        self.assertEqual(len(self.viewer._entries), 0)

    def test_undo_restores_exact_position_after_grid_change(self):
        self.viewer.canvas.set_grid(step=0.1, snap=False)
        before = QtCore.QPointF(0.13, 0.27)
        after = QtCore.QPointF(2.34, 4.56)
        self.item.setPos(before)
        self.item.setPos(after)
        self.viewer._on_footprints_moved([(self.item, before, after)])
        self.viewer.canvas.set_grid(step=1.0, snap=True)

        self.assertTrue(self.viewer.undo())
        self.assertAlmostEqual(self.item.pos().x(), 0.13, places=9)
        self.assertAlmostEqual(self.item.pos().y(), 0.27, places=9)


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestHostedViewer(unittest.TestCase):
    def setUp(self):
        self.viewer = Viewer2D(hosted=True)

    def tearDown(self):
        self.viewer.close()

    def test_freecad_actions_live_in_one_header_menu(self):
        # The header used to carry five separate buttons; they now live behind
        # a single File menu so the view controls have room.
        labels = [a.text() for a in self.viewer.menu_file.actions() if a.text()]
        self.assertIn("Import footprints\u2026", labels)
        self.assertIn("Import netlist\u2026", labels)
        self.assertIn("Import board solid (STEP)\u2026", labels)
        self.assertIn("Board solid from FreeCAD\u2026", labels)
        self.assertNotIn("Create FreeCAD document", labels)
        # Preferences moved to the Edit menu (kept last) in 0.16.5.
        edit = [a.text() for a in self.viewer.menu_edit.actions() if a.text()]
        self.assertEqual(edit[-1], "Preferences\u2026")

    def test_three_d_model_actions_are_available_without_a_netlist(self):
        # A plain footprint import must still be able to reach its 3D models.
        labels = [a.text() for a in self.viewer.menu_file.actions() if a.text()]
        self.assertIn("3D models folder\u2026", labels)
        self.assertIn("Load 3D model for selection\u2026", labels)

    def test_version_is_visible_in_header(self):
        self.assertTrue(self.viewer.version_label.isVisibleTo(self.viewer))
        self.assertEqual(self.viewer.version_label.text(), "v%s" % __version__)

    def test_hosted_viewer_has_no_independent_theme_button(self):
        self.assertFalse(hasattr(self.viewer, "btn_theme"))

    def test_header_action_emits_host_request(self):
        seen = []
        self.viewer.hostActionRequested.connect(seen.append)
        self.viewer.act_import.trigger()
        self.assertEqual(seen, ["import"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
