# -*- coding: utf-8 -*-
"""Architecture guards for v0.16.5 project/menu/full-board DRC workflow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")


def test_preferences_is_last_edit_menu_command():
    rules = PANEL.index('self.act_design_rules = self.menu_edit.addAction("Design Rules…")')
    stackup = PANEL.index('self.act_stackup_dialog = self.menu_edit.addAction("Stackup…")')
    prefs = PANEL.index('self.act_options = self.menu_edit.addAction("Preferences…")')
    assert rules < stackup < prefs
    between = PANEL[stackup:prefs]
    assert 'self.menu_edit.addSeparator()' in between


def test_file_menu_has_close_project_with_unsaved_guard():
    assert 'self.act_close_project = self.menu_file.addAction("Close Project")' in PANEL
    assert 'self.act_close_project.triggered.connect(self.close_project)' in PANEL
    assert 'def close_project(self, _checked=False):' in PANEL
    assert 'self.confirm_save_or_discard("closing the current project")' in PANEL
    assert 'close_action.setEnabled(bool(self._project_file))' in PANEL
    assert 'self._capture_saved_content_state()' in PANEL


def test_tools_menu_exposes_full_board_drc_results():
    assert 'self.menu_tools = QtWidgets.QMenu(self)' in PANEL
    assert 'self.act_drc_check = self.menu_tools.addAction("DRC Check…")' in PANEL
    assert 'self.act_drc_check.triggered.connect(self.run_drc_check)' in PANEL
    assert 'def _collect_drc_violations(self):' in PANEL
    assert 'def _show_drc_results(self, issues):' in PANEL
    assert 'def run_drc_check(self, _checked=False):' in PANEL
    assert 'table.setHeaderLabels(["Rule", "Objects", "Layer / details"])' in PANEL


def test_full_drc_uses_live_rules_for_requested_clearance_families():
    for rule in (
        '"track_track"', '"track_via"', '"track_pad"', '"via_via"',
        '"via_pad"', '"pad_pad"', '"courtyard_courtyard"',
        '"outline_track"', '"outline_via"', '"outline_pad"',
        '"outline_courtyard"',
    ):
        assert rule in PANEL[PANEL.index('def _collect_drc_violations'):PANEL.index('def _show_drc_results')]
    assert 'route_conflicts(track, pads, tracks, vias, order' in PANEL
    assert 'via_conflicts(via, pads, tracks, vias, order' in PANEL
    assert 'self._paths_with_clearance_intersect(' in PANEL
