# -*- coding: utf-8 -*-
"""Source-level guards for the document-free modified/save-close workflow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
VIEWER = (ROOT / "elec3d" / "viewer.py").read_text(encoding="utf-8")


def test_modified_state_is_signature_based_and_save_resets_baseline():
    assert "def _persistent_content_state(self):" in PANEL
    assert "def _current_content_signature(self):" in PANEL
    assert "self._current_content_signature() != self._saved_content_signature" in PANEL
    assert "def _capture_saved_content_state(self):" in PANEL
    # Both successful project save and design load establish a clean baseline.
    assert PANEL.count("self._capture_saved_content_state()") >= 3


def test_view_only_state_is_not_part_of_dirty_signature():
    start = PANEL.index("def _persistent_content_state(self):")
    end = PANEL.index("def _current_content_signature(self):", start)
    body = PANEL[start:end]
    for view_only in ("grid_visible", "grid_step", "center", "sections", "view_mode"):
        assert ('"%s"' % view_only) not in body
    assert '"zoom"' not in body
    assert 'stack.pop("active_id", None)' in body
    assert 'layer.pop("visible", None)' in body


def test_unsaved_close_guard_offers_save_discard_cancel():
    assert "class _MainWindowCloseFilter" in VIEWER
    assert 'viewer.confirm_save_or_discard("closing FreeCAD")' in VIEWER
    assert '"StandardButton", "Save"' in PANEL
    assert '"StandardButton", "Discard"' in PANEL
    assert '"StandardButton", "Cancel"' in PANEL
    assert "event.ignore()" in VIEWER


def test_modified_title_propagates_to_mdi_tab_and_survives_rehost():
    assert 'base += " *"' in PANEL
    assert "viewer.windowTitleChanged.connect(sub.setWindowTitle)" in VIEWER
    assert '"saved_content_signature": getattr(viewer, "_saved_content_signature", None)' in VIEWER
    assert 'viewer._saved_content_signature = state.get("saved_content_signature")' in VIEWER
    assert '"modified": bool(viewer.is_modified())' in VIEWER
    assert 'if bool(state.get("modified", False)):' in VIEWER
    assert 'viewer._capture_saved_content_state()' in VIEWER
