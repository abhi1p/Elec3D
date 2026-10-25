# -*- coding: utf-8 -*-
"""Live-panel tests for the v0.15 routing and interactive-drag work.

These drive a real ``Viewer2D`` rather than asserting on source text, so they
catch wiring mistakes (a cache that is never invalidated, an index built from
the wrong pad set) that a source-grep test cannot see.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d.footprint import parse_footprint_file            # noqa: E402
from elec3d.routing import Track, Via                        # noqa: E402

try:
    from elec3d.qt import Qt, QtCore, QtWidgets
    from elec3d.panel2d import Viewer2D
    HAVE_QT = True
except ImportError:                                           # pragma: no cover
    HAVE_QT = False

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SAMPLE = os.path.join(DATA, "NE555DR.kicad_mod")

_app = None


def setUpModule():
    global _app
    if HAVE_QT:
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def is_45(points):
    for i in range(1, len(points)):
        dx = abs(points[i][0] - points[i - 1][0])
        dy = abs(points[i][1] - points[i - 1][1])
        if dx <= 1e-9 and dy <= 1e-9:
            continue
        if dx > 1e-9 and dy > 1e-9 and abs(dx - dy) > 1e-6:
            return False
    return True


@unittest.skipUnless(HAVE_QT, "no PySide binding available")
class RoutingPanelBase(unittest.TestCase):
    def setUp(self):
        self.viewer = Viewer2D()
        self.viewer.resize(1000, 700)
        self.viewer.add_footprint(parse_footprint_file(SAMPLE))
        self.viewer.show()
        _app.processEvents()
        self.layer = self.viewer.stack.layers[0].id

    def tearDown(self):
        self.viewer.close()

    def add_track(self, points, net="N", width=0.25, layer=None):
        t = Track(layer_id=layer if layer is not None else self.layer,
                  width=width, points=points, net=net)
        self.viewer.tracks.append(t)
        self.viewer._apply_routed_copper()
        return t

    def add_via(self, x, y, net="N", diameter=0.8):
        v = Via(x=x, y=y, diameter=diameter, start_layer_id=self.layer,
                end_layer_id=self.viewer.stack.layers[-1].id, net=net)
        self.viewer.vias.append(v)
        self.viewer._apply_routed_copper()
        return v

    def item_for(self, model):
        for item in self.viewer.canvas.track_items + self.viewer.canvas.via_items:
            owned = getattr(item, "track_model", None) or getattr(item, "via_model", None)
            if owned is model:
                return item
        raise AssertionError("no canvas item for model")


class TestRoutingCache(RoutingPanelBase):
    def test_cache_is_closed_by_default_so_stale_data_cannot_leak(self):
        self.assertIsNone(self.viewer._routing_cache)

    def test_pads_are_recomputed_after_a_footprint_moves(self):
        first = self.viewer._routing_pads(include_hidden=True)
        self.assertTrue(first, "sample footprint should expose copper pads")
        before_x = first[0]["x"]

        self.viewer._begin_routing_cache()
        try:
            self.viewer._routing_pads(include_hidden=True)   # populate cache
            # A footprint move must invalidate the cached pad geometry, or DRC
            # would silently keep testing against the old positions.
            entry = self.viewer._entries[0]
            entry["item"].setPos(QtCore.QPointF(25.0, 25.0))
            self.viewer._invalidate_routing_cache()
            after = self.viewer._routing_pads(include_hidden=True)
        finally:
            self.viewer._end_routing_cache()
        self.assertNotAlmostEqual(after[0]["x"], before_x, places=3)

    def test_cached_pads_match_uncached_pads(self):
        plain = self.viewer._routing_pads(include_hidden=True)
        self.viewer._begin_routing_cache()
        try:
            cached = self.viewer._routing_pads(include_hidden=True)
            again = self.viewer._routing_pads(include_hidden=True)
        finally:
            self.viewer._end_routing_cache()
        self.assertEqual(len(plain), len(cached))
        self.assertIs(cached, again, "second call should reuse the cached list")

    def test_obstacle_index_excludes_the_copper_being_dragged(self):
        track = self.add_track([(0.0, 0.0), (10.0, 0.0)])
        index = self.viewer._obstacle_index(exclude_uids={track.uid})
        self.assertNotIn(track, index.tracks)
        full = self.viewer._obstacle_index()
        self.assertIn(track, full.tracks)

    def test_snap_index_uses_visible_pads_while_drc_index_includes_hidden(self):
        # The two indexes exist precisely because their pad sets differ.
        visible = self.viewer._routing_pads(include_hidden=False)
        hidden = self.viewer._routing_pads(include_hidden=True)
        self.viewer.stack.layers[0].visible = False
        self.viewer._apply_design_layers(visibility_changed=True)
        self.assertLessEqual(len(self.viewer._routing_pads(include_hidden=False)),
                             len(visible))
        self.assertEqual(len(self.viewer._routing_pads(include_hidden=True)),
                         len(hidden))


class TestInteractiveDrag(RoutingPanelBase):
    def begin_drag(self, model, at, modifiers=None):
        item = self.item_for(model)
        if modifiers is None:
            modifiers = Qt.NoModifier
        return self.viewer._begin_copper_drag(item, QtCore.QPointF(*at),
                                              modifiers)

    def test_grabbing_a_vertex_starts_a_corner_drag(self):
        track = self.add_track([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)])
        self.assertTrue(self.begin_drag(track, (5.0, 0.0)))
        try:
            self.assertEqual(self.viewer._copper_drag_session["mode"],
                             "track_corner")
            self.assertEqual(self.viewer._copper_drag_session["corner_index"], 1)
        finally:
            self.viewer.cancel_copper_drag()

    def test_grabbing_mid_span_starts_a_segment_drag(self):
        track = self.add_track([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)])
        self.assertTrue(self.begin_drag(track, (2.5, 0.0)))
        try:
            self.assertEqual(self.viewer._copper_drag_session["mode"],
                             "track_segment")
        finally:
            self.viewer.cancel_copper_drag()

    def test_segment_drag_keeps_45_degree_geometry_by_default(self):
        track = self.add_track([(0.0, 0.0), (5.0, 5.0), (15.0, 5.0),
                                (20.0, 10.0)])
        self.assertTrue(self.begin_drag(track, (10.0, 5.0)))
        try:
            self.viewer._apply_copper_drag_target(QtCore.QPointF(10.0, 8.0))
            self.assertTrue(is_45(track.points), track.points)
        finally:
            self.viewer.cancel_copper_drag()

    def test_alt_frees_the_angle_during_a_drag(self):
        self.assertTrue(self.viewer._keep45_enabled(Qt.NoModifier))
        self.assertFalse(self.viewer._keep45_enabled(Qt.AltModifier))

    def test_cancel_restores_the_exact_mouse_down_geometry(self):
        points = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]
        track = self.add_track(list(points))
        self.assertTrue(self.begin_drag(track, (2.5, 0.0)))
        self.viewer._apply_copper_drag_target(QtCore.QPointF(2.5, 4.0))
        self.viewer.cancel_copper_drag()
        self.assertEqual([tuple(p) for p in track.points], points)

    def test_cancel_closes_the_routing_cache(self):
        track = self.add_track([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)])
        self.begin_drag(track, (2.5, 0.0))
        self.assertIsNotNone(self.viewer._routing_cache)
        self.viewer.cancel_copper_drag()
        self.assertIsNone(self.viewer._routing_cache)

    def test_finishing_a_drag_closes_the_routing_cache(self):
        track = self.add_track([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)])
        self.begin_drag(track, (2.5, 0.0))
        self.viewer._finish_copper_drag(QtCore.QPointF(2.5, 2.0))
        self.assertIsNone(self.viewer._routing_cache)

    def test_dragging_a_via_carries_connected_track_ends(self):
        via = self.add_via(10.0, 0.0)
        track = self.add_track([(0.0, 0.0), (10.0, 0.0)])
        self.assertTrue(self.begin_drag(via, (10.0, 0.0)))
        try:
            self.viewer._apply_copper_drag_target(QtCore.QPointF(12.0, 3.0))
            # The via moved, and the track end went with it.
            self.assertAlmostEqual(via.x, 12.0, places=6)
            self.assertAlmostEqual(via.y, 3.0, places=6)
            self.assertAlmostEqual(track.points[-1][0], 12.0, places=6)
            self.assertAlmostEqual(track.points[-1][1], 3.0, places=6)
            self.assertTrue(is_45(track.points), track.points)
        finally:
            self.viewer.cancel_copper_drag()

    def test_drag_is_rejected_while_a_route_is_in_progress(self):
        track = self.add_track([(0.0, 0.0), (10.0, 0.0)])
        self.viewer.routing = {"layer_id": self.layer, "width": 0.25,
                               "points": [(0.0, 0.0)], "cursor": (0.0, 0.0),
                               "free": False, "net": None, "connected": set(),
                               "snap": {}}
        try:
            self.assertFalse(self.begin_drag(track, (5.0, 0.0)))
        finally:
            self.viewer.routing = None


class TestRatsnestThrottle(RoutingPanelBase):
    def test_throttle_runs_on_the_leading_edge_then_coalesces(self):
        calls = []
        self.viewer._refresh_ratsnest = lambda **kw: calls.append(kw)
        self.viewer._ratsnest_last_refresh = 0.0

        self.viewer._refresh_ratsnest_throttled()
        self.assertEqual(len(calls), 1, "first update should run immediately")

        for _ in range(20):
            self.viewer._refresh_ratsnest_throttled()
        self.assertEqual(len(calls), 1, "rapid updates should be coalesced")

    def test_throttle_schedules_a_trailing_update_so_nothing_is_lost(self):
        calls = []
        self.viewer._refresh_ratsnest = lambda **kw: calls.append(kw)
        self.viewer._ratsnest_last_refresh = 0.0
        self.viewer._refresh_ratsnest_throttled()
        self.viewer._refresh_ratsnest_throttled()

        timer = getattr(self.viewer, "_ratsnest_timer", None)
        self.assertIsNotNone(timer)
        self.assertTrue(timer.isActive(),
                        "a trailing refresh must be scheduled")


class TestWalkaroundPreview(RoutingPanelBase):
    def start_route_at(self, x, y, net="N"):
        self.viewer.routing = {
            "layer_id": self.layer, "width": 0.25,
            "points": [(x, y)], "cursor": (x, y), "free": False,
            "net": net, "connected": set(), "snap": {},
        }

    def test_clear_path_is_returned_directly(self):
        self.start_route_at(0.0, 0.0)
        self.viewer.routing["cursor"] = (5.0, 0.0)
        preview, conflicts, blocked = self.viewer._legal_route_preview()
        self.assertFalse(blocked)
        self.assertEqual(conflicts, [])
        self.assertEqual(preview[-1], (5.0, 0.0))

    def test_route_goes_around_a_blocking_different_net_track(self):
        # A wall of foreign copper straight across the direct path.
        self.add_track([(10.0, -6.0), (10.0, 6.0)], net="OTHER")
        self.start_route_at(0.0, 0.0, net="N")
        self.viewer.routing["cursor"] = (20.0, 0.0)

        preview, _conflicts, blocked = self.viewer._legal_route_preview()
        self.assertFalse(blocked,
                         "walkaround should reach the cursor, not stop short")
        # A detour actually reached the cursor rather than stopping short.
        self.assertEqual(preview[-1], (20.0, 0.0))
        self.assertGreater(len(preview), 2, "a detour needs extra vertices")
        self.assertTrue(is_45(preview), preview)
        # And the result it produced is genuinely legal.
        self.assertEqual(self.viewer._route_collision_conflicts(preview), [])

    def test_disabling_walkaround_falls_back_to_the_hard_barrier(self):
        self.add_track([(10.0, -6.0), (10.0, 6.0)], net="OTHER")
        self.viewer.route_walkaround = False
        self.start_route_at(0.0, 0.0, net="N")
        self.viewer.routing["cursor"] = (20.0, 0.0)
        preview, conflicts, blocked = self.viewer._legal_route_preview()
        self.assertTrue(blocked)
        self.assertTrue(conflicts)
        self.assertLess(preview[-1][0], 20.0,
                        "clipped route must stop before the obstacle")


class TestRoutingPreferences(RoutingPanelBase):
    def test_defaults_match_kicad_behaviour(self):
        self.assertTrue(self.viewer.drag_keep_45)
        self.assertTrue(self.viewer.route_walkaround)

    def test_preferences_are_applied_to_the_live_panel(self):
        class Opts(object):
            sidebar_order = list(self.viewer._viewer_options.sidebar_order)
            sidebar_expanded = dict(self.viewer._viewer_options.sidebar_expanded)
            crosshair_mode = "small"
            crosshair_size_px = 40
            drag_keep_45 = False
            route_walkaround = False

        import elec3d
        original = elec3d.get_options
        elec3d.get_options = lambda: Opts()
        try:
            self.viewer.apply_ui_preferences()
        finally:
            elec3d.get_options = original
        self.assertFalse(self.viewer.drag_keep_45)
        self.assertFalse(self.viewer.route_walkaround)

    def test_options_model_exposes_and_normalises_the_new_flags(self):
        from elec3d.options import ImportOptions
        self.assertTrue(ImportOptions().drag_keep_45)
        self.assertTrue(ImportOptions().route_walkaround)
        self.assertIs(ImportOptions(drag_keep_45=0).drag_keep_45, False)
        self.assertIs(ImportOptions(route_walkaround=1).route_walkaround, True)


if __name__ == "__main__":
    unittest.main()
