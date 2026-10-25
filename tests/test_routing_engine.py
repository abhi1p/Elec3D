# -*- coding: utf-8 -*-
"""Engine tests for v0.15 routing: broad-phase indexing, 45-degree dragging,
walkaround and the cached board outline."""

import math

from elec3d.routing import (
    Track, Via, ObstacleIndex, SpatialGrid, board_outline,
    build_copper_topology, ratsnest_segments, route_conflicts, via_conflicts,
    route_snap, route45, drag_track_segment, drag_track_corner,
    drag_via_attached_points, snap_direction_45, walkaround_route,
    conflict_boxes, clip_polyline_before_conflict, track_bbox, via_bbox,
    pad_bbox, bbox_overlap,
)


def pad(x, y, pin, net="N", layer=1, w=1.0, h=1.0, shape="rect"):
    return {"x": x, "y": y, "pin": str(pin), "ref": "U1", "net": net,
            "layer_id": layer, "w": w, "h": h, "rot": 0.0, "shape": shape}


def is_45(points):
    for i in range(1, len(points)):
        dx = abs(points[i][0] - points[i - 1][0])
        dy = abs(points[i][1] - points[i - 1][1])
        if dx <= 1e-9 and dy <= 1e-9:
            continue
        if dx > 1e-9 and dy > 1e-9 and abs(dx - dy) > 1e-6:
            return False
    return True


# --- broad-phase indexing -------------------------------------------------

def test_spatial_grid_returns_only_overlapping_payloads():
    grid = SpatialGrid([((0, 0, 1, 1), "a"), ((10, 10, 11, 11), "b"),
                        ((0.5, 0.5, 2, 2), "c")])
    near = set(grid.query((0, 0, 1, 1)))
    assert "a" in near and "c" in near and "b" not in near


def test_spatial_grid_still_finds_oversized_objects():
    # A board-spanning object cannot be bucketed, but must never be lost.
    huge = (-1e5, -1e5, 1e5, 1e5)
    grid = SpatialGrid([(huge, "huge"), ((0, 0, 1, 1), "small")])
    assert set(grid.query((0, 0, 0.5, 0.5))) == {"huge", "small"}


def test_topology_matches_bruteforce_on_a_chain_of_touching_copper():
    order = [1, 2]
    tracks = [Track(layer_id=1, width=0.25, points=[(i, 0), (i + 1, 0)])
              for i in range(12)]
    via = Via(x=12.0, y=0.0, diameter=0.8, start_layer_id=1, end_layer_id=2)
    topo = build_copper_topology(tracks, [via], order)
    roots = {topo["dsu"].find(k) for k, _kind, _obj in topo["items"]}
    # Every segment abuts the next, so the whole chain is one island.
    assert len(roots) == 1


def test_topology_keeps_separated_copper_in_distinct_islands():
    order = [1]
    tracks = [Track(layer_id=1, width=0.25, points=[(0, 0), (1, 0)]),
              Track(layer_id=1, width=0.25, points=[(50, 50), (51, 50)])]
    topo = build_copper_topology(tracks, [], order)
    roots = {topo["dsu"].find(k) for k, _kind, _obj in topo["items"]}
    assert len(roots) == 2


def test_indexed_route_conflicts_agree_with_unindexed():
    order = [1]
    tracks = [Track(layer_id=1, width=0.25, points=[(5, -5), (5, 5)], net="B"),
              Track(layer_id=1, width=0.25, points=[(90, 90), (95, 95)], net="C")]
    pads = [pad(20, 0, 1, net="C")]
    vias = [Via(x=40, y=0, diameter=0.8, start_layer_id=1, end_layer_id=1, net="D")]
    cand = Track(layer_id=1, width=0.25, points=[(0, 0), (60, 0)], net="A")

    plain = route_conflicts(cand, pads, tracks, vias, order, active_net="A")
    index = ObstacleIndex(pads, tracks, vias, order)
    indexed = route_conflicts(cand, None, None, None, order, active_net="A",
                              index=index)
    assert {h["kind"] for h in plain} == {h["kind"] for h in indexed}
    assert len(plain) == len(indexed) == 3


def test_indexed_via_conflicts_agree_with_unindexed():
    order = [1, 2]
    tracks = [Track(layer_id=1, width=0.25, points=[(0, 0), (10, 0)], net="B")]
    pads = [pad(30, 0, 1, net="C")]
    vias = [Via(x=5.2, y=0, diameter=0.8, start_layer_id=1, end_layer_id=2, net="D")]
    cand = Via(x=5.0, y=0.0, diameter=0.8, start_layer_id=1, end_layer_id=2, net="A")
    plain = via_conflicts(cand, pads, tracks, vias, order, active_net="A")
    index = ObstacleIndex(pads, tracks, vias, order)
    indexed = via_conflicts(cand, None, None, None, order, active_net="A", index=index)
    assert {h["kind"] for h in plain} == {h["kind"] for h in indexed}


def test_indexed_route_snap_agrees_with_unindexed():
    order = [1]
    tracks = [Track(layer_id=1, width=0.25, points=[(0, 0), (10, 0)], net="N")]
    pads = [pad(2.0, 3.0, 1)]
    vias = [Via(x=8, y=0, diameter=0.8, start_layer_id=1, end_layer_id=1, net="N")]
    index = ObstacleIndex(pads, tracks, vias, order)
    for world in ((2.05, 3.02), (4.25, 0.05), (8.1, 0.0), (40.0, 40.0)):
        a = route_snap(world, 1, pads, tracks, vias, order, 0.5, grid=0.1)
        b = route_snap(world, 1, pads, tracks, vias, order, 0.5, grid=0.1,
                       index=index)
        assert a["kind"] == b["kind"]
        assert abs(a["x"] - b["x"]) < 1e-9 and abs(a["y"] - b["y"]) < 1e-9


def test_bbox_helpers_include_copper_envelope():
    t = Track(layer_id=1, width=1.0, points=[(0, 0), (10, 0)])
    assert track_bbox(t) == (-0.5, -0.5, 10.5, 0.5)
    v = Via(x=0, y=0, diameter=2.0)
    assert via_bbox(v) == (-1.0, -1.0, 1.0, 1.0)
    assert bbox_overlap(track_bbox(t), pad_bbox(pad(10.2, 0, 1)))


# --- 45-degree dragging ---------------------------------------------------

def test_default_segment_drag_behaviour_is_unchanged():
    pts = [(0, 0), (10, 0)]
    assert drag_track_segment(pts, 0, 0, 2, True, True) == [
        (0.0, 0.0), (0.0, 2.0), (10.0, 2.0), (10.0, 0.0)]
    pts = [(0, 0), (5, 0), (10, 0), (15, 0)]
    assert drag_track_segment(pts, 1, 0, 3) == [
        (0.0, 0.0), (5.0, 3.0), (10.0, 3.0), (15.0, 0.0)]


def test_keep45_segment_drag_slides_joints_along_neighbour_directions():
    # A staircase: diagonal, horizontal, diagonal.  Dragging the middle
    # horizontal run must leave every segment on the 45 grid.
    pts = [(0.0, 0.0), (5.0, 5.0), (15.0, 5.0), (20.0, 10.0)]
    moved = drag_track_segment(pts, 1, 0.0, 3.0, keep45=True)
    assert is_45(moved), moved
    ys = [p[1] for p in moved]
    assert max(ys) >= 8.0 - 1e-9  # the dragged run really did move up
    assert moved[0] == (0.0, 0.0) and moved[-1] == (20.0, 10.0)


def test_keep45_segment_drag_preserves_outer_endpoints_when_anchored():
    pts = [(0.0, 0.0), (10.0, 0.0)]
    moved = drag_track_segment(pts, 0, 0.0, 2.0, anchor_start=True,
                               anchor_end=True, keep45=True)
    assert moved[0] == (0.0, 0.0)
    assert moved[-1] == (10.0, 0.0)
    assert is_45(moved), moved


def test_keep45_falls_back_gracefully_for_degenerate_segments():
    pts = [(0.0, 0.0), (0.0, 0.0)]
    moved = drag_track_segment(pts, 0, 1.0, 1.0, keep45=True)
    assert len(moved) >= 1


def test_corner_drag_moves_one_vertex_and_can_hold_45():
    pts = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]
    plain = drag_track_corner(pts, 1, 0.0, 2.0)
    assert plain[1] == (5.0, 2.0)
    assert plain[0] == (0.0, 0.0) and plain[-1] == (10.0, 0.0)

    held = drag_track_corner(pts, 1, 1.0, 2.0, keep45=True)
    assert is_45(held), held
    assert held[0] == (0.0, 0.0) and held[-1] == (10.0, 0.0)


def test_corner_drag_ignores_out_of_range_index():
    pts = [(0.0, 0.0), (5.0, 0.0)]
    assert drag_track_corner(pts, 9, 1.0, 1.0) == pts
    assert drag_track_corner(pts, -1, 1.0, 1.0) == pts


def test_via_attached_drag_moves_listed_vertices_and_can_hold_45():
    pts = [(0.0, 0.0), (10.0, 0.0)]
    plain = drag_via_attached_points(pts, [1], 2.0, 3.0)
    assert plain[-1] == (12.0, 3.0)

    held = drag_via_attached_points(pts, [1], 2.0, 3.0, keep45=True)
    assert is_45(held), held
    assert held[0] == (0.0, 0.0)
    assert held[-1] == (12.0, 3.0)


def test_via_attached_drag_with_no_indices_is_identity():
    pts = [(0.0, 0.0), (10.0, 0.0)]
    assert drag_via_attached_points(pts, [], 5.0, 5.0) == pts


def test_snap_direction_45_picks_the_nearest_octant():
    assert snap_direction_45(10.0, 0.4) == (1.0, 0.0)
    assert snap_direction_45(0.4, 10.0) == (0.0, 1.0)
    d = snap_direction_45(5.0, 4.8)
    assert abs(d[0] - d[1]) < 1e-9 and d[0] > 0
    assert snap_direction_45(0.0, 0.0) is None


# --- walkaround -----------------------------------------------------------

def test_walkaround_returns_direct_path_when_nothing_blocks():
    out = walkaround_route((0, 0), (10, 10), lambda pts: False)
    assert out and out[-1] == (10.0, 10.0)
    assert is_45([(0.0, 0.0)] + out)


def test_walkaround_finds_a_legal_detour_around_a_blocking_pad():
    order = [1]
    blocker = pad(5.0, 0.0, 1, net="B", w=2.0, h=2.0)
    index = ObstacleIndex([blocker], [], [], order)

    def conflict(points):
        cand = Track(layer_id=1, width=0.25, points=points, net="A")
        return bool(route_conflicts(cand, None, None, None, order,
                                    active_net="A", index=index))

    # The straight line from (0,0) to (10,0) runs through the pad.
    assert conflict([(0.0, 0.0), (10.0, 0.0)])
    out = walkaround_route((0, 0), (10, 0), conflict,
                           obstacle_boxes=[pad_bbox(blocker, 0.25)])
    assert out is not None, "walkaround should find a detour"
    path = [(0.0, 0.0)] + out
    assert not conflict(path)
    assert path[-1] == (10.0, 0.0)
    assert is_45(path), path


def test_walkaround_gives_up_cleanly_when_fully_enclosed():
    # Nothing is ever legal, so the router must report failure rather than
    # returning illegal geometry for the caller to commit.
    assert walkaround_route((0, 0), (10, 0), lambda pts: True,
                            obstacle_boxes=[(4, -1, 6, 1)]) is None


def test_conflict_boxes_cover_each_reported_obstacle_kind():
    t = Track(layer_id=1, width=0.5, points=[(0, 0), (5, 0)], net="B")
    v = Via(x=9, y=0, diameter=1.0, start_layer_id=1, end_layer_id=1, net="C")
    p = pad(20, 0, 1, net="D")
    boxes = conflict_boxes([
        {"kind": "track", "object": t}, {"kind": "via", "object": v},
        {"kind": "pad", "object": p}, {"kind": "outline", "object": None}])
    assert len(boxes) == 3


# --- cached board outline -------------------------------------------------

def test_board_outline_is_cached_per_geometry_and_invalidated_on_change():
    rect = {"kind": "rectangle", "x": 0, "y": 0, "width": 20, "height": 20,
            "layer_id": 1}
    first = board_outline([rect])
    second = board_outline([dict(rect)])
    assert first is second, "identical geometry should hit the cache"

    moved = board_outline([dict(rect, width=30)])
    assert moved is not first, "changed geometry must not reuse a stale loop"


def test_board_outline_reports_containment_and_boundary_distance():
    rect = {"kind": "rectangle", "x": 0, "y": 0, "width": 20, "height": 20,
            "layer_id": 1}
    outline = board_outline([rect])
    assert outline.contains((0.0, 0.0))
    assert not outline.contains((50.0, 0.0))
    # The rectangle spans -10..10, so the centre is 10mm from every edge.
    assert abs(outline.distance_to_point((0.0, 0.0), 100.0) - 10.0) < 1e-6
    assert outline.distance_to_segment((0.0, 0.0), (0.0, 5.0), 100.0) > 4.9


def test_empty_outline_is_falsy_and_unconstraining():
    outline = board_outline([])
    assert not outline
    assert outline.loops == []


# --- ratsnest -------------------------------------------------------------

def test_ratsnest_accepts_a_prebuilt_topology_and_matches_a_fresh_build():
    order = [1]
    pads_by_net = {"N": [pad(0, 0, 1), pad(20, 0, 2)]}
    tracks, vias = [], []
    topo = build_copper_topology(tracks, vias, order)
    a = ratsnest_segments(pads_by_net, tracks, vias, order)
    b = ratsnest_segments(pads_by_net, tracks, vias, order, topology=topo)
    assert len(a) == len(b) == 1


def test_ratsnest_connects_every_group_of_a_multi_pad_net_once():
    order = [1]
    pads_by_net = {"N": [pad(0, 0, 1), pad(20, 0, 2), pad(40, 0, 3),
                         pad(60, 0, 4)]}
    wires = ratsnest_segments(pads_by_net, [], [], order)
    # A spanning tree over four disconnected pads needs exactly three airwires.
    assert len(wires) == 3
    assert all(name == "N" for _a, _b, name in wires)


def test_ratsnest_drops_airwires_once_copper_joins_the_pads():
    order = [1]
    pads_by_net = {"N": [pad(0, 0, 1), pad(20, 0, 2)]}
    joined = Track(layer_id=1, width=0.25, points=[(0, 0), (20, 0)], net="N")
    assert ratsnest_segments(pads_by_net, [joined], [], order) == []


# --- clip -----------------------------------------------------------------

def test_clip_reports_blocked_and_keeps_the_committed_prefix():
    def conflict(points):
        return any(p[0] > 5.0 for p in points)

    clipped, blocked = clip_polyline_before_conflict(
        [(0.0, 0.0), (10.0, 0.0)], conflict, fixed_count=1)
    assert blocked
    assert clipped[0] == (0.0, 0.0)
    assert clipped[-1][0] <= 5.0 + 1e-3
