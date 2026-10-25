# -*- coding: utf-8 -*-
"""Pure-Python coverage for v0.13 design-rule clearances."""

from elec3d.drc import DesignRules, DEFAULT_RULES
from elec3d.routing import Track, Via, route_conflicts, via_conflicts


def test_design_rules_defaults_and_round_trip():
    rules = DesignRules()
    assert rules.snapshot() == DEFAULT_RULES
    assert rules.set("track_track", 0.42)
    restored = DesignRules(rules.snapshot())
    assert abs(restored.get("track_track") - 0.42) < 1e-12


def test_track_to_track_clearance_is_rule_driven():
    candidate = Track(layer_id=1, width=0.2, points=[(0, 0), (10, 0)], net="A")
    obstacle = Track(layer_id=1, width=0.2, points=[(0, 0.35), (10, 0.35)], net="B")
    # Edge spacing is 0.15 mm.  A 0.20-mm rule blocks it, a 0.10-mm rule allows it.
    assert route_conflicts(candidate, [], [obstacle], [], [1], active_net="A",
                           rules={"track_track": 0.20})
    assert not route_conflicts(candidate, [], [obstacle], [], [1], active_net="A",
                               rules={"track_track": 0.10})


def test_track_to_pad_clearance_is_rule_driven():
    candidate = Track(layer_id=1, width=0.2, points=[(-2, 0.8), (2, 0.8)], net="A")
    pad = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "rot": 0.0,
           "shape": "rect", "layer_id": 1, "net": "B"}
    # Track edge is 0.20 mm from the pad edge.
    assert route_conflicts(candidate, [pad], [], [], [1], active_net="A",
                           rules={"track_pad": 0.25})
    assert not route_conflicts(candidate, [pad], [], [], [1], active_net="A",
                               rules={"track_pad": 0.15})


def test_via_to_via_clearance_is_rule_driven():
    candidate = Via(x=0, y=0, diameter=0.8, start_layer_id=1, end_layer_id=2, net="A")
    obstacle = Via(x=1.0, y=0, diameter=0.8, start_layer_id=1, end_layer_id=2, net="B")
    # Edge spacing is 0.20 mm.
    assert via_conflicts(candidate, [], [], [obstacle], [1, 2], active_net="A",
                         rules={"via_via": 0.25})
    assert not via_conflicts(candidate, [], [], [obstacle], [1, 2], active_net="A",
                             rules={"via_via": 0.15})
