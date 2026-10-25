# -*- coding: utf-8 -*-
"""Source-level guards for the three-entry recent-project menu."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")


def test_file_menu_has_recent_project_submenu_and_persistent_pref():
    assert 'self.menu_recent_projects = self.menu_file.addMenu("Open Recent")' in PANEL
    assert 'params.SetString("RecentProjects"' in PANEL
    assert 'params.GetString("RecentProjects"' in PANEL
    assert 'if len(clean) >= 3:' in PANEL


def test_recent_projects_are_promoted_on_open_and_project_save():
    assert 'self._remember_recent_project(self._project_file)' in PANEL
    assert 'self._remember_recent_project(path)' in PANEL
    assert 'def _open_recent_project(self, path):' in PANEL
    assert 'self.open_design_path(path, prompt_for_project=False, check_unsaved=True)' in PANEL


def test_missing_recent_projects_are_pruned():
    assert 'not os.path.isfile(path)' in PANEL
    assert 'self._forget_recent_project(path)' in PANEL
    assert 'No recent projects' in PANEL
