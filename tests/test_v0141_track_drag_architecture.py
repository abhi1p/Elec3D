# -*- coding: utf-8 -*-
"""Regression guards for v0.14.1 copper move/drag semantics."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_track_selection_is_segment_aware_and_u_expands_connected_route():
    view = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
    panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    assert "selected_segment_index" in view
    assert "def segment_index_at" in view
    assert "def selected_path" in view
    assert "def select_complete_track" in panel
    assert "build_copper_topology" in panel
    assert "Qt.Key_U" in panel


def test_direct_mouse_drag_is_connectivity_preserving_but_m_is_translation():
    view = (ROOT / "elec3d" / "view2d.py").read_text(encoding="utf-8")
    panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    assert "copperDragStarted = Signal" in view
    assert "copperDragPointerMoved = Signal" in view
    assert "copperDragFinished = Signal" in view
    assert "def _begin_copper_drag" in panel
    assert "drag_track_segment(" in panel
    assert 'session.get("via_links", {})' in panel

    begin_m = panel.split("def begin_move_selected", 1)[1].split(
        "def _apply_move_session_target", 1)[0]
    apply_m = panel.split("def _apply_move_session_target", 1)[1].split(
        "def _on_move_pointer", 1)[0]
    assert '"mode": "translate"' in begin_m
    assert "via_links" not in begin_m
    assert "drag_track_segment(" not in apply_m
    assert 'track.points = [self._point_delta' in apply_m
    assert 'via.x = float(base.get("x", via.x)) + dx' in apply_m
