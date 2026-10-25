# -*- coding: utf-8 -*-
"""Regression guards for v0.16.3 logical track-segment editing."""
from pathlib import Path

from elec3d.routing import Track, delete_track_segment, normalize_track_points

ROOT = Path(__file__).resolve().parents[1]


def test_collinear_same_direction_vertices_are_one_logical_segment():
    track = Track(uid="t", layer_id=1, width=0.25,
                  points=[(0, 0), (5, 0), (10, 0), (15, 0)], net="N1")
    assert track.points == [(0.0, 0.0), (15.0, 0.0)]


def test_collinear_reversal_remains_a_real_turn():
    assert normalize_track_points([(0, 0), (10, 0), (5, 0)]) == [
        (0.0, 0.0), (10.0, 0.0), (5.0, 0.0)]


def test_delete_middle_segment_splits_track_and_preserves_attributes():
    track = Track(uid="t", layer_id=7, width=0.4,
                  points=[(0, 0), (5, 0), (10, 5), (15, 5)], net="SIG")
    parts = delete_track_segment(track, 1)
    assert len(parts) == 2
    assert parts[0].uid == "t"
    assert parts[0].points == [(0.0, 0.0), (5.0, 0.0)]
    assert parts[1].uid != "t"
    assert parts[1].points == [(10.0, 5.0), (15.0, 5.0)]
    assert all(p.layer_id == 7 and p.width == 0.4 and p.net == "SIG" for p in parts)


def test_delete_end_segment_keeps_remaining_track_uid():
    track = Track(uid="t", layer_id=1,
                  points=[(0, 0), (5, 0), (10, 5)], net="N")
    parts = delete_track_segment(track, 0)
    assert len(parts) == 1
    assert parts[0].uid == "t"
    assert parts[0].points == [(5.0, 0.0), (10.0, 5.0)]


def test_delete_only_segment_removes_track_entirely():
    track = Track(uid="t", layer_id=1, points=[(0, 0), (5, 0)])
    assert delete_track_segment(track, 0) == []


def test_panel_delete_distinguishes_segment_from_u_whole_track_selection():
    panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    delete_block = panel.split("def _delete_selected_copper", 1)[1].split(
        "# -- design layers", 1)[0]
    assert "selected_segment_index" in delete_block
    assert "select_whole_track" in delete_block
    assert "delete_track_segment(" in delete_block
    assert 'label = "Delete track segment"' in delete_block
