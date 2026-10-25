# -*- coding: utf-8 -*-
"""Regression guards for v0.16.4 route welding after segment deletion."""
from pathlib import Path

from elec3d.routing import Track, join_track_endpoints

ROOT = Path(__file__).resolve().parents[1]


def test_bridge_welds_deleted_gap_back_into_one_track():
    left = Track(uid="left", layer_id=1, width=0.25,
                 points=[(0, 0), (5, 0)], net="N1")
    bridge = Track(uid="bridge", layer_id=1, width=0.25,
                   points=[(5, 0), (10, 0)], net="N1")
    right = Track(uid="right", layer_id=1, width=0.25,
                  points=[(10, 0), (12, 2)], net="N1")

    merged = join_track_endpoints(left, bridge)
    assert merged is not None
    assert merged.uid == "left"
    # Collinear left + bridge are normalized into one straight segment.
    assert merged.points == [(0.0, 0.0), (10.0, 0.0)]

    merged = join_track_endpoints(merged, right)
    assert merged is not None
    assert merged.uid == "left"
    assert merged.points == [(0.0, 0.0), (10.0, 0.0), (12.0, 2.0)]


def test_endpoint_weld_handles_reversed_orientation():
    a = Track(uid="a", layer_id=2, width=0.3,
              points=[(5, 0), (0, 0)], net="N")
    b = Track(uid="b", layer_id=2, width=0.3,
              points=[(5, 0), (10, 0)], net="N")
    merged = join_track_endpoints(a, b)
    assert merged is not None
    assert merged.uid == "a"
    assert merged.points == [(0.0, 0.0), (10.0, 0.0)]


def test_t_junction_into_track_interior_is_not_folded_into_polyline():
    trunk = Track(uid="trunk", layer_id=1, width=0.25,
                  points=[(0, 0), (10, 0)], net="N")
    branch = Track(uid="branch", layer_id=1, width=0.25,
                   points=[(5, 0), (5, 5)], net="N")
    assert join_track_endpoints(trunk, branch) is None


def test_width_or_net_change_stays_as_separate_track_object():
    a = Track(uid="a", layer_id=1, width=0.25,
              points=[(0, 0), (5, 0)], net="N1")
    width_change = Track(uid="b", layer_id=1, width=0.5,
                         points=[(5, 0), (10, 0)], net="N1")
    other_net = Track(uid="c", layer_id=1, width=0.25,
                      points=[(5, 0), (10, 0)], net="N2")
    assert join_track_endpoints(a, width_change) is None
    assert join_track_endpoints(a, other_net) is None


def test_finish_route_uses_endpoint_welding():
    panel = (ROOT / "elec3d" / "panel2d.py").read_text(encoding="utf-8")
    assert '"start_track_uid": self._route_snap_endpoint_track_uid(snap)' in panel
    finish = panel.split("def _finish_route", 1)[1].split("def _place_via_and_continue", 1)[0]
    assert "self._append_finished_track(" in finish
    assert "self._route_snap_endpoint_track_uid(r.get(\"snap\"))" in finish
