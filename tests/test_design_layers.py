# -*- coding: utf-8 -*-
"""Tests for the copper stack model.

``elec3d.design`` holds no Qt objects, so these run anywhere the parser runs.

    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elec3d.design import LAYER_PALETTE, LayerStack, OutlineShape       # noqa: E402


class TestOutlineShape(unittest.TestCase):
    def test_snapshot_round_trip(self):
        shape = OutlineShape("abc", "circle", layer_id=3, x=1.0, y=2.0, radius=3.0)
        restored = OutlineShape.restore(shape.snapshot())
        self.assertEqual(restored.uid, "abc")
        self.assertEqual(restored.kind, "circle")
        self.assertEqual(restored.layer_id, 3)
        self.assertEqual(restored.geometry["radius"], 3.0)


class TestLayerStack(unittest.TestCase):
    def test_board_starts_with_one_active_bottom_layer(self):
        stack = LayerStack()
        self.assertEqual(len(stack), 1)
        # A one-layer board is B.Cu: the bottom test wins when index 0 is also
        # the last index, which is what the reference viewer does.
        self.assertEqual(stack.layers[0].name, "B.Cu")
        self.assertEqual(stack.active().id, stack.layers[0].id)

    def test_add_stacks_upwards_and_relabels(self):
        stack = LayerStack()
        stack.add()
        self.assertEqual([l.name for l in stack], ["B.Cu", "F.Cu"])
        stack.add()
        self.assertEqual([l.name for l in stack], ["B.Cu", "In1.Cu", "F.Cu"])
        stack.add()
        self.assertEqual([l.name for l in stack],
                         ["B.Cu", "In1.Cu", "In2.Cu", "F.Cu"])

    def test_added_layer_becomes_active(self):
        stack = LayerStack()
        added = stack.add()
        self.assertEqual(stack.active_id, added.id)

    def test_added_layers_cycle_the_palette(self):
        stack = LayerStack()
        self.assertEqual(stack.layers[0].color, LAYER_PALETTE[0])
        self.assertEqual(stack.add().color, LAYER_PALETTE[1])
        self.assertEqual(stack.add().color, LAYER_PALETTE[2])

    def test_top_down_lists_only_copper_layers(self):
        stack = LayerStack()
        stack.add()
        self.assertEqual([l.name for l in stack.top_down()], ["F.Cu", "B.Cu"])
        self.assertTrue(all(layer.is_copper for layer in stack.top_down()))

    def test_remove_returns_a_fallback_and_relabels(self):
        stack = LayerStack()
        stack.add()
        middle = stack.add()          # B.Cu, In1.Cu, F.Cu -> remove the middle
        stack.add()
        self.assertEqual([l.name for l in stack],
                         ["B.Cu", "In1.Cu", "In2.Cu", "F.Cu"])
        fallback = stack.remove(middle.id)
        self.assertIsNotNone(fallback)
        self.assertTrue(stack.has(fallback))
        self.assertEqual([l.name for l in stack], ["B.Cu", "In1.Cu", "F.Cu"])

    def test_remove_refuses_to_empty_the_board(self):
        stack = LayerStack()
        self.assertIsNone(stack.remove(stack.layers[0].id))
        self.assertEqual(len(stack), 1)

    def test_removing_the_active_layer_moves_active_to_the_fallback(self):
        stack = LayerStack()
        top = stack.add()
        self.assertEqual(stack.active_id, top.id)
        fallback = stack.remove(top.id)
        self.assertEqual(stack.active_id, fallback)

    def test_by_id_falls_back_rather_than_returning_none(self):
        stack = LayerStack()
        # Drawing code holds ids that may already have been removed, so an
        # unknown id resolves to a real layer instead of blowing up.
        self.assertIs(stack.by_id(9999), stack.layers[0])

    def test_rename_survives_relabelling(self):
        stack = LayerStack()
        stack.add()
        self.assertTrue(stack.rename(stack.top().id, "Component"))
        stack.add()
        self.assertEqual([l.name for l in stack], ["B.Cu", "Component", "F.Cu"])

    def test_there_is_no_separate_outline_design_layer(self):
        stack = LayerStack()
        self.assertFalse(hasattr(stack, "outline"))
        self.assertTrue(all(layer.is_copper for layer in stack.all_layers()))
        self.assertFalse(stack.has(0))

    def test_legacy_outline_active_id_migrates_to_top_copper(self):
        stack = LayerStack()
        stack.add()
        data = stack.snapshot()
        data["active_id"] = 0
        data["outline"] = {"id": 0, "name": "Outline", "visible": False}
        restored = LayerStack()
        restored.restore(data)
        self.assertEqual(restored.active_id, restored.top().id)
        self.assertFalse(hasattr(restored, "outline"))

    def test_snapshot_round_trip(self):
        stack = LayerStack()
        stack.add()
        stack.add()
        stack.layers[1].visible = False
        stack.set_active(stack.layers[0].id)
        data = stack.snapshot()

        restored = LayerStack()
        restored.restore(data)
        self.assertEqual([l.name for l in restored], [l.name for l in stack])
        self.assertEqual([l.color for l in restored], [l.color for l in stack])
        self.assertEqual([l.visible for l in restored], [l.visible for l in stack])
        self.assertEqual(restored.active_id, stack.active_id)
        self.assertNotIn("outline", data)
        # A restored stack must keep issuing fresh ids rather than colliding
        # with the ones it just loaded.
        loaded_ids = set(l.id for l in restored)
        self.assertNotIn(restored.add().id, loaded_ids)
        self.assertEqual(len(set(l.id for l in restored)), len(restored))

    def test_single_layer_stackup_defaults(self):
        stack = LayerStack()
        layer = stack.layers[0]
        self.assertFalse(hasattr(stack, "top_margin"))    # removed in 0.17
        self.assertAlmostEqual(stack.copper_thickness(layer.id), 0.035)
        self.assertAlmostEqual(stack.bottom_margin, 1.0)
        # The stack spans from the solid's lowest point to the top plane.
        self.assertAlmostEqual(stack.total_stackup_thickness(), 1.0)

    def test_adding_layer_extends_stackup_without_resetting_existing_values(self):
        stack = LayerStack()
        bottom = stack.bottom()
        stack.set_copper_thickness(bottom.id, 0.070)
        top = stack.add()
        self.assertAlmostEqual(stack.copper_thickness(bottom.id), 0.070)
        self.assertAlmostEqual(stack.copper_thickness(top.id), 0.035)
        self.assertAlmostEqual(stack.separation(bottom.id, top.id), 1.6)
        self.assertAlmostEqual(stack.total_stackup_thickness(), 2.6)


    def test_copper_thickness_does_not_move_reference_planes_or_change_total_height(self):
        stack = LayerStack()
        top = stack.add()
        bottom = stack.bottom()
        separation = stack.separation(bottom.id, top.id)
        total = stack.total_stackup_thickness()
        stack.set_copper_thickness(bottom.id, 0.250)
        stack.set_copper_thickness(top.id, 0.500)
        self.assertAlmostEqual(stack.separation(bottom.id, top.id), separation)
        self.assertAlmostEqual(stack.total_stackup_thickness(), total)

    def test_each_new_gap_has_an_independent_separation(self):
        stack = LayerStack()
        middle = stack.add()
        bottom = stack.bottom()
        stack.set_separation(bottom.id, middle.id, 0.8)
        top = stack.add()
        self.assertAlmostEqual(stack.separation(bottom.id, middle.id), 0.8)
        self.assertAlmostEqual(stack.separation(middle.id, top.id), 1.6)
        stack.set_separation(middle.id, top.id, 2.2)
        self.assertAlmostEqual(stack.separation(bottom.id, middle.id), 0.8)
        self.assertAlmostEqual(stack.separation(middle.id, top.id), 2.2)

    def test_removing_middle_layer_merges_gap_and_preserves_total_height(self):
        stack = LayerStack()
        middle = stack.add()
        top = stack.add()
        bottom = stack.bottom()
        stack.set_separation(bottom.id, middle.id, 0.8)
        stack.set_separation(middle.id, top.id, 1.1)
        stack.set_copper_thickness(middle.id, 0.05)
        before = stack.total_stackup_thickness()
        stack.remove(middle.id)
        self.assertAlmostEqual(stack.separation(bottom.id, top.id), 1.9)
        self.assertAlmostEqual(stack.total_stackup_thickness(), before)

    def test_stackup_snapshot_round_trip(self):
        stack = LayerStack()
        top = stack.add()
        bottom = stack.bottom()
        stack.set_bottom_margin(0.6)
        stack.set_copper_thickness(top.id, 0.018)
        stack.set_copper_thickness(bottom.id, 0.070)
        stack.set_separation(bottom.id, top.id, 0.9)
        data = stack.snapshot()

        restored = LayerStack()
        restored.restore(data)
        self.assertIsNone(restored.legacy_top_margin)
        self.assertAlmostEqual(restored.bottom_margin, 0.6)
        self.assertAlmostEqual(restored.copper_thickness(top.id), 0.018)
        self.assertAlmostEqual(restored.copper_thickness(bottom.id), 0.070)
        self.assertAlmostEqual(restored.separation(bottom.id, top.id), 0.9)
        self.assertAlmostEqual(restored.total_stackup_thickness(), 1.5)

    def test_old_snapshot_without_stackup_gets_defaults(self):
        stack = LayerStack()
        stack.add()
        data = stack.snapshot()
        data.pop("stackup")

        restored = LayerStack()
        restored.restore(data)
        self.assertIsNone(restored.legacy_top_margin)
        self.assertAlmostEqual(restored.bottom_margin, 1.0)
        self.assertTrue(all(abs(restored.copper_thickness(l.id) - 0.035) < 1e-12
                            for l in restored.layers))
        self.assertAlmostEqual(
            restored.separation(restored.layers[0].id, restored.layers[1].id), 1.6)

    def test_restore_ignores_empty_data(self):
        stack = LayerStack()
        stack.add()
        before = [l.name for l in stack]
        stack.restore(None)
        stack.restore({})
        self.assertEqual([l.name for l in stack], before)


if __name__ == "__main__":
    unittest.main()
