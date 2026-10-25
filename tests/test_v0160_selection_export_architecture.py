# -*- coding: utf-8 -*-
"""Regression guards for the v0.16 selection/export interaction work."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEW = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
EXPORT = (ROOT / "elec3d" / "export3d.py").read_text(encoding="utf-8")


class TestSelectionFilterArchitecture(unittest.TestCase):
    def test_selection_filter_covers_editor_object_classes(self):
        self.assertIn('"footprints": True, "pads": True, "tracks": True, "vias": True,', VIEW)
        self.assertNotIn('"outlines": True', VIEW)
        self.assertIn("def set_selection_filter", VIEW)
        self.assertIn("Selection filter", PANEL)
        self.assertIn('ToolRailButton("filter"', PANEL)

    def test_ctrl_click_is_explicit_multi_selection_path(self):
        self.assertIn("ctrl_extend = bool(event.modifiers() & Qt.ControlModifier)", VIEW)
        self.assertIn("def _ctrl_toggle_item", VIEW)
        self.assertIn("Ctrl+click", VIEW)

    def test_direct_via_drag_remains_connectivity_preserving(self):
        self.assertIn('session["mode"] = "via_drag"', PANEL)
        self.assertIn("_via_connected_track_points", PANEL)
        self.assertIn("drag_via_attached_points", PANEL)
        self.assertIn("Drag via — connected tracks follow", PANEL)


class TestExportParityArchitecture(unittest.TestCase):
    def test_negative_board_block_is_the_exact_board_solid(self):
        board = EXPORT.split("def _board_block", 1)[1].split("def _rgb_tuple", 1)[0]
        self.assertIn("exact_shape_for_export(solid)", board)
        self.assertNotIn("face.extrude", board)
        self.assertNotIn("_outline_loops_by_layer", EXPORT)
        self.assertIn('records.insert(0, _record(_board_block(solid), "PCB_Board"', EXPORT)

    def test_step_export_uses_hidden_temp_document_and_does_not_restore_old_doc(self):
        section = EXPORT.split("def export_step", 1)[1]
        self.assertIn("_new_hidden_temp_document", section)
        self.assertNotIn("old_doc =", section)
        self.assertNotIn("App.setActiveDocument(old_doc)", section)

    def test_hidden_document_helper_prefers_freecad_hidden_temp_flags(self):
        from elec3d import export3d

        calls = []
        class FakeApp(object):
            @staticmethod
            def newDocument(name, *args, **kwargs):
                calls.append((name, args, kwargs))
                return object()
        export3d._new_hidden_temp_document(FakeApp, "tmp")
        self.assertTrue(calls)
        self.assertTrue(calls[0][2].get("hidden"))
        self.assertTrue(calls[0][2].get("temp"))


if __name__ == "__main__":
    unittest.main()
