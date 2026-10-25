# -*- coding: utf-8 -*-
"""Regression guards for v0.14.0 editor interaction changes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_preferences_moved_to_edit_menu_and_renamed():
    panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    file_block = panel.split('self.menu_file = QtWidgets.QMenu(self)', 1)[1].split(
        'self.menu_edit = QtWidgets.QMenu(self)', 1)[0]
    edit_block = panel.split('self.menu_edit = QtWidgets.QMenu(self)', 1)[1].split(
        '# Use a normal push button', 1)[0]
    assert 'Options…' not in file_block
    assert 'Preferences…' in edit_block
    assert 'hostActionRequested.emit("options")' in edit_block


def test_m_move_is_explicit_for_copper_and_direct_drag_is_footprint_only():
    view = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
    panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    track = view.split("class TrackGraphicsItem", 1)[1].split("class ViaGraphicsItem", 1)[0]
    via = view.split("class ViaGraphicsItem", 1)[1].split(
        "# ---------------------------------------------------------------------------\n# scene items", 1)[0]
    footprint = view.split("class FootprintItem", 1)[1].split(
        "# ---------------------------------------------------------------------------\n# the view", 1)[0]
    assert "ItemIsMovable" not in track
    assert "ItemIsMovable" not in via
    assert "ItemIsMovable" in footprint
    assert "movePointerMoved = Signal(object, object)" in view
    assert "moveClicked = Signal(object, object)" in view
    assert "set_interactive_move_enabled" in view
    assert "def begin_move_selected" in panel
    assert "def cancel_move_selected" in panel


def test_via_move_drags_connected_track_vertices_and_track_move_uses_segment_drag():
    panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    routing = (ROOT / "elec3d" / "routing.py").read_text(encoding="utf-8")
    assert "def _via_connected_track_points" in panel
    assert 'session.get("via_links", {})' in panel
    assert "def drag_track_segment" in routing
    assert "anchor_start" in routing
    assert "anchor_end" in routing
    assert "drag_track_segment(" in panel


def test_layer_hotkeys_are_editor_shortcuts():
    panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    view = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
    assert "Qt.Key_Plus" in panel
    assert "Qt.Key_Minus" in panel
    assert 'getattr(Qt, "Key_PageUp", None)' in panel
    assert 'getattr(Qt, "Key_PageDown", None)' in panel
    assert "def _cycle_active_layer" in panel
    assert "def _select_extreme_layer" in panel
    # +/- no longer zoom the canvas because those keys belong to layer switching.
    key_body = view.split("def keyPressEvent(self, event):", 1)[1].split("def _info_at", 1)[0]
    assert "set_zoom(self.px_per_mm() * 1.25)" not in key_body
    assert "set_zoom(self.px_per_mm() / 1.25)" not in key_body
