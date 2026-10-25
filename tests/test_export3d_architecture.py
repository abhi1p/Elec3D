# -*- coding: utf-8 -*-
"""Source/pure-Python guards for the explicit STEP/FreeCAD export workflow."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
EXPORT = (ROOT / "elec3d" / "export3d.py").read_text(encoding="utf-8")


class TestExport3DArchitecture(unittest.TestCase):
    def test_file_menu_exposes_both_export_destinations(self):
        self.assertIn('addAction("Export STEP…")', PANEL)
        self.assertIn('addAction("Export to FreeCAD…")', PANEL)
        self.assertIn("def export_step_dialog", PANEL)
        self.assertIn("def export_freecad_dialog", PANEL)

    def test_dialog_matches_reference_export_choices(self):
        for key in ("tracks", "pads", "models", "negative", "fuse", "no_pcurves"):
            self.assertIn('"%s"' % key, PANEL)
        self.assertIn("Export as negative (cavity)", PANEL)
        self.assertIn("Fuse touching copper", PANEL)
        self.assertIn("Omit P-curves", PANEL)

    def test_freecad_document_existence_probe_handles_host_exception(self):
        from elec3d import export3d

        class FakeApp(object):
            @staticmethod
            def listDocuments():
                return {"Existing": object()}

            @staticmethod
            def getDocument(name):
                if name == "Existing":
                    return object()
                raise RuntimeError("Unknown document %r" % name)

        self.assertTrue(export3d._document_exists(FakeApp, "Existing"))
        self.assertFalse(export3d._document_exists(FakeApp, "board_Export"))

        class FallbackApp(object):
            @staticmethod
            def listDocuments():
                raise RuntimeError("not available")

            @staticmethod
            def getDocument(name):
                if name == "Existing":
                    return object()
                raise RuntimeError("Unknown document %r" % name)

        self.assertTrue(export3d._document_exists(FallbackApp, "Existing"))
        self.assertFalse(export3d._document_exists(FallbackApp, "board_Export"))

    def test_direct_freecad_name_probe_does_not_call_getdocument_as_boolean(self):
        section = EXPORT.split("def create_freecad_document", 1)[1].split("def export_step", 1)[0]
        self.assertIn("while _document_exists(App, name):", section)
        self.assertNotIn("while App.getDocument(name) is not None", section)

    def test_direct_freecad_export_does_not_round_trip_through_step(self):
        section = EXPORT.split("def create_freecad_document", 1)[1].split("def export_step", 1)[0]
        self.assertIn("_populate_document", section)
        self.assertNotIn("Import.export", section)
        self.assertNotIn("exportStep", section)


    def test_direct_freecad_export_can_update_linked_document(self):
        section = EXPORT.split("def create_freecad_document", 1)[1].split("def export_step", 1)[0]
        self.assertIn("target_document", section)
        self.assertIn("update_existing", section)
        self.assertIn("_clear_export_contents", section)
        self.assertIn('"updated": bool(updated)', section)
        self.assertIn("Update existing FreeCAD document", PANEL)
        self.assertIn("_freecad_export_document", PANEL)

    def test_step_export_uses_freecad_coloured_step_exporter_and_restores_pcurve_pref(self):
        section = EXPORT.split("def export_step", 1)[1]
        self.assertIn("Import.export(objects, path)", section)
        self.assertIn('WriteSurfaceCurveMode', section)
        self.assertIn("old_pcurve", section)

    def test_geometry_pipeline_covers_tracks_vias_pads_models_and_outline_cavity(self):
        for token in ("_track_shape", "_via_shape", "_pad_shape", "_model_shape", "_board_block"):
            self.assertIn("def %s" % token, EXPORT)
        self.assertIn("model_adjust", EXPORT)
        self.assertIn("hole_diameter", EXPORT)
        # Pad local coordinates must match the 2D/3D world's Y-up conversion.
        pad_section = EXPORT.split("def _pad_shape", 1)[1].split("def _scaled_shape", 1)[0]
        self.assertIn("shape.mirror", pad_section)

    def test_direct_freecad_dialog_does_not_erase_step_pcurve_preference(self):
        self.assertIn('if key == "no_pcurves" and self.destination != "step"', PANEL)

    def test_default_names_follow_reference_semantics(self):
        from elec3d import export3d
        self.assertEqual(export3d.export_file_name({"tracks": True, "pads": True, "models": True}),
                         "assembly.step")
        self.assertEqual(export3d.export_file_name({"tracks": True, "pads": True, "models": False}),
                         "copper.step")
        self.assertEqual(export3d.export_file_name({"tracks": False, "pads": False, "models": True}),
                         "components.step")
        self.assertEqual(export3d.export_file_name({"negative": True}), "cavity.step")


if __name__ == "__main__":
    unittest.main(verbosity=2)
