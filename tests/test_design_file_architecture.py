# -*- coding: utf-8 -*-
"""Source-level checks for project/design integration that do not require Qt."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
VIEWER = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")
INIT = (ROOT / "Init.py").read_text(encoding="utf-8")
IMPORTER = (ROOT / "elec3d_import.py").read_text(encoding="utf-8")


def test_file_menu_has_project_open_save_and_save_as_actions():
    assert 'addAction("Create a new project\\u2026")' in PANEL
    assert 'addAction("Open\\u2026")' in PANEL
    assert 'addAction("Save")' in PANEL
    assert 'addAction("Save As\\u2026")' in PANEL
    assert 'self.act_save_design.setShortcut(enum(QtGui.QKeySequence, "StandardKey", "Save"))' in PANEL
    assert 'self.act_open_design.setShortcut(enum(QtGui.QKeySequence, "StandardKey", "Open"))' in PANEL


def test_design_capture_includes_complete_footprint_layout_and_model_corrections():
    assert '"stack": self.stack.snapshot()' in PANEL
    assert '"routing": self.routing_snapshot()' in PANEL
    assert '"board_solid": self._board_solid_snapshot(variables, path)' in PANEL
    assert '"source_text": source_text' in PANEL
    assert '"source_original": str(source_original)' in PANEL
    assert '"source_sha256": source_hash' in PANEL
    assert '"model_adjust": dict(entry.get("model_adjust")' in PANEL
    assert '"model_adjust_mode": entry.get("model_adjust_mode", "auto")' in PANEL


def test_project_save_creates_standard_project_files_and_asset_folders():
    assert 'projectfile.PROJECT_EXTENSION' in PANEL
    assert 'projectfile.FOOTPRINT_DIR_NAME' in PANEL
    assert 'projectfile.MODEL_DIR_NAME' in PANEL
    assert 'designfile.write_file(paths["board_file"], data)' in PANEL
    assert 'projectfile.write_project(' in PANEL
    assert 'projectfile.atomic_write_bytes' in PANEL


def test_open_uses_embedded_footprint_and_checks_project_and_library_copies():
    assert 'parse_footprint(source_text' in PANEL
    assert 'Project footprint mismatch for %s' in PANEL
    assert 'Footprint mismatch for %s' in PANEL
    assert 'Footprint library file not found for %s' in PANEL
    assert 'using the saved board footprint' in PANEL


def test_freecad_file_open_registers_project_and_board_extensions():
    assert 'Elec3D project (*.e3d_pro)' in INIT
    assert 'Elec3D board (*.e3d_pcb)' in INIT
    assert 'endswith((".e3d_pro", ".e3d_pcb"))' in IMPORTER
    assert 'viewer.open_design_path(filename, prompt_for_project=True)' in IMPORTER


def test_project_identity_survives_tab_window_host_rebuild():
    assert '"project_file": getattr(viewer, "_project_file", "")' in VIEWER
    assert '"design_file": getattr(viewer, "_design_file", "")' in VIEWER
    assert 'viewer._project_file = state.get("project_file", "") or ""' in VIEWER
    assert 'viewer._design_file = state.get("design_file", "") or ""' in VIEWER


def test_design_save_does_not_clear_an_active_drag_into_untracked_geometry():
    assert 'if self.canvas.has_active_interaction():' in PANEL
    assert 'Finish the current mouse operation before saving the design.' in PANEL
    snapshot = PANEL.split('def _design_snapshot', 1)[1].split('def _project_asset_name', 1)[0]
    assert 'cancel_interaction()' not in snapshot


def test_clear_design_is_available_and_resets_complete_board_state():
    assert 'addAction("Clear design")' in PANEL
    assert 'def clear_design(self, _checked=False, confirm=True):' in PANEL
    assert 'self.stack = LayerStack()' in PANEL
    assert 'self.models_folder = ""' in PANEL
    assert 'self.track_width = 0.25' in PANEL
    assert 'self.route_tool = "select"' in PANEL
    assert 'self.canvas.clear_board_sections()' in PANEL


def test_netlist_replacement_detaches_previous_project_and_board_identity():
    apply = PANEL.split('def apply_netlist(self, netlist):', 1)[1].split(
        'def _flow_place_netlist_entries', 1)[0]
    assert 'self._reset_design_identity()' in apply
    assert 'self._project_file = ""' in PANEL
    assert 'self.setWindowTitle("Elec3D")' in PANEL


def test_standalone_board_open_prompts_for_project_creation():
    assert 'This .e3d_pcb board is not attached to a project.' in PANEL
    assert 'Create project for board' in PANEL


def test_project_save_avoids_windows_model_self_copy_and_preserves_footprint_case():
    assert 'projectfile.atomic_copy_file(source, target)' in PANEL
    assert 'projectfile.footprint_asset_filename(' in PANEL
    assert 'This legacy board has no embedded footprint snapshot' in PANEL


def test_project_open_does_not_treat_missing_original_library_as_missing_project_footprint():
    loader = PANEL.split('def _load_footprint_for_design', 1)[1].split(
        'def load_design_from', 1)[0]
    # Project-local Footprints/ is sufficient for a portable project.  Missing
    # source-computer/global KiCad libraries are only warned for standalone
    # boards; an existing external library is still fingerprint-compared above.
    missing_branch = loader.split('missing_ref = original_ref', 1)[0]
    assert 'if not project_mode:' in missing_branch[-1600:]
    assert 'Project footprint file not found for %s' in loader
    assert 'Footprint mismatch for %s: current library file differs' in loader
