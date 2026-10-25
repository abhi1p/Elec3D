# -*- coding: utf-8 -*-
"""Tests for the sidebar sections of the 2D viewer.

Covers the Properties panel (Basic Properties + Fields), the Design layers
stack, and what a multi-footprint selection shows.

    QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d.footprint import parse_footprint_file          # noqa: E402

try:
    from elec3d.qt import Qt, QtCore, QtWidgets, QT_BINDING
    from elec3d.panel2d import MIXED, StackRow, Viewer2D
    try:
        if QT_BINDING == "PySide6":
            from PySide6 import QtTest
        elif QT_BINDING == "PySide2":
            from PySide2 import QtTest
        else:
            from PySide import QtTest
    except ImportError:                                     # pragma: no cover
        QtTest = None
    HAVE_QT = True
except ImportError:                                         # pragma: no cover
    QtTest = None
    HAVE_QT = False

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SAMPLE = os.path.join(DATA, "NE555DR.kicad_mod")
SHAPES = os.path.join(DATA, "SHAPES_TEST.kicad_mod")

_app = None


def setUpModule():
    global _app
    if HAVE_QT:
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _pump():
    """Let queued events settle, as they would between real mouse clicks."""
    _app.processEvents()


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class PanelTestCase(unittest.TestCase):
    """Shared fixture: a shown viewer with two different footprints loaded."""

    def setUp(self):
        self.viewer = Viewer2D(theme="dark")
        self.viewer.resize(1000, 700)
        # Visibility matters: isVisible() is false for every child of a widget
        # that was never shown, which would make the row assertions vacuous.
        self.viewer.show()
        self.first = self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.second = self.viewer.add_footprint(parse_footprint_file(SHAPES))
        self.entries = self.viewer._entries

    def tearDown(self):
        self.viewer.hide()
        self.viewer.deleteLater()

    # -- helpers ---------------------------------------------------------
    def stack_rows(self):
        layout = self.viewer.stack_layout
        return [layout.itemAt(i).widget() for i in range(layout.count())
                if isinstance(layout.itemAt(i).widget(), StackRow)]

    def field_rows(self):
        grid = self.viewer.props.grid
        rows = []
        for r in range(grid.rowCount()):
            left = grid.itemAtPosition(r, 0)
            right = grid.itemAtPosition(r, 1)
            if left is not None and right is not None:
                rows.append((left.widget().text(), right.widget().text()))
        return rows

    def field(self, key):
        return dict(self.field_rows()).get(key)

    def combo_items(self):
        combo = self.viewer.prop_layer_combo
        return [combo.itemText(i) for i in range(combo.count())]

    def select_layer(self, uid):
        combo = self.viewer.prop_layer_combo
        combo.setCurrentIndex(combo.findData(uid))


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestPropertiesSection(PanelTestCase):
    def test_single_footprint_shows_kind_layer_and_fields(self):
        self.viewer.canvas.select_footprint(self.first)
        self.assertEqual(self.viewer.prop_kind.text(), "Footprint")
        self.assertTrue(self.viewer.prop_layer_row.isVisible())
        self.assertTrue(self.viewer.prop_rotation_row.isVisible())
        self.assertAlmostEqual(self.viewer.prop_rotation.value(), 0.0)
        self.assertTrue(self.viewer.grp_fields.isVisible())
        self.assertFalse(self.viewer.prop_hint.isVisible())
        self.assertEqual(self.field("footprint"), "NE555DR")
        self.assertEqual(self.field("pads"), "8")

    def test_fields_group_starts_collapsed_for_footprints(self):
        self.viewer.canvas.select_footprint(self.first)
        self.assertFalse(self.viewer.grp_fields.is_expanded())

    def test_fields_expansion_is_remembered_across_selections(self):
        self.viewer.canvas.select_footprint(self.first)
        self.viewer.grp_fields.header.setChecked(True)
        self.viewer.grp_fields._on_clicked()
        self.viewer.canvas.select_footprint(self.second)
        self.assertTrue(self.viewer.grp_fields.is_expanded())

    def test_pad_click_hides_the_design_layer_row(self):
        pad = self.entries[0]["fp"].pads[0]
        self.viewer.report_click({"kind": "pad", "pad": pad})
        self.assertEqual(self.viewer.prop_kind.text(), "Pad %s" % pad.number)
        # A pad sits on footprint copper, not on a board plane, so offering the
        # design-layer dropdown here would edit the wrong object.
        self.assertFalse(self.viewer.prop_layer_row.isVisible())

    def test_sub_object_selection_forces_the_fields_group_open(self):
        # A pad has nothing in Basic Properties, so a collapsed Fields group
        # would leave the panel showing nothing at all.
        self.assertFalse(self.viewer.grp_fields.is_expanded())
        self.viewer.report_click({"kind": "pad", "pad": self.entries[0]["fp"].pads[0]})
        self.assertTrue(self.viewer.grp_fields.is_expanded())

    def test_no_selection_state(self):
        self.viewer.clear()
        self.assertEqual(self.viewer.prop_kind.text(), "No selection")
        self.assertTrue(self.viewer.prop_hint.isVisible())
        self.assertFalse(self.viewer.prop_layer_row.isVisible())
        self.assertFalse(self.viewer.prop_rotation_row.isVisible())
        self.assertFalse(self.viewer.grp_fields.isVisible())


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestMultiSelection(PanelTestCase):
    def test_kind_counts_the_selected_objects(self):
        self.viewer.canvas.select_all_footprints()
        self.assertEqual(self.viewer.prop_kind.text(), "2 objects selected")
        self.assertEqual(self.viewer.prop_kind.toolTip(), "2 footprints")

    def test_shared_layer_is_shown_as_a_value_not_as_mixed(self):
        # Both footprints landed on the active plane, so layer is genuinely
        # common and must read as a real value.
        self.viewer.canvas.select_all_footprints()
        self.assertTrue(self.viewer.prop_layer_row.isVisible())
        self.assertNotIn(MIXED, self.combo_items())
        self.assertEqual(self.viewer.prop_layer_combo.currentText(), "B.Cu")

    def test_differing_layer_shows_a_mixed_entry(self):
        self.viewer._on_stack_add()
        self.viewer.canvas.select_footprint(self.first)
        self.select_layer(self.viewer.stack.top().id)

        self.viewer.canvas.select_all_footprints()
        # The row stays visible: every selected footprint owns a layer, they
        # just disagree about which one.
        self.assertTrue(self.viewer.prop_layer_row.isVisible())
        self.assertEqual(self.viewer.prop_layer_combo.itemText(0), MIXED)

    def test_common_field_values_survive_and_differing_ones_read_mixed(self):
        self.viewer.canvas.select_all_footprints()
        rows = dict(self.field_rows())
        # The two sample footprints differ in name, pad count and extent.
        self.assertEqual(rows["footprint"], MIXED)
        self.assertEqual(rows["pads"], MIXED)
        # ...but both sit at the same design-layer-driven position value.
        self.assertNotEqual(rows.get("position"), None)

    def test_fields_are_intersected_across_the_selection(self):
        first_keys = set(k for k, _ in self.viewer._footprint_fields(self.entries[0]))
        second_keys = set(k for k, _ in self.viewer._footprint_fields(self.entries[1]))
        self.viewer.canvas.select_all_footprints()
        shown = set(k for k, _ in self.field_rows())
        # A field only appears when every selected footprint owns it.
        self.assertEqual(shown, first_keys & second_keys)

    def test_layer_edit_applies_to_the_whole_selection(self):
        self.viewer._on_stack_add()
        top = self.viewer.stack.top().id
        self.viewer.canvas.select_all_footprints()
        self.select_layer(top)
        for entry in self.entries:
            self.assertEqual(self.viewer.design_layer_of(entry).id, top)

    def test_ctrl_a_selects_every_footprint(self):
        self.viewer.canvas.select_footprint(None)
        self.viewer.canvas.select_all_footprints()
        self.assertEqual(len(self.viewer.canvas.selected_footprints()), 2)
        self.assertEqual(self.viewer.prop_kind.text(), "2 objects selected")

    def test_active_footprint_is_kept_when_the_selection_grows(self):
        self.viewer.canvas.select_footprint(self.second)
        active = self.viewer._active
        self.viewer.canvas.select_all_footprints()
        # Extending a selection must not drag the graphics/pads sections onto
        # a different footprint.
        self.assertIs(self.viewer._active, active)


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestModelAdjustSidebar(PanelTestCase):
    def test_manual_mode_stays_within_fixed_sidebar_viewport(self):
        self.viewer.canvas.select_footprint(self.first)
        entry = self.entries[0]
        entry["model_adjust_mode"] = "manual"
        self.viewer.view_mode = "3d"
        self.viewer._update_model_adjust_section(entry)
        _pump()

        viewport_w = self.viewer.sidebar_scroll.viewport().width()
        self.assertLessEqual(self.viewer.sidebar_inner.width(), viewport_w)
        # The right-most action used to sit outside the scroll viewport with
        # horizontal scrolling disabled, so it was simply invisible.
        for widget in (self.viewer.model_adjust_mode,
                       self.viewer.model_adjust_spins["x"],
                       self.viewer.model_adjust_spins["y"],
                       self.viewer.model_adjust_spins["z"],
                       self.viewer.btn_center_xy,
                       self.viewer.btn_adjust_reset):
            pos = widget.mapTo(self.viewer.sidebar_inner, QtCore.QPoint(0, 0))
            self.assertGreaterEqual(pos.x(), 0)
            self.assertLessEqual(pos.x() + widget.width(), self.viewer.sidebar_inner.width())


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestDesignLayersSection(PanelTestCase):
    def test_rows_are_listed_top_down(self):
        self.viewer._on_stack_add()
        names = [l.name for l in self.viewer.stack.top_down()]
        self.assertEqual(names, ["F.Cu", "B.Cu"])
        self.assertEqual([r.uid for r in self.stack_rows()],
                         [l.id for l in self.viewer.stack.top_down()])

    def test_new_footprints_land_on_the_active_layer(self):
        added = self.viewer._on_stack_add() or self.viewer.stack.active_id
        item = self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.assertEqual(item.design_layer_id, added)

    def test_active_layer_is_undimmed_and_others_are_not(self):
        self.viewer._on_stack_add()
        self.viewer.canvas.select_footprint(self.first)
        self.select_layer(self.viewer.stack.top().id)
        self.assertEqual(self.first.opacity(), 1.0)
        self.assertLess(self.second.opacity(), 1.0)

    def test_copper_is_tinted_with_the_design_layer_colour(self):
        self.viewer._on_stack_add()
        self.viewer.canvas.select_footprint(self.first)
        top = self.viewer.stack.top()
        self.select_layer(top.id)
        self.assertEqual(self.first.layer_hex("F.Cu"), top.color)
        # Non-copper layers keep their own KiCad colour.
        self.assertNotEqual(self.first.layer_hex("F.SilkS"), top.color)

    def test_hiding_a_layer_hides_and_deselects_its_footprints(self):
        self.viewer.canvas.select_all_footprints()
        bottom = self.viewer.stack.bottom().id
        self.viewer._on_stack_visible(bottom, False)
        self.assertFalse(self.first.isVisible())
        self.assertFalse(self.first.isSelected())
        self.assertEqual(self.viewer.prop_kind.text(), "No selection")

    def test_removing_a_layer_moves_its_footprints_to_the_fallback(self):
        self.viewer._on_stack_add()
        top = self.viewer.stack.top().id
        self.viewer.canvas.select_all_footprints()
        self.select_layer(top)
        self.viewer._on_stack_remove(self.viewer.stack.bottom().id)
        for entry in self.entries:
            self.assertTrue(self.viewer.stack.has(entry["item"].design_layer_id))

    def test_the_last_layer_cannot_be_removed(self):
        only = self.viewer.stack.bottom().id
        self.viewer._on_stack_remove(only)
        self.assertEqual(len(self.viewer.stack), 1)
        row = next(r for r in self.stack_rows() if r.uid == only)
        self.assertFalse(row.remove.isEnabled())

    def test_row_counts_track_the_footprints_on_each_layer(self):
        self.viewer._on_stack_add()
        self.viewer.canvas.select_footprint(self.first)
        self.select_layer(self.viewer.stack.top().id)
        counts = {}
        for entry in self.entries:
            layer = self.viewer.design_layer_of(entry)
            counts[layer.id] = counts.get(layer.id, 0) + 1
        self.assertEqual(sorted(counts.values()), [1, 1])

    def test_clicking_a_row_makes_that_layer_active(self):
        self.viewer._on_stack_add()
        bottom = self.viewer.stack.bottom().id
        self.assertNotEqual(self.viewer.stack.active_id, bottom)
        self.viewer._on_stack_activate(bottom)
        self.assertEqual(self.viewer.stack.active_id, bottom)


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestStackupSection(PanelTestCase):
    def row_kinds(self):
        return [row.kind for row in self.viewer._stackup_rows]

    def row_labels(self):
        return [row.label.text() for row in self.viewer._stackup_rows]

    def test_one_copper_layer_has_copper_then_bottom_margin(self):
        # There is no top margin: the board solid decides where the board ends.
        self.assertEqual(self.row_kinds(), ["copper", "bottom_margin"])
        self.assertEqual(self.row_labels(), ["B.Cu", "bottom margin"])

    def test_add_layer_inserts_separation_and_new_top_copper(self):
        self.viewer._on_stack_add()
        self.assertEqual(self.row_kinds(),
                         ["copper", "separation", "copper", "bottom_margin"])
        self.assertEqual(self.row_labels(),
                         ["F.Cu", "separation", "B.Cu", "bottom margin"])

    def test_three_layers_have_one_separation_between_each_pair(self):
        self.viewer._on_stack_add()
        self.viewer._on_stack_add()
        self.assertEqual(self.row_kinds(),
                         ["copper", "separation", "copper",
                          "separation", "copper", "bottom_margin"])
        self.assertEqual(self.row_labels()[0::2],
                         ["F.Cu", "In1.Cu", "B.Cu"])

    def test_value_edit_updates_model_without_rebuilding_rows(self):
        copper_row = self.viewer._stackup_rows[0]
        row_id = id(copper_row)
        copper_row.spin.setValue(0.070)
        _pump()
        self.assertAlmostEqual(
            self.viewer.stack.copper_thickness(copper_row.layer_id), 0.070)
        self.assertEqual(id(self.viewer._stackup_rows[0]), row_id)

    def test_summary_tracks_top_reference_plane(self):
        bottom_margin = self.viewer._stackup_rows[-1]
        bottom_margin.spin.setValue(2.0)
        _pump()
        self.assertIn("top plane 2.000 mm", self.viewer.stackup_summary.text())


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class TestSectionCounts(PanelTestCase):
    def test_counts_use_the_parenthesised_form(self):
        self.viewer.canvas.select_footprint(self.first)
        self.assertIn("(2)", self.viewer.sec_footprints.header.text())
        self.assertIn("(8)", self.viewer.sec_pads.header.text())


@unittest.skipUnless(HAVE_QT and QtTest is not None, "no PySide QtTest available")
class TestSelectionPriority(PanelTestCase):
    """Only pads and footprints are selectable, and pads outrank footprints."""

    def setUp(self):
        PanelTestCase.setUp(self)
        self.canvas = self.viewer.canvas
        self.canvas.fit()
        _pump()

    def click(self, item, x, y, modifier=None):
        """Click at a point given in ``item``'s own coordinates."""
        if modifier is None:
            modifier = Qt.NoModifier
        pos = self.canvas.mapFromScene(item.mapToScene(QtCore.QPointF(x, y)))
        QtTest.QTest.mouseClick(self.canvas.viewport(), Qt.LeftButton,
                                modifier, pos)
        _pump()

    def click_empty(self):
        # Far outside every footprint, so nothing is under the cursor.
        pos = self.canvas.mapFromScene(QtCore.QPointF(-500.0, -500.0))
        QtTest.QTest.mouseClick(self.canvas.viewport(), Qt.LeftButton,
                                Qt.NoModifier, pos)
        _pump()

    def pad_of(self, entry, index=0):
        return entry["fp"].pads[index]

    def checked_rows(self):
        return [e["row"].isChecked() for e in self.entries]

    # -- pads win ---------------------------------------------------------
    def test_clicking_a_pad_selects_the_pad_not_the_footprint(self):
        pad = self.pad_of(self.entries[0], 2)
        self.click(self.first, pad.x, pad.y)
        self.assertEqual(self.viewer.prop_kind.text(), "Pad %s" % pad.number)
        self.assertEqual(self.first.selected_pad, str(pad.number))

    def test_a_highlighted_pad_replaces_the_footprint_outline(self):
        pad = self.pad_of(self.entries[0], 1)
        self.click(self.first, pad.x, pad.y)
        # The footprint stays selected in Qt terms so that dragging still moves
        # it, but it must not draw its own outline on top of the pad's.
        self.assertTrue(self.first.isSelected())
        self.assertIsNotNone(self.first.selected_pad)

    def test_clicking_the_footprint_body_selects_the_footprint(self):
        self.click(self.first, 0.0, 0.0)
        self.assertEqual(self.viewer.prop_kind.text(), "Footprint")
        self.assertIsNone(self.first.selected_pad)

    def test_clicking_a_text_selects_its_footprint_not_the_text(self):
        text = [t for t in self.entries[0]["fp"].texts if not t.hide][0]
        self.click(self.first, text.x, text.y)
        # Texts are reference material, not selectable objects.
        self.assertEqual(self.viewer.prop_kind.text(), "Footprint")

    def test_dragging_a_pad_moves_the_whole_footprint(self):
        pad = self.pad_of(self.entries[0], 2)
        before = QtCore.QPointF(self.first.pos())
        other_before = QtCore.QPointF(self.second.pos())
        start = self.canvas.mapFromScene(
            self.first.mapToScene(QtCore.QPointF(pad.x, pad.y)))
        end = start + QtCore.QPoint(40, 25)
        QtTest.QTest.mousePress(self.canvas.viewport(), Qt.LeftButton,
                                Qt.NoModifier, start)
        QtTest.QTest.mouseMove(self.canvas.viewport(), end)
        _pump()
        QtTest.QTest.mouseRelease(self.canvas.viewport(), Qt.LeftButton,
                                  Qt.NoModifier, end)
        _pump()
        # Pads are a finer thing to point at, not a thing you can move.
        self.assertNotEqual(self.first.pos(), before)
        self.assertEqual(self.second.pos(), other_before)

    def test_selecting_a_footprint_clears_the_pad_highlight(self):
        pad = self.pad_of(self.entries[0], 0)
        self.click(self.first, pad.x, pad.y)
        self.assertIsNotNone(self.first.selected_pad)
        self.click(self.first, 0.0, 0.0)
        self.assertIsNone(self.first.selected_pad)
        self.assertIsNone(self.canvas.selected_pad())

    def test_pads_list_row_selects_the_pad_and_narrows_the_selection(self):
        self.canvas.select_all_footprints()
        self.viewer.set_active(self.entries[1])
        pad = self.pad_of(self.entries[1], 3)
        self.viewer._on_pad_row(pad)
        self.assertEqual(self.viewer.prop_kind.text(), "Pad %s" % pad.number)
        self.assertEqual(self.second.selected_pad, str(pad.number))
        # A pad belongs to one footprint, so picking it narrows the selection.
        self.assertEqual(self.canvas.selected_footprints(), [self.second])

    # -- deselection ------------------------------------------------------
    def test_clicking_empty_canvas_deselects_everything(self):
        self.click(self.first, 0.0, 0.0)
        self.click_empty()
        self.assertEqual(self.viewer.prop_kind.text(), "No selection")
        self.assertTrue(self.viewer.prop_hint.isVisible())
        self.assertFalse(self.viewer.prop_layer_row.isVisible())
        self.assertFalse(self.viewer.grp_fields.isVisible())

    def test_deselection_clears_the_footprint_list_highlight(self):
        self.click(self.first, 0.0, 0.0)
        self.assertEqual(self.checked_rows(), [True, False])
        self.click_empty()
        # Leaving the active footprint lit would contradict the "No selection"
        # the panel just switched to.
        self.assertEqual(self.checked_rows(), [False, False])

    def test_escape_clears_a_pad_selection(self):
        pad = self.pad_of(self.entries[0], 0)
        self.click(self.first, pad.x, pad.y)
        QtTest.QTest.keyClick(self.canvas, Qt.Key_Escape)
        _pump()
        self.assertIsNone(self.canvas.selected_pad())
        self.assertEqual(self.viewer.prop_kind.text(), "No selection")

    # -- multi-selection --------------------------------------------------
    def test_every_selected_footprint_is_highlighted_in_the_list(self):
        self.click(self.first, 0.0, 0.0)
        self.click(self.second, 0.0, 0.0, Qt.ControlModifier)
        self.assertEqual(self.viewer.prop_kind.text(), "2 objects selected")
        self.assertEqual(self.checked_rows(), [True, True])

    def test_ctrl_clicking_a_pad_extends_rather_than_selecting_the_pad(self):
        self.click(self.first, 0.0, 0.0)
        pad = self.pad_of(self.entries[1], 0)
        self.click(self.second, pad.x, pad.y, Qt.ControlModifier)
        # Extending a selection must not collapse the readout to one pad.
        self.assertEqual(self.viewer.prop_kind.text(), "2 objects selected")
        self.assertIsNone(self.canvas.selected_pad())


if __name__ == "__main__":
    unittest.main()
