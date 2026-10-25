# -*- coding: utf-8 -*-
"""Non-GUI guards for FreeCAD MDI host/layout regressions."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TestMdiHostArchitecture(unittest.TestCase):
    def test_tab_removal_restores_previous_freecad_document_state(self):
        src = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")
        self.assertIn("def _remember_mdi_return_target", src)
        self.assertIn("def _restore_mdi_return_target", src)
        self.assertIn("_mdi_return_was_maximized", src)
        self.assertIn("target.showMaximized()", src)
        self.assertIn("_restore_mdi_return_target(area, removed_was_maximized)", src)

    def test_viewer_and_3d_toolbar_are_allowed_to_shrink_inside_mdi(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        view3d = (ROOT / "elec3d" / "view3d.py").read_text(encoding="utf-8")
        self.assertIn("def minimumSizeHint(self):", panel)
        self.assertIn("self.view_stack.setMinimumSize(0, 0)", panel)
        self.assertIn("self.view_stack.setSizePolicy(SIZE_IGNORED, SIZE_EXPANDING)", panel)
        self.assertIn("btn.setSizePolicy(SIZE_IGNORED, SIZE_FIXED)", view3d)
        self.assertIn("bar.addWidget(btn, 1)", view3d)
        self.assertIn("compact =", view3d)

    def test_header_action_buttons_keep_readable_size_hints(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        self.assertIn("header.setSizePolicy(SIZE_EXPANDING, SIZE_PREFERRED)", panel)
        self.assertIn("button.setSizePolicy(SIZE_PREFERRED, SIZE_FIXED)", panel)
        self.assertIn("self.btn_file.setSizePolicy(SIZE_PREFERRED, SIZE_FIXED)", panel)
        self.assertNotIn("self.btn_file.setSizePolicy(SIZE_IGNORED, SIZE_FIXED)", panel)

    def test_selected_title_cannot_collapse_and_file_uses_one_clean_caret(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        theme = (ROOT / "elec3d" / "theme.py").read_text(encoding="utf-8")
        self.assertIn("self.title.setMinimumWidth(96)", panel)
        self.assertIn("self.title.setSizePolicy(SIZE_PREFERRED, SIZE_PREFERRED)", panel)
        self.assertIn('QtWidgets.QPushButton("File  \\u25be")', panel)
        self.assertNotIn("QtWidgets.QToolButton.InstantPopup", panel)
        self.assertNotIn("self.btn_file.setMenu(self.menu_file)", panel)
        self.assertIn("button.height() + 4", panel)
        self.assertIn("QMenu::item", theme)
        self.assertIn("padding: 7px 30px 7px 12px", theme)


    def test_workbench_switches_never_close_or_reshow_the_editor(self):
        workbench = (ROOT / "elec3d" / "workbench.py").read_text(encoding="utf-8")
        viewer = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")
        self.assertIn("viewer2d.on_workbench_deactivated()", workbench)
        self.assertNotIn("viewer2d.hide_viewer()", workbench)
        body = viewer.split("def on_workbench_deactivated", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("restore_panels()", body)
        for forbidden in ("hide_viewer", ".show()", "_destroy_viewer"):
            self.assertNotIn(forbidden, body)
        self.assertIn("viewer.setParent(None, Qt.Window)", viewer)

    def test_closing_the_tab_or_window_ends_the_session(self):
        viewer = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        self.assertIn('confirm_save_or_discard("closing Elec3D")', viewer)
        self.assertIn("viewer.shutdown()", viewer)
        self.assertIn("_viewer.close_handler = _on_window_close_requested", viewer)
        self.assertIn("self._set_app_filter(False)", panel)
        self.assertIn("app.removeEventFilter(self)", panel)

    def test_options_only_exposes_host_mode_and_fixed_behaviours_are_not_preferences(self):
        commands = (ROOT / "elec3d" / "commands.py").read_text(encoding="utf-8")
        viewer = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")
        workbench = (ROOT / "elec3d" / "workbench.py").read_text(encoding="utf-8")
        self.assertIn('form.addRow("Viewer opens as", mode)', commands)
        self.assertNotIn('form.addRow("Open viewer with workbench"', commands)
        self.assertNotIn('form.addRow("Hide side panels for viewer tab"', commands)
        self.assertNotIn('GetBool("AutoOpen2DView"', workbench)
        self.assertNotIn('GetBool("HidePanels"', viewer)

    def test_file_menu_is_netlist_first(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        netlist = panel.index('self.menu_file.addAction("Import netlist\\u2026")')
        footprints = panel.index('self.menu_file.addAction("Import footprints\\u2026")')
        self.assertLess(netlist, footprints)

    def test_live_ratsnest_hook_is_connected(self):
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        view2d = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
        self.assertIn("footprintsMoving = Signal(object)", view2d)
        self.assertIn("self.footprintsMoving.emit(moving)", view2d)
        self.assertIn("self.canvas.footprintsMoving.connect(self._on_footprints_moving)", panel)
        self.assertIn("ratsnest_segments(", panel)





class TestFreeCADExportTabPersistence(unittest.TestCase):
    def test_export_keeps_addon_as_separate_mdi_tab(self):
        viewer = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")
        panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
        self.assertIn("def keep_viewer_tab_available", viewer)
        self.assertIn("area.subWindowList()", viewer)
        self.assertIn("viewerhost.keep_viewer_tab_available()", panel)

if __name__ == "__main__":
    unittest.main()
