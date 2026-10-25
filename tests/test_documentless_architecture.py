# -*- coding: utf-8 -*-
"""Architecture guards for the 0.4 document-free viewer path."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TestDocumentlessImportPath(unittest.TestCase):
    def test_gui_import_command_does_not_create_document(self):
        text = (ROOT / "elec3d" / "commands.py").read_text(encoding="utf-8")
        self.assertNotIn("newDocument(", text)
        self.assertNotIn("import_footprint(", text)

    def test_file_handler_does_not_create_document(self):
        text = (ROOT / "elec3d_import.py").read_text(encoding="utf-8")
        self.assertNotIn("newDocument(", text)
        self.assertNotIn("build_footprint(", text)
        self.assertNotIn("import_footprint(", text)

    def test_workbench_activation_only_opens_custom_viewer(self):
        text = (ROOT / "elec3d" / "workbench.py").read_text(encoding="utf-8")
        self.assertIn("viewer2d.show_viewer()", text)
        self.assertNotIn("load_document(", text)
        self.assertNotIn("import activate_interaction", text)

    def test_document_creation_is_explicit_export_only(self):
        text = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        exporter = (ROOT / "elec3d" / "export3d.py").read_text(encoding="utf-8")
        # Normal import/workbench activation stays document-free.  A document is
        # created only after the user explicitly chooses the export workflow.
        self.assertIn("def create_freecad_document", text)
        self.assertIn('addAction("Export to FreeCAD\u2026")', panel)
        self.assertIn('addAction("Export STEP\u2026")', panel)
        self.assertIn("App.newDocument", exporter)
        self.assertNotIn("App.newDocument", panel)

    def test_host_mode_preferences_are_applied_live(self):
        commands = (ROOT / "elec3d" / "commands.py").read_text(encoding="utf-8")
        viewer = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")
        self.assertIn("viewer2d.request_apply_host_preferences()", commands)
        self.assertIn("def request_apply_host_preferences", viewer)
        self.assertIn("def apply_host_preferences", viewer)
        self.assertIn("_discard_current_host_for_rebuild()", viewer)
        self.assertIn("_host_viewer(target)", viewer)

    def test_no_independent_theme_preference_in_options(self):
        commands = (ROOT / "elec3d" / "commands.py").read_text(encoding="utf-8")
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        init = (ROOT / "elec3d" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("Colour palette", commands)
        self.assertNotIn('SetString("Theme"', commands)
        self.assertNotIn("btn_theme", panel)
        self.assertNotIn('GetString("Theme"', init)


if __name__ == "__main__":
    unittest.main(verbosity=2)
