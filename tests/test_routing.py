from elec3d.routing import (
    Track, Via, route45, route_snap, via_spans_layer, ratsnest_segments,
    automatic_via_target_layer,
)


def pad(x, y, pin, net="N", layer=1, w=1.0, h=1.0, shape="rect"):
    return {"x": x, "y": y, "pin": str(pin), "ref": "U1", "net": net,
            "layer_id": layer, "w": w, "h": h, "rot": 0.0, "shape": shape}


def test_route45_ends_exactly_at_cursor_and_supports_posture():
    a, b = (0, 0), (8, 3)
    p0 = route45(a, b, 0)
    p1 = route45(a, b, 1)
    assert p0[-1] == b and p1[-1] == b
    assert p0 != p1
    for points in (p0, p1):
        prev = a
        for cur in points:
            dx, dy = abs(cur[0]-prev[0]), abs(cur[1]-prev[1])
            assert dx == 0 or dy == 0 or abs(dx-dy) < 1e-12
            prev = cur


def test_snap_inside_pad_is_exact_pad_center():
    p = pad(2.0, 3.0, 1)
    snap = route_snap((2.4, 3.1), 1, [p], [], [], [1], 0.2, grid=0.1)
    assert snap["kind"] == "pad"
    assert (snap["x"], snap["y"]) == (2.0, 3.0)


def test_snap_inside_via_is_exact_center_and_arbitrary_span():
    v = Via(x=4, y=5, diameter=1.0, start_layer_id=1, end_layer_id=4)
    order = [1, 2, 3, 4]
    assert all(via_spans_layer(v, lid, order) for lid in order)
    snap = route_snap((4.35, 5.0), 3, [], [], [v], order, 0.1, grid=0.1)
    assert snap["kind"] == "via"
    assert (snap["x"], snap["y"]) == (4.0, 5.0)


def test_snap_to_middle_of_track_projects_to_centerline():
    t = Track(layer_id=1, width=0.25, points=[(0, 0), (10, 0)], net="N")
    snap = route_snap((4.25, 0.35), 1, [], [t], [], [1], 0.5, grid=0.1)
    assert snap["kind"] == "track"
    assert abs(snap["x"] - 4.25) < 1e-12
    assert abs(snap["y"]) < 1e-12


def test_shift_style_object_snap_disable_falls_back_to_grid():
    p = pad(2.0, 3.0, 1)
    snap = route_snap((2.03, 3.04), 1, [p], [], [], [1], 0.5,
                      grid=0.1, object_snap=False, grid_snap=True)
    assert snap["kind"] == "grid"
    assert (snap["x"], snap["y"]) == (2.0, 3.0)


def test_ctrl_style_grid_disable_allows_free_point_when_no_object():
    snap = route_snap((2.037, 3.049), 1, [], [], [], [1], 0.5,
                      grid=0.1, object_snap=True, grid_snap=False)
    assert snap["kind"] == "free"
    assert (snap["x"], snap["y"]) == (2.037, 3.049)


def test_known_active_net_does_not_snap_to_different_known_net():
    wrong = pad(1.0, 1.0, 1, net="OTHER")
    snap = route_snap((1.0, 1.0), 1, [wrong], [], [], [1], 0.5,
                      grid=0.1, active_net="N")
    assert snap["kind"] == "grid"


def test_via_default_hole_is_zero_and_snapshot_roundtrips():
    v = Via(x=1, y=2, start_layer_id=2, end_layer_id=5)
    assert v.hole_diameter == 0.0
    restored = Via.restore(v.snapshot())
    assert restored.hole_diameter == 0.0
    assert (restored.start_layer_id, restored.end_layer_id) == (2, 5)


def test_incomplete_track_moves_ratsnest_anchor_to_free_copper_end():
    pads = {"N": [pad(0, 0, 1), pad(10, 0, 2)]}
    t = Track(layer_id=1, width=0.25, points=[(0, 0), (6, 0)], net="N")
    segs = ratsnest_segments(pads, [t], [], [1])
    assert len(segs) == 1
    a, b, name = segs[0]
    assert name == "N"
    xs = sorted([round(a["x"], 6), round(b["x"], 6)])
    assert xs == [6.0, 10.0]


def test_completed_track_removes_ratsnest_for_net():
    pads = {"N": [pad(0, 0, 1), pad(10, 0, 2)]}
    t = Track(layer_id=1, width=0.25, points=[(0, 0), (10, 0)], net="N")
    assert ratsnest_segments(pads, [t], [], [1]) == []


def test_via_connects_same_net_across_nonadjacent_layers():
    pads = {"N": [pad(0, 0, 1, layer=1), pad(0, 0, 2, layer=4)]}
    v = Via(x=0, y=0, diameter=0.8, hole_diameter=0.0,
            start_layer_id=1, end_layer_id=4, net="N")
    assert ratsnest_segments(pads, [], [v], [1, 2, 3, 4]) == []


def test_named_pad_propagates_net_to_complete_track_via_island():
    from elec3d.routing import assign_connection_nets

    pads = {"GND": [pad(0, 0, 1, net="GND", layer=1)]}
    t1 = Track(layer_id=1, width=0.25, points=[(0, 0), (5, 0)], net=None)
    v = Via(x=5, y=0, diameter=0.8, start_layer_id=1, end_layer_id=3, net=None)
    t2 = Track(layer_id=3, width=0.25, points=[(5, 0), (8, 0)], net=None)
    changed = assign_connection_nets(pads, [t1, t2], [v], [1, 2, 3])
    assert changed == 3
    assert (t1.net, v.net, t2.net) == ("GND", "GND", "GND")


def test_ambiguous_short_does_not_overwrite_connection_net_metadata():
    from elec3d.routing import assign_connection_nets

    pads = {
        "GND": [pad(0, 0, 1, net="GND", layer=1)],
        "VCC": [pad(10, 0, 2, net="VCC", layer=1)],
    }
    t = Track(layer_id=1, width=0.25, points=[(0, 0), (10, 0)], net=None)
    changed = assign_connection_nets(pads, [t], [], [1])
    assert changed == 0
    assert t.net is None


def test_auto_via_target_follows_nearest_unresolved_ratsnest_pad_layer():
    pads = {"N": [
        pad(0, 0, 1, layer=1),
        pad(20, 0, 2, layer=4),
        pad(5, 0, 3, layer=3),
    ]}
    target = automatic_via_target_layer(
        (1, 0), 1, "N", {"U1\x1f1"}, pads, [1, 2, 3, 4])
    assert target == 3


def test_auto_via_target_ignores_same_layer_and_connected_targets():
    pads = {"N": [
        pad(1, 0, 1, layer=1),
        pad(2, 0, 2, layer=2),
        pad(6, 0, 3, layer=4),
    ]}
    target = automatic_via_target_layer(
        (0, 0), 1, "N", {"U1\x1f2"}, pads, [1, 2, 3, 4])
    assert target == 4
    assert automatic_via_target_layer((0, 0), 1, None, set(), pads, [1, 2, 3, 4]) is None


def test_route_conflict_blocks_crossing_different_net_track_but_allows_same_net():
    from elec3d.routing import route_conflicts

    candidate = Track(layer_id=1, width=0.25, points=[(0, 0), (10, 0)], net="SIG")
    wrong = Track(layer_id=1, width=0.25, points=[(5, -3), (5, 3)], net="VCC")
    same = Track(layer_id=1, width=0.25, points=[(7, -3), (7, 3)], net="SIG")
    hits = route_conflicts(candidate, [], [wrong, same], [], [1], active_net="SIG")
    assert [(h["kind"], h["net"]) for h in hits] == [("track", "VCC")]


def test_route_conflict_uses_track_width_not_only_centerline_intersection():
    from elec3d.routing import route_conflicts

    candidate = Track(layer_id=1, width=0.50, points=[(0, 0), (10, 0)], net="SIG")
    # Parallel centre lines do not intersect, but 0.35 mm spacing is inside the
    # two 0.25 mm half-widths plus the small numerical/contact tolerance.
    wrong = Track(layer_id=1, width=0.50, points=[(0, 0.35), (10, 0.35)], net="GND")
    assert route_conflicts(candidate, [], [wrong], [], [1], active_net="SIG")


def test_route_conflict_blocks_different_net_pad_and_via_on_active_layer():
    from elec3d.routing import route_conflicts

    candidate = Track(layer_id=2, width=0.25, points=[(0, 0), (10, 0)], net="SIG")
    wrong_pad = pad(4, 0, 2, net="VCC", layer=2, w=1.0, h=1.0)
    wrong_via = Via(x=7, y=0, diameter=0.8, start_layer_id=1, end_layer_id=3, net="GND")
    hits = route_conflicts(candidate, [wrong_pad], [], [wrong_via], [1, 2, 3], active_net="SIG")
    assert {h["kind"] for h in hits} == {"pad", "via"}
    assert {h["net"] for h in hits} == {"VCC", "GND"}


def test_via_conflict_checks_all_layers_spanned_by_candidate():
    from elec3d.routing import via_conflicts

    candidate = Via(x=0, y=0, diameter=0.8, start_layer_id=1, end_layer_id=3, net="SIG")
    wrong_mid = Track(layer_id=2, width=0.25, points=[(-2, 0), (2, 0)], net="VCC")
    same_mid = Track(layer_id=2, width=0.25, points=[(-2, 0.1), (2, 0.1)], net="SIG")
    hits = via_conflicts(candidate, [], [wrong_mid, same_mid], [], [1, 2, 3], active_net="SIG")
    assert [(h["kind"], h["net"]) for h in hits] == [("track", "VCC")]


def test_route_conflict_is_not_enforced_until_active_net_is_known():
    from elec3d.routing import route_conflicts

    candidate = Track(layer_id=1, width=0.25, points=[(0, 0), (10, 0)], net=None)
    known = Track(layer_id=1, width=0.25, points=[(5, -3), (5, 3)], net="VCC")
    assert route_conflicts(candidate, [], [known], [], [1], active_net=None) == []


def test_clip_polyline_stops_at_first_different_net_clearance():
    from elec3d.routing import clip_polyline_before_conflict, route_conflicts

    wrong = Track(layer_id=1, width=0.25, points=[(5, -3), (5, 3)], net="VCC")
    def blocked(points):
        candidate = Track(layer_id=1, width=0.25, points=points, net="SIG")
        return bool(route_conflicts(candidate, [], [wrong], [], [1], active_net="SIG"))

    clipped, hit = clip_polyline_before_conflict([(0, 0), (10, 0)], blocked)
    assert hit is True
    assert len(clipped) == 2
    # 0.125 + 0.125 + 0.02 mm clearance from the x=5 obstacle centreline.
    assert 4.70 < clipped[-1][0] < 4.74
    assert abs(clipped[-1][1]) < 1e-9


def test_clip_polyline_preserves_committed_vertices_and_clips_only_moving_tail():
    from elec3d.routing import clip_polyline_before_conflict, route_conflicts

    wrong = Track(layer_id=1, width=0.25, points=[(8, -3), (8, 3)], net="VCC")
    def blocked(points):
        candidate = Track(layer_id=1, width=0.25, points=points, net="SIG")
        return bool(route_conflicts(candidate, [], [wrong], [], [1], active_net="SIG"))

    pts = [(0, 0), (4, 0), (6, 0), (10, 0)]
    clipped, hit = clip_polyline_before_conflict(pts, blocked, fixed_count=2)
    assert hit is True
    assert clipped[:2] == [(0.0, 0.0), (4.0, 0.0)]
    assert clipped[2] == (6.0, 0.0)
    assert 7.70 < clipped[-1][0] < 7.74


def test_route_outline_blocks_track_envelope_outside_rectangle():
    from elec3d.routing import route_outline_conflicts

    outline = [{"kind": "rectangle", "x": 0, "y": 0, "width": 10, "height": 8}]
    legal = Track(layer_id=1, width=0.25, points=[(0, 0), (4.8, 0)], net="SIG")
    outside = Track(layer_id=1, width=0.25, points=[(0, 0), (5.0, 0)], net="SIG")
    assert route_outline_conflicts(legal, outline) == []
    hits = route_outline_conflicts(outside, outline)
    assert [h["kind"] for h in hits] == ["outline"]


def test_route_outline_clips_at_board_edge_even_without_known_net():
    from elec3d.routing import clip_polyline_before_conflict, route_outline_conflicts

    outline = [{"kind": "rectangle", "x": 0, "y": 0, "width": 10, "height": 8}]
    def blocked(points):
        candidate = Track(layer_id=1, width=0.25, points=points, net=None)
        return bool(route_outline_conflicts(candidate, outline))

    clipped, hit = clip_polyline_before_conflict([(0, 0), (8, 0)], blocked)
    assert hit is True
    assert len(clipped) == 2
    # 5 mm half-width minus 0.125 mm copper radius and 0.02 mm margin.
    assert 4.84 < clipped[-1][0] < 4.87
    assert abs(clipped[-1][1]) < 1e-9


def test_open_outline_line_does_not_constrain_routing():
    from elec3d.routing import route_outline_conflicts

    open_outline = [{"kind": "line", "x1": -5, "y1": -5, "x2": 5, "y2": -5}]
    candidate = Track(layer_id=1, width=0.25, points=[(0, 0), (50, 0)], net="SIG")
    assert route_outline_conflicts(candidate, open_outline) == []


def test_closed_line_chain_constrains_routing_like_board_polygon():
    from elec3d.routing import route_outline_conflicts

    outline = [
        {"kind": "line", "x1": -5, "y1": -4, "x2": 5, "y2": -4},
        {"kind": "line", "x1": 5, "y1": -4, "x2": 5, "y2": 4},
        {"kind": "line", "x1": 5, "y1": 4, "x2": -5, "y2": 4},
        {"kind": "line", "x1": -5, "y1": 4, "x2": -5, "y2": -4},
    ]
    candidate = Track(layer_id=1, width=0.25, points=[(0, 0), (6, 0)], net="SIG")
    assert route_outline_conflicts(candidate, outline)


def test_via_must_fit_completely_inside_closed_outline():
    from elec3d.routing import via_outline_conflicts

    outline = [{"kind": "circle", "x": 0, "y": 0, "radius": 5}]
    legal = Via(x=4.4, y=0, diameter=0.8, start_layer_id=1, end_layer_id=2)
    blocked = Via(x=4.7, y=0, diameter=0.8, start_layer_id=1, end_layer_id=2)
    assert via_outline_conflicts(legal, outline) == []
    assert via_outline_conflicts(blocked, outline)


def test_via_layer_outlines_are_checked_independently():
    from elec3d.routing import via_layer_outline_conflicts

    # Two copper planes may intentionally use the same board boundary.  They
    # are separate cross-sections, not nested loops in one even/odd polygon.
    top = [{"kind": "circle", "x": 0, "y": 0, "radius": 5}]
    bottom = [{"kind": "circle", "x": 0, "y": 0, "radius": 5}]
    legal = Via(x=0, y=0, diameter=0.8, start_layer_id=1, end_layer_id=2)
    assert via_layer_outline_conflicts(legal, [top, bottom]) == []

    too_close = Via(x=4.7, y=0, diameter=0.8, start_layer_id=1, end_layer_id=2)
    assert via_layer_outline_conflicts(too_close, [top, bottom])


def test_drag_track_segment_stretches_neighbors_and_preserves_anchored_endpoints():
    from elec3d.routing import drag_track_segment
    pts = [(0, 0), (10, 0)]
    assert drag_track_segment(pts, 0, 0, 2, True, True) == [
        (0.0, 0.0), (0.0, 2.0), (10.0, 2.0), (10.0, 0.0)
    ]

    pts = [(0, 0), (5, 0), (10, 0), (15, 0)]
    moved = drag_track_segment(pts, 1, 0, 3)
    assert moved == [(0.0, 0.0), (5.0, 3.0), (10.0, 3.0), (15.0, 0.0)]
